"""Data access for conversation_memory (rolling head-summary sidecar).

Backend-only table (migration 330; service-role RLS — never exposed to
PostgREST anon/authenticated, same posture as generated_media). ORM-model
style (read_scope/write_scope + select/insert on ``ConversationMemory``),
converged from the raw-SQL db_engine call style.
"""

from __future__ import annotations

from typing import Any, Optional

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.db.session import read_scope, write_scope
from app.models import ConversationMemory


class ConversationMemoryRepository:
    async def load(self, conversation_id: int) -> Optional[dict[str, Any]]:
        async with read_scope() as session:
            row = (
                (
                    await session.execute(
                        select(
                            ConversationMemory.conversation_id,
                            ConversationMemory.summary_md,
                            ConversationMemory.last_seq_summarized,
                            ConversationMemory.model,
                            ConversationMemory.updated_at,
                        ).where(
                            ConversationMemory.conversation_id == int(conversation_id)
                        )
                    )
                )
                .mappings()
                .first()
            )
            return dict(row) if row else None

    async def upsert(
        self,
        *,
        conversation_id: int,
        summary_md: str,
        last_seq_summarized: int,
        model: Optional[str] = None,
    ) -> None:
        # Monotonic guard: only advance when the incoming summary covers MORE
        # messages than the stored one (concurrent summarizers can't regress
        # the watermark) — the conditional ON CONFLICT ... WHERE clause.
        stmt = pg_insert(ConversationMemory).values(
            conversation_id=int(conversation_id),
            summary_md=summary_md,
            last_seq_summarized=int(last_seq_summarized),
            model=model,
            updated_at=func.now(),
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=["conversation_id"],
            set_={
                "summary_md": stmt.excluded.summary_md,
                "last_seq_summarized": stmt.excluded.last_seq_summarized,
                "model": stmt.excluded.model,
                "updated_at": func.now(),
            },
            where=(
                ConversationMemory.last_seq_summarized
                < stmt.excluded.last_seq_summarized
            ),
        )
        async with write_scope() as session:
            await session.execute(stmt)


_repo: Optional[ConversationMemoryRepository] = None


def get_conversation_memory_repository() -> ConversationMemoryRepository:
    global _repo
    if _repo is None:
        _repo = ConversationMemoryRepository()
    return _repo
