"""Memory retriever — pgvector cosine + salience + Sonnet 2-step + Redis cache.

Pipeline (M1.B, agent_user namespace only):

    user input
       │
       ├── Redis cache lookup (key=memory:recall:{user}:{agent}:{session}:{input_hash})
       │       └── HIT → return cached UUIDs (no DB, no LLM)
       │
       └── MISS
              ├── pgvector cosine search top-K candidates (P0: SCOPED to user_id)
              ├── salience scoring: 0.7 * cosine + 0.3 * log(1 + reinforcement_count)
              ├── UuidIntMapper renders [0]..[K-1] for the LLM
              ├── Sonnet 2-step filter chooses top-N (cheap aux model)
              ├── increment reinforcement_count + last_recalled_at on selected rows
              └── persist {selected UUIDs} to Redis (5min TTL)

Isolation rule (P0): every query MUST be filtered by user_id. Cross-user
leakage is the worst possible failure mode of a memory system. Tests
prove this (test_p0_user_isolation).

Cost shape:
- Cache hit: ~1ms total
- Cache miss: 1 DB query + 1 sonnet call + 1 cached embedding lookup
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Optional
from uuid import UUID

from app.services.memory import (
    ExtractedFrom,
    MemoryRecord,
    MemoryScope,
)
from app.services.memory.uuid_int_mapper import UuidIntMapper

logger = logging.getLogger(__name__)


DEFAULT_TOP_K_CANDIDATES = 10
DEFAULT_TOP_N_FINAL = 5
DEFAULT_CACHE_TTL_S = 300  # 5 minutes (plan-eng-review Issue 1.1)

_COSINE_WEIGHT = 0.7
_SALIENCE_WEIGHT = 0.3


# Sonnet filter prompt template. Caller passes through to a cheap LLM.
_RANKING_PROMPT = """You will see {n} candidate memories that might be relevant to a user's query. Pick the {target} most useful ones.

USER QUERY:
{query}

CANDIDATES:
{candidates}

Reply with ONLY the bracketed numbers of memories to keep, in order of usefulness.
Example: "[2], [0], [4]"
If NONE are relevant, reply: "NONE"."""


SonnetCall = Callable[[str], Awaitable[str]]
EmbeddingCall = Callable[[str], Awaitable[Optional[list[float]]]]


@dataclass
class MemoryRetriever:
    """Retrieve relevant memories for a chat turn.

    All I/O dependencies are injected so the class is unit-testable.
    """

    supabase_client: Any            # async client
    embedding_call: EmbeddingCall   # text -> Optional[vector]
    sonnet_call: SonnetCall         # prompt -> ranking text
    redis_client: Optional[Any]     # async redis or None (cache disabled)

    top_k: int = DEFAULT_TOP_K_CANDIDATES
    top_n: int = DEFAULT_TOP_N_FINAL
    cache_ttl_s: int = DEFAULT_CACHE_TTL_S

    async def recall(
        self,
        *,
        user_id: UUID,
        agent_id: UUID,
        session_id: Optional[UUID],
        user_query: str,
    ) -> list[MemoryRecord]:
        """Return up to ``top_n`` memory records ranked by relevance.

        Empty list if no memories exist or all were filtered out. Never
        raises — every failure mode degrades to "no recall this turn".
        """
        cache_key = self._build_cache_key(
            user_id=user_id,
            agent_id=agent_id,
            session_id=session_id,
            user_query=user_query,
        )

        cached_ids = await self._cache_get(cache_key)
        if cached_ids is not None:
            logger.debug("[memory.retriever] cache hit for key=%s", cache_key[:50])
            records = await self._fetch_by_ids(cached_ids, user_id=user_id)
            await self._reinforce(records)
            return records

        embedding = await self._safe_embed(user_query)
        if embedding is None:
            return []

        candidates = await self._cosine_top_k(
            user_id=user_id, agent_id=agent_id, embedding=embedding
        )
        if not candidates:
            return []

        ranked = self._apply_salience(candidates)
        final = await self._sonnet_filter(ranked, user_query=user_query)
        if not final:
            await self._cache_set(cache_key, [])
            return []

        await self._reinforce(final)
        await self._cache_set(cache_key, [r.id for r in final])
        return final

    # ------------------------------------------------------------------
    # pgvector + salience
    # ------------------------------------------------------------------

    async def _cosine_top_k(
        self,
        *,
        user_id: UUID,
        agent_id: UUID,
        embedding: list[float],
    ) -> list[tuple[MemoryRecord, float]]:
        """Returns [(record, cosine_sim)] for top_k candidates.

        ★ P0: ALWAYS filter by user_id. The hard-coded WHERE clause is
        what makes cross-user leakage structurally impossible.
        """
        try:
            # Supabase RPC for pgvector cosine — assumes the SQL function
            # ``recall_agent_memories(p_user_id, p_agent_id, p_query, p_limit)``
            # is created server-side. M1.B Day 1 migration adds it.
            result = await self.supabase_client.rpc(
                "recall_agent_memories",
                {
                    "p_user_id": str(user_id),
                    "p_agent_id": str(agent_id),
                    "p_query_embedding": embedding,
                    "p_limit": self.top_k,
                    "p_scope": MemoryScope.AGENT_USER.value,
                },
            ).execute()
        except Exception:  # noqa: BLE001
            logger.exception("[memory.retriever] cosine RPC failed; returning []")
            return []

        rows = result.data or []
        out: list[tuple[MemoryRecord, float]] = []
        for row in rows:
            try:
                rec = _row_to_record(row)
            except (KeyError, ValueError):
                continue
            cosine = float(row.get("cosine_similarity") or 0.0)
            out.append((rec, cosine))
        return out

    def _apply_salience(
        self, candidates: list[tuple[MemoryRecord, float]]
    ) -> list[tuple[MemoryRecord, float]]:
        """Re-score candidates: 0.7*cosine + 0.3*log(1+reinforcement_count)."""
        scored = [
            (rec, _COSINE_WEIGHT * cosine
                  + _SALIENCE_WEIGHT * math.log(1 + max(0, rec.reinforcement_count)))
            for rec, cosine in candidates
        ]
        scored.sort(key=lambda t: t[1], reverse=True)
        return scored

    # ------------------------------------------------------------------
    # Sonnet 2-step filter
    # ------------------------------------------------------------------

    async def _sonnet_filter(
        self,
        candidates: list[tuple[MemoryRecord, float]],
        *,
        user_query: str,
    ) -> list[MemoryRecord]:
        if not candidates:
            return []

        records = [rec for rec, _ in candidates]
        mapper = UuidIntMapper.from_records(records)
        rendered = "\n".join(
            f"[{i}] (when_to_use: {rec.when_to_use}) — {rec.summary}"
            for i, rec in enumerate(records)
        )
        prompt = (
            _RANKING_PROMPT
            .replace("{n}", str(len(records)))
            .replace("{target}", str(self.top_n))
            .replace("{query}", user_query[:500])
            .replace("{candidates}", rendered)
        )

        try:
            decision = await self.sonnet_call(prompt)
        except Exception:  # noqa: BLE001
            logger.exception(
                "[memory.retriever] sonnet filter failed; falling back to top-N salience"
            )
            return records[: self.top_n]

        if decision.strip().upper() == "NONE":
            return []

        selected_ids = mapper.resolve_int_refs(decision)
        if not selected_ids:
            # Sonnet returned garbled output; fall back to top-N by salience.
            return records[: self.top_n]
        # Preserve order returned by Sonnet, dedupe.
        by_id = {r.id: r for r in records}
        seen: set[UUID] = set()
        out: list[MemoryRecord] = []
        for uid in selected_ids:
            if uid in seen:
                continue
            seen.add(uid)
            rec = by_id.get(uid)
            if rec is not None:
                out.append(rec)
            if len(out) >= self.top_n:
                break
        return out

    # ------------------------------------------------------------------
    # Reinforcement
    # ------------------------------------------------------------------

    async def _reinforce(self, records: list[MemoryRecord]) -> None:
        """Bump reinforcement_count and last_recalled_at on each recalled record."""
        if not records:
            return
        ids = [str(r.id) for r in records]
        try:
            await self.supabase_client.rpc(
                "reinforce_agent_memories",
                {"p_memory_ids": ids, "p_now": datetime.now(timezone.utc).isoformat()},
            ).execute()
        except Exception:  # noqa: BLE001
            logger.exception("[memory.retriever] reinforcement update failed; ignoring")

    # ------------------------------------------------------------------
    # Cache
    # ------------------------------------------------------------------

    @staticmethod
    def _build_cache_key(
        *,
        user_id: UUID,
        agent_id: UUID,
        session_id: Optional[UUID],
        user_query: str,
    ) -> str:
        h = hashlib.sha1(user_query.encode("utf-8")).hexdigest()[:12]  # noqa: S324
        sess = str(session_id) if session_id else "no-session"
        return f"memory:recall:{user_id}:{agent_id}:{sess}:{h}"

    async def _cache_get(self, key: str) -> Optional[list[UUID]]:
        if self.redis_client is None:
            return None
        try:
            raw = await self.redis_client.get(key)
        except Exception:  # noqa: BLE001
            logger.exception("[memory.retriever] redis get failed; degrading to MISS")
            return None
        if raw is None:
            return None
        try:
            payload = json.loads(raw if isinstance(raw, str) else raw.decode("utf-8"))
            return [UUID(s) for s in payload]
        except (json.JSONDecodeError, ValueError):
            logger.warning("[memory.retriever] cache value corrupt; treating as MISS")
            return None

    async def _cache_set(self, key: str, ids: list[UUID]) -> None:
        if self.redis_client is None:
            return
        try:
            payload = json.dumps([str(u) for u in ids])
            await self.redis_client.set(key, payload, ex=self.cache_ttl_s)
        except Exception:  # noqa: BLE001
            logger.exception("[memory.retriever] redis set failed; ignoring")

    async def _fetch_by_ids(
        self, ids: list[UUID], *, user_id: UUID
    ) -> list[MemoryRecord]:
        """Cache-hit path: fetch full records by id, P0-filtered by user_id."""
        if not ids:
            return []
        try:
            result = (
                await self.supabase_client.table("agent_memories")
                .select("*")
                .in_("id", [str(u) for u in ids])
                .eq("user_id", str(user_id))  # P0 isolation
                .execute()
            )
        except Exception:  # noqa: BLE001
            logger.exception("[memory.retriever] cache-hit fetch failed; returning []")
            return []
        rows = result.data or []
        # Preserve cached order.
        by_id = {}
        for row in rows:
            try:
                rec = _row_to_record(row)
                by_id[rec.id] = rec
            except (KeyError, ValueError):
                continue
        return [by_id[u] for u in ids if u in by_id]

    async def _safe_embed(self, text: str) -> Optional[list[float]]:
        try:
            return await self.embedding_call(text)
        except Exception:  # noqa: BLE001
            logger.exception("[memory.retriever] embedding call failed; no recall")
            return None


# ---------------------------------------------------------------------------
# Row mapper
# ---------------------------------------------------------------------------


def _row_to_record(row: dict) -> MemoryRecord:
    extracted_raw = row.get("extracted_from")
    extracted = ExtractedFrom(extracted_raw) if extracted_raw else None
    return MemoryRecord(
        id=UUID(row["id"]),
        agent_id=UUID(row["agent_id"]),
        user_id=UUID(row["user_id"]),
        scope=MemoryScope(row.get("scope") or MemoryScope.AGENT_USER.value),
        summary=row.get("summary") or "",
        when_to_use=row.get("when_to_use") or "",
        extracted_from=extracted,
        reinforcement_count=int(row.get("reinforcement_count") or 0),
        last_recalled_at=_parse_dt(row.get("last_recalled_at")),
        created_at=_parse_dt(row.get("created_at")) or datetime.now(timezone.utc),
        metadata=row.get("metadata_json") or {},
    )


def _parse_dt(value) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    try:
        # PostgREST returns ISO 8601 strings
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


__all__ = [
    "DEFAULT_CACHE_TTL_S",
    "DEFAULT_TOP_K_CANDIDATES",
    "DEFAULT_TOP_N_FINAL",
    "MemoryRetriever",
    "SonnetCall",
    "EmbeddingCall",
]
