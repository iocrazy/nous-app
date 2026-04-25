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
    """Orchestrates extract → embed → insert. Caller injects all I/O."""

    user_extractor: UserMemoryExtractor
    assistant_extractor: AssistantMemoryExtractor
    embedding_service: (
        Any  # exposes async generate_embedding(text) -> list[float] | None
    )
    supabase_client: (
        Any  # async client, exposes table('agent_memories').insert().execute()
    )

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
            await self.supabase_client.table("agent_memories").insert(rows).execute()
            return len(rows)
        except Exception:  # noqa: BLE001
            logger.exception(
                "[memory.writer] batch insert failed; dropped %d candidate fact(s)",
                len(rows),
            )
            return 0

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


__all__ = ["MemoryWriter"]
