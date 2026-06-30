"""Data access for chat_attachments (Task 2 — independent chat image store).

Uses the privileged db_engine (BYPASSRLS); the service layer enforces membership.
Mirrors the ChatRepository pattern from chat_repository.py.
"""

from __future__ import annotations

from typing import Any, Optional

from app.db import engine as db_engine


def _bigint(v: Any) -> int:
    return int(v)


class ChatAttachmentRepository:
    async def create(
        self,
        *,
        scope_id: int,
        channel_id: int,
        creator_id: str,
        mime: Optional[str],
        file_path: str,
        file_size_bytes: Optional[int],
        width: Optional[int] = None,
        height: Optional[int] = None,
    ) -> dict:
        """INSERT a new chat_attachments row and return the full inserted row.

        Raises RuntimeError when the INSERT returns no row (should never happen
        in production but guards against silent failures in tests/mocks).
        """
        row = await db_engine.execute_returning_one(
            """
            INSERT INTO public.chat_attachments
              (scope_id, channel_id, creator_id, mime, file_path,
               file_size_bytes, width, height)
            VALUES
              (:scope_id, :channel_id, :creator_id, :mime, :file_path,
               :file_size_bytes, :width, :height)
            RETURNING *
            """,
            {
                "scope_id": _bigint(scope_id),
                "channel_id": _bigint(channel_id),
                "creator_id": creator_id,
                "mime": mime,
                "file_path": file_path,
                "file_size_bytes": file_size_bytes,
                "width": width,
                "height": height,
            },
        )
        if row is None:
            raise RuntimeError("INSERT INTO chat_attachments returned no row")
        return _coerce_bigints(dict(row))

    async def get(self, attachment_id: int) -> Optional[dict]:
        """Fetch a single row by primary key, or None if not found."""
        row = await db_engine.fetch_one(
            """
            SELECT id, scope_id, channel_id, creator_id, mime, file_path,
                   file_size_bytes, width, height, promoted_resource_id, created_at
              FROM public.chat_attachments
             WHERE id = :id
            """,
            {"id": _bigint(attachment_id)},
        )
        if row is None:
            return None
        return _coerce_bigints(dict(row))

    async def set_promoted(self, attachment_id: int, resource_id: int) -> None:
        """Mark this attachment as promoted to a resource (idempotent UPDATE)."""
        await db_engine.execute(
            """
            UPDATE public.chat_attachments
               SET promoted_resource_id = :resource_id
             WHERE id = :id
            """,
            {
                "id": _bigint(attachment_id),
                "resource_id": _bigint(resource_id),
            },
        )


def _coerce_bigints(row: dict) -> dict:
    """Coerce Snowflake BIGINT columns that asyncpg may return as Decimal/str."""
    for col in ("id", "scope_id", "channel_id"):
        if row.get(col) is not None:
            row[col] = _bigint(row[col])
    return row


_repo: Optional[ChatAttachmentRepository] = None


def get_chat_attachment_repository() -> ChatAttachmentRepository:
    global _repo
    if _repo is None:
        _repo = ChatAttachmentRepository()
    return _repo
