"""Repository for inspiration_attachments (migration 348). Pure data access.

Same service-role postgrest client + _bigint template as
``inspiration_repository.py`` (Task 3). ``storage_backend`` has a DB default
('supabase') and is not set here — this repo's callers only ever write
through the Supabase ObjectStore (see attachment_service.py).
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from loguru import logger

from app.db.supabase_client import get_async_supabase_admin


def _bigint(value: Any) -> int:
    if isinstance(value, int):
        return value
    return int(str(value))


class InspirationAttachmentsRepository:
    TABLE = "inspiration_attachments"

    async def _client(self):
        return await get_async_supabase_admin()

    async def create(
        self,
        note_id: Any,
        user_id: str,
        bucket: str,
        path: str,
        mime: str,
        size_bytes: int,
        original_name: str,
    ) -> Optional[Dict[str, Any]]:
        try:
            client = await self._client()
            result = (
                await client.table(self.TABLE)
                .insert(
                    {
                        "note_id": _bigint(note_id),
                        "user_id": user_id,
                        "bucket": bucket,
                        "path": path,
                        "mime": mime,
                        "size_bytes": size_bytes,
                        "original_name": original_name,
                    }
                )
                .execute()
            )
            return result.data[0] if result and result.data else None
        except Exception as e:
            logger.error(f"inspiration attachment create failed (note={note_id}): {e}")
            return None

    async def get_by_id(self, attachment_id: Any) -> Optional[Dict[str, Any]]:
        try:
            client = await self._client()
            result = (
                await client.table(self.TABLE)
                .select("*")
                .eq("id", _bigint(attachment_id))
                .maybe_single()
                .execute()
            )
            return result.data if result and result.data else None
        except Exception as e:
            logger.error(f"inspiration attachment get({attachment_id}) failed: {e}")
            return None

    async def list_for_notes(self, note_ids: List[Any]) -> List[Dict[str, Any]]:
        if not note_ids:
            return []
        try:
            client = await self._client()
            result = (
                await client.table(self.TABLE)
                .select("*")
                .in_("note_id", [_bigint(n) for n in note_ids])
                .order("id")
                .execute()
            )
            return result.data or []
        except Exception as e:
            logger.error(f"inspiration attachments list_for_notes failed: {e}")
            return []

    async def delete(self, attachment_id: Any) -> bool:
        try:
            client = await self._client()
            result = (
                await client.table(self.TABLE)
                .delete()
                .eq("id", _bigint(attachment_id))
                .execute()
            )
            return bool(result and result.data)
        except Exception as e:
            logger.error(f"inspiration attachment delete({attachment_id}) failed: {e}")
            return False


_repo: Optional[InspirationAttachmentsRepository] = None


def get_inspiration_attachments_repository() -> InspirationAttachmentsRepository:
    global _repo
    if _repo is None:
        _repo = InspirationAttachmentsRepository()
    return _repo
