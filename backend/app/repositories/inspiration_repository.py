"""Repository for inspiration_notes (migration 348).

Pure data access via the service-role supabase client (canvas_repository
template). Ownership checks live in the service layer — every method here
trusts its caller. All bigint ids are coerced with _bigint before binding.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from loguru import logger

from app.db.supabase_client import get_async_supabase_admin


def _bigint(value: Any) -> int:
    if isinstance(value, int):
        return value
    return int(str(value))


class InspirationNotesRepository:
    TABLE = "inspiration_notes"

    async def _client(self):
        return await get_async_supabase_admin()

    async def create(
        self,
        user_id: str,
        content_md: str,
        tags: List[str],
        note_date: str,
        ref_hotspot: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        try:
            client = await self._client()
            payload: Dict[str, Any] = {
                "user_id": user_id,
                "content_md": content_md,
                "tags": tags,
                "note_date": note_date,
            }
            if ref_hotspot is not None:
                payload["ref_hotspot"] = ref_hotspot
            result = await client.table(self.TABLE).insert(payload).execute()
            return result.data[0] if result and result.data else None
        except Exception as e:
            logger.error(f"inspiration create failed (user={user_id}): {e}")
            return None

    async def get_by_id(self, note_id: Any) -> Optional[Dict[str, Any]]:
        try:
            client = await self._client()
            result = (
                await client.table(self.TABLE)
                .select("*")
                .eq("id", _bigint(note_id))
                .is_("deleted_at", "null")
                .maybe_single()
                .execute()
            )
            return result.data if result and result.data else None
        except Exception as e:
            logger.error(f"inspiration get_by_id({note_id}) failed: {e}")
            return None

    async def list(
        self,
        user_id: str,
        *,
        date: Optional[str] = None,
        tag: Optional[str] = None,
        q: Optional[str] = None,
        limit: int = 50,
        before_id: Optional[Any] = None,
    ) -> List[Dict[str, Any]]:
        try:
            client = await self._client()
            query = (
                client.table(self.TABLE)
                .select("*")
                .eq("user_id", user_id)
                .is_("deleted_at", "null")
            )
            if date:
                query = query.eq("note_date", date)
            if tag:
                query = query.contains("tags", [tag])
            if q:
                query = query.ilike("content_md", f"%{q}%")
            if before_id:
                query = query.lt("id", _bigint(before_id))
            result = await query.order("id", desc=True).limit(limit).execute()
            return result.data or []
        except Exception as e:
            logger.error(f"inspiration list failed (user={user_id}): {e}")
            return []

    async def update(
        self,
        note_id: Any,
        *,
        content_md: Optional[str] = None,
        tags: Optional[List[str]] = None,
        pinned: Optional[bool] = None,
    ) -> Optional[Dict[str, Any]]:
        try:
            client = await self._client()
            payload: Dict[str, Any] = {
                "updated_at": datetime.now(timezone.utc).isoformat()
            }
            if content_md is not None:
                payload["content_md"] = content_md
            if tags is not None:
                payload["tags"] = tags
            if pinned is not None:
                payload["pinned"] = pinned
            result = (
                await client.table(self.TABLE)
                .update(payload)
                .eq("id", _bigint(note_id))
                .execute()
            )
            return result.data[0] if result and result.data else None
        except Exception as e:
            logger.error(f"inspiration update({note_id}) failed: {e}")
            return None

    async def soft_delete(self, note_id: Any) -> bool:
        try:
            client = await self._client()
            result = (
                await client.table(self.TABLE)
                .update({"deleted_at": datetime.now(timezone.utc).isoformat()})
                .eq("id", _bigint(note_id))
                .execute()
            )
            return bool(result and result.data)
        except Exception as e:
            logger.error(f"inspiration soft_delete({note_id}) failed: {e}")
            return False

    async def activity(
        self, user_id: str, date_from: str, date_to: str
    ) -> List[Dict[str, Any]]:
        try:
            client = await self._client()
            result = await client.rpc(
                "inspiration_activity",
                {"p_user_id": user_id, "p_from": date_from, "p_to": date_to},
            ).execute()
            return result.data or []
        except Exception as e:
            logger.error(f"inspiration activity failed (user={user_id}): {e}")
            return []

    async def tag_counts(self, user_id: str) -> List[Dict[str, Any]]:
        try:
            client = await self._client()
            result = await client.rpc(
                "inspiration_tag_counts", {"p_user_id": user_id}
            ).execute()
            return result.data or []
        except Exception as e:
            logger.error(f"inspiration tag_counts failed (user={user_id}): {e}")
            return []


_repo: Optional[InspirationNotesRepository] = None


def get_inspiration_notes_repository() -> InspirationNotesRepository:
    global _repo
    if _repo is None:
        _repo = InspirationNotesRepository()
    return _repo
