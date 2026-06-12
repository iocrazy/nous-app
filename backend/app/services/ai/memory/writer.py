"""Memory writer — embeds extracted facts and INSERTs into agent_memories.

Called from a Celery task (write_memory_task) so it never blocks the
chat path. The MemoryHarvester PostToolUse hook fires the Celery
signature, this module is what executes server-side.

Flow:
    1. Pull recent ai_messages for (session_id) split by role
    2. Run UserMemoryExtractor on user msgs, AssistantMemoryExtractor on
       assistant msgs (in parallel)
    3. For each ExtractedFact, embed when_to_use (NOT summary — RemiMem)
    4. INSERT one agent_memories row per fact with scope='agent_user'
       (per plan-eng-review: M1.B writes only this layer)

Failures are logged + swallowed at the row level so a single bad fact
doesn't lose the rest of the batch.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from typing import Any, Optional
from uuid import UUID

from app.services.ai.memory import MemoryScope
from app.services.ai.memory.embedding_utils import parse_embedding_text
from app.services.ai.memory.extractor import (
    AssistantMemoryExtractor,
    ExtractedFact,
    UserMemoryExtractor,
)

logger = logging.getLogger(__name__)


@dataclass
class MemoryWriter:
    """Orchestrates extract → embed → insert. Caller injects all I/O.

    Wave F (F3): optional contradiction_classifier hook. When set, after
    each successful insert we look up nearest existing memories in the
    same namespace and ask the cheap LLM to classify replaces /
    contradicts / supplements / unrelated. REPLACES + CONTRADICTS
    trigger a status='superseded' + superseded_by=new.id update on the
    old row.

    Caller wires the classifier closure (typically a cheap-LLM call).
    Default None = legacy behavior (no contradiction check).
    """

    user_extractor: UserMemoryExtractor
    assistant_extractor: AssistantMemoryExtractor
    embedding_service: (
        Any  # exposes async generate_embedding(text) -> list[float] | None
    )
    # Wave F (F3): optional. If set, must be `async (prompt: str) -> str`.
    contradiction_classifier: Optional[Any] = None

    async def write(
        self,
        *,
        agent_id: UUID,
        user_id: UUID,
        # run_id / session_id are BIGINT Snowflake ids (mig 231/232) → str.
        run_id: Optional[str],
        user_messages: list[str],
        assistant_messages: list[str],
        scope: MemoryScope = MemoryScope.AGENT_USER,
        session_id: Optional[str] = None,
    ) -> int:
        """Run extraction → embedding → insert. Returns count of rows inserted.

        Phase N (N1): when ``session_id`` is provided, batch shares one
        thread_id (newly minted or reused from recent siblings via
        threading.assign_thread_for).
        """
        user_facts, asst_facts = await asyncio.gather(
            self.user_extractor.extract(user_messages),
            self.assistant_extractor.extract(assistant_messages),
        )
        all_facts = list(user_facts) + list(asst_facts)
        if not all_facts:
            return 0

        # N1: resolve thread_id once for the whole batch (all facts in
        # one harvest share the same conversational moment).
        thread_id: Optional[UUID] = None
        if session_id is not None:
            try:
                from datetime import datetime, timezone

                from app.services.ai.memory.threading import assign_thread_for

                async def _fetch_recent(aid, uid, sid, limit):
                    from app.db import engine as db_engine

                    return await db_engine.fetch_all(
                        "SELECT created_at, thread_id FROM public.agent_memories "
                        "WHERE agent_id = :aid AND user_id = :uid "
                        "AND session_id = :sid AND status = 'active' "
                        "ORDER BY created_at DESC LIMIT :lim",
                        {
                            "aid": str(aid),
                            "uid": str(uid),
                            "sid": str(sid),
                            "lim": limit,
                        },
                    )

                thread_id = await assign_thread_for(
                    agent_id=str(agent_id),
                    user_id=str(user_id),
                    session_id=str(session_id),
                    created_at=datetime.now(timezone.utc),
                    fetch_recent_in_session=_fetch_recent,
                )
            except Exception:
                thread_id = None  # best-effort

        rows = await asyncio.gather(
            *(
                self._build_row(
                    fact,
                    agent_id=agent_id,
                    user_id=user_id,
                    run_id=run_id,
                    scope=scope,
                    thread_id=thread_id,
                    session_id=session_id,
                )
                for fact in all_facts
            )
        )
        rows = [r for r in rows if r is not None]
        if not rows:
            return 0

        from app.db import engine as db_engine

        inserted_rows: list[dict] = []
        for row in rows:
            cols = list(row.keys())
            placeholders = []
            params: dict[str, Any] = {}
            for c in cols:
                if c == "embedding":
                    params[c] = "[" + ",".join(repr(x) for x in row[c]) + "]"
                    placeholders.append("CAST(:embedding AS vector)")
                elif c == "metadata_json":
                    params[c] = json.dumps(row[c])
                    placeholders.append("CAST(:metadata_json AS jsonb)")
                else:
                    params[c] = row[c]
                    placeholders.append(f":{c}")
            # RETURNING only what the contradiction post-pass consumes, and
            # CAST the vector to text (asyncpg has no pgvector codec on the
            # shared engine — same pattern as scheduled_memory_consolidation).
            # Avoids shipping the 1536-dim embedding back as an undecodable
            # type on every memory insert.
            sql = (
                "INSERT INTO public.agent_memories (" + ", ".join(cols) + ") "
                "VALUES (" + ", ".join(placeholders) + ") "
                "RETURNING id, summary, CAST(embedding AS text) AS embedding"
            )
            try:
                got = await db_engine.execute_returning_one(sql, params)
                if got:
                    inserted_rows.append(got)
            except Exception:  # noqa: BLE001
                logger.exception(
                    "[memory.writer] insert failed; dropped 1 candidate fact"
                )
        if not inserted_rows:
            return 0

        # Wave F (F3): post-insert contradiction check. Best-effort —
        # any failure here just means we don't mark old memories
        # superseded (worst case: duplicate semantics in retrieval, the
        # consolidation sweeper will eventually merge them).
        if self.contradiction_classifier is not None:
            try:
                await self._supersede_contradicting(
                    inserted_rows,
                    agent_id=agent_id,
                    user_id=user_id,
                    scope=scope,
                )
            except Exception:
                logger.exception(
                    "[memory.writer] contradiction post-pass failed (non-fatal)"
                )

        return len(inserted_rows)

    async def _supersede_contradicting(
        self,
        inserted_rows: list[dict],
        *,
        agent_id: UUID,
        user_id: UUID,
        scope: MemoryScope,
    ) -> None:
        """For each newly-inserted memory, find HIGH-similarity existing
        rows + ask classifier; mark replaces/contradicts old as superseded."""
        from app.services.ai.memory.contradiction import (
            HIGH_SIMILARITY,
            classify_pair,
            select_supersede_targets,
        )

        for new_row in inserted_rows:
            new_id = new_row.get("id")
            new_summary = new_row.get("summary") or ""
            new_embedding = parse_embedding_text(new_row.get("embedding"))
            if not (new_id and new_summary and new_embedding):
                continue

            # Pull nearest existing active memories in same namespace.
            # Uses the cosine RPC if available, else cheap fallback to
            # pure SQL list+score (acceptable for typical N).
            neighbors = await self._nearest_existing(
                agent_id=agent_id,
                user_id=user_id,
                scope=scope,
                exclude_id=new_id,
                embedding=new_embedding,
                threshold=HIGH_SIMILARITY,
                limit=3,
            )
            if not neighbors:
                continue

            decisions = []
            for old in neighbors:
                d = await classify_pair(
                    old_summary=old["summary"],
                    old_id=old["id"],
                    new_summary=new_summary,
                    classifier=self.contradiction_classifier,
                )
                if d is not None:
                    decisions.append(d)

            target_ids = select_supersede_targets(decisions)

            from app.db import engine as db_engine

            for old_id in target_ids:
                try:
                    await db_engine.execute(
                        "UPDATE public.agent_memories SET status = 'superseded', "
                        "superseded_by = :new WHERE id = :old",
                        {"new": str(new_id), "old": str(old_id)},
                    )
                    from app.agent_framework._metrics_helper import inc_metric

                    inc_metric("memory_superseded_by_contradiction")
                except Exception:
                    logger.exception(
                        "[memory.writer] failed to mark %s superseded by %s",
                        old_id,
                        new_id,
                    )

    async def _nearest_existing(
        self,
        *,
        agent_id: UUID,
        user_id: UUID,
        scope: MemoryScope,
        exclude_id: Any,
        embedding: list[float],
        threshold: float,
        limit: int,
    ) -> list[dict]:
        """Find existing memories with cosine >= threshold to ``embedding``.

        Best-effort: returns [] on any failure. Uses a simple top-K read
        + cosine in Python (acceptable for typical N). A future optimization
        could route through a pgvector RPC, but this keeps the contradiction
        path self-contained without a new SQL function.
        """
        from app.db import engine as db_engine

        try:
            rows = await db_engine.fetch_all(
                "SELECT id, summary, CAST(embedding AS text) AS embedding "
                "FROM public.agent_memories "
                "WHERE agent_id = :aid AND user_id = :uid AND scope = :scope "
                "AND status = 'active' AND id <> :exclude LIMIT 50",
                {
                    "aid": str(agent_id),
                    "uid": str(user_id),
                    "scope": scope.value,
                    "exclude": str(exclude_id),
                },
            )
        except Exception:
            return []

        scored: list[tuple[float, dict]] = []
        for row in rows:
            emb = parse_embedding_text(row.get("embedding"))
            if not emb:
                continue
            sim = _cosine(embedding, emb)
            if sim >= threshold:
                scored.append((sim, row))
        scored.sort(reverse=True, key=lambda t: t[0])
        return [r for _, r in scored[:limit]]

    async def _build_row(
        self,
        fact: ExtractedFact,
        *,
        agent_id: UUID,
        user_id: UUID,
        # run_id / session_id are BIGINT Snowflake ids (mig 231/232) → str.
        run_id: Optional[str],
        scope: MemoryScope,
        thread_id: Optional[UUID] = None,
        session_id: Optional[str] = None,
    ) -> Optional[dict]:
        embedding = await self._embed(fact.when_to_use)
        if embedding is None:
            # Skipped (e.g. no API key) — log + drop. Not a row worth saving
            # without an embedding because retriever can't find it later.
            return None

        # Phase N (N2): cheap heuristic kind classification at write time.
        # The retriever uses kind for per-kind weight modifiers (M2).
        from app.services.ai.memory.kind_classifier import classify_heuristic

        kind = classify_heuristic(fact.summary).value

        row = {
            "agent_id": str(agent_id),
            "user_id": str(user_id),
            # agent_memories.run_id is BIGINT (FK → agent_runs.id, mig 232) —
            # asyncpg rejects str binds on int8.
            "run_id": int(run_id) if run_id else None,
            "scope": scope.value,
            "summary": fact.summary,
            "when_to_use": fact.when_to_use,
            "extracted_from": fact.extracted_from.value,
            "embedding": embedding,
            "metadata_json": {},
            "kind": kind,
        }
        if thread_id is not None:
            row["thread_id"] = str(thread_id)
        if session_id is not None:
            row["session_id"] = str(session_id)
        return row

    async def _embed(self, text: str) -> Optional[list[float]]:
        try:
            return await self.embedding_service.generate_embedding(text)
        except Exception:  # noqa: BLE001
            logger.exception("[memory.writer] embedding call failed; skipping fact")
            return None


def _cosine(a: list[float], b: list[float]) -> float:
    """Cosine similarity for embedding lists. Defensive against zero / mismatched."""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(x * x for x in b) ** 0.5
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


__all__ = ["MemoryWriter"]
