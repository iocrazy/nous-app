"""Data access for conversation_memory (rolling head-summary sidecar).

Backend-only table (migration 330; service-role RLS — never exposed to
PostgREST anon/authenticated, same posture as generated_media). All access
goes through the privileged db_engine, matching conversation_repository's
single-statement call style (db_engine.fetch_one / db_engine.execute — see
``ConversationRepository.get_conversation`` / ``mark_read``).
"""

from __future__ import annotations

from typing import Any, Optional

from app.db import engine as db_engine


class ConversationMemoryRepository:
    async def load(self, conversation_id: int) -> Optional[dict[str, Any]]:
        return await db_engine.fetch_one(
            "SELECT conversation_id, summary_md, last_seq_summarized, model, updated_at "
            "FROM public.conversation_memory WHERE conversation_id = :cid",
            {"cid": int(conversation_id)},
        )

    async def upsert(
        self,
        *,
        conversation_id: int,
        summary_md: str,
        last_seq_summarized: int,
        model: Optional[str] = None,
    ) -> None:
        await db_engine.execute(
            "INSERT INTO public.conversation_memory "
            "(conversation_id, summary_md, last_seq_summarized, model, updated_at) "
            "VALUES (:cid, :summary, :last_seq, :model, now()) "
            "ON CONFLICT (conversation_id) DO UPDATE SET "
            "summary_md = EXCLUDED.summary_md, "
            "last_seq_summarized = EXCLUDED.last_seq_summarized, "
            "model = EXCLUDED.model, updated_at = now() "
            "WHERE public.conversation_memory.last_seq_summarized < EXCLUDED.last_seq_summarized",
            {
                "cid": int(conversation_id),
                "summary": summary_md,
                "last_seq": int(last_seq_summarized),
                "model": model,
            },
        )


_repo: Optional[ConversationMemoryRepository] = None


def get_conversation_memory_repository() -> ConversationMemoryRepository:
    global _repo
    if _repo is None:
        _repo = ConversationMemoryRepository()
    return _repo
