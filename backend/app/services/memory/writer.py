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
import logging
from dataclasses import dataclass
from typing import Any, Optional
from uuid import UUID

from app.services.memory import MemoryScope
from app.services.memory.extractor import (
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
    supabase_client: (
        Any  # async client, exposes table('agent_memories').insert().execute()
    )
    # Wave F (F3): optional. If set, must be `async (prompt: str) -> str`.
    contradiction_classifier: Optional[Any] = None

    async def write(
        self,
        *,
        agent_id: UUID,
        user_id: UUID,
        run_id: Optional[UUID],
        user_messages: list[str],
        assistant_messages: list[str],
        scope: MemoryScope = MemoryScope.AGENT_USER,
    ) -> int:
        """Run extraction → embedding → insert. Returns count of rows inserted."""
        user_facts, asst_facts = await asyncio.gather(
            self.user_extractor.extract(user_messages),
            self.assistant_extractor.extract(assistant_messages),
        )
        all_facts = list(user_facts) + list(asst_facts)
        if not all_facts:
            return 0

        rows = await asyncio.gather(
            *(
                self._build_row(
                    fact, agent_id=agent_id, user_id=user_id, run_id=run_id, scope=scope
                )
                for fact in all_facts
            )
        )
        rows = [r for r in rows if r is not None]
        if not rows:
            return 0

        try:
            insert_result = (
                await self.supabase_client.table("agent_memories")
                .insert(rows)
                .execute()
            )
        except Exception:  # noqa: BLE001
            logger.exception(
                "[memory.writer] batch insert failed; dropped %d candidate fact(s)",
                len(rows),
            )
            return 0

        # Wave F (F3): post-insert contradiction check. Best-effort —
        # any failure here just means we don't mark old memories
        # superseded (worst case: duplicate semantics in retrieval, the
        # consolidation sweeper will eventually merge them).
        if self.contradiction_classifier is not None:
            try:
                inserted_rows = list(insert_result.data or [])
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

        return len(rows)

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
        from app.services.memory.contradiction import (
            HIGH_SIMILARITY,
            classify_pair,
            select_supersede_targets,
        )

        for new_row in inserted_rows:
            new_id = new_row.get("id")
            new_summary = new_row.get("summary") or ""
            new_embedding = new_row.get("embedding")
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
            for old_id in target_ids:
                try:
                    await (
                        self.supabase_client.table("agent_memories")
                        .update(
                            {
                                "status": "superseded",
                                "superseded_by": str(new_id),
                            }
                        )
                        .eq("id", old_id)
                        .execute()
                    )
                    from app.agent_framework._metrics_helper import inc_metric
                    inc_metric("memory_superseded_by_contradiction")
                except Exception:
                    logger.exception(
                        "[memory.writer] failed to mark %s superseded by %s",
                        old_id, new_id,
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
        try:
            result = (
                await self.supabase_client.table("agent_memories")
                .select("id, summary, embedding")
                .eq("agent_id", str(agent_id))
                .eq("user_id", str(user_id))
                .eq("scope", scope.value)
                .eq("status", "active")
                .neq("id", str(exclude_id))
                .limit(50)
                .execute()
            )
        except Exception:
            return []

        rows = result.data or []
        scored: list[tuple[float, dict]] = []
        for row in rows:
            emb = row.get("embedding")
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
        run_id: Optional[UUID],
        scope: MemoryScope,
    ) -> Optional[dict]:
        embedding = await self._embed(fact.when_to_use)
        if embedding is None:
            # Skipped (e.g. no API key) — log + drop. Not a row worth saving
            # without an embedding because retriever can't find it later.
            return None

        return {
            "agent_id": str(agent_id),
            "user_id": str(user_id),
            "run_id": str(run_id) if run_id else None,
            "scope": scope.value,
            "summary": fact.summary,
            "when_to_use": fact.when_to_use,
            "extracted_from": fact.extracted_from.value,
            "embedding": embedding,
            "metadata_json": {},
        }

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
