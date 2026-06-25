from __future__ import annotations

from loguru import logger

from app.db.supabase_client import get_async_supabase_admin


class UserHiddenSourcesRepository:
    """Per-user "close/hide" of signal sources.

    A hidden source keeps collecting globally; it's just excluded from this
    user's feed. Keyed by (user_id, source_id) — see migration 316.
    """

    TABLE = "user_hidden_sources"

    async def _client(self):
        return await get_async_supabase_admin()

    async def list_hidden_ids(self, user_id: str) -> list[str]:
        """Source ids this user has hidden (as strings, for set membership)."""
        client = await self._client()
        result = (
            await client.table(self.TABLE)
            .select("source_id")
            .eq("user_id", user_id)
            .execute()
        )
        return [str(r["source_id"]) for r in (result.data or [])]

    async def hide(self, user_id: str, source_id: str) -> None:
        """Hide a source for this user (idempotent upsert)."""
        client = await self._client()
        try:
            await client.table(self.TABLE).upsert(
                {"user_id": user_id, "source_id": source_id},
                on_conflict="user_id,source_id",
            ).execute()
        except Exception as e:  # noqa: BLE001
            logger.error(f"hide source failed for {user_id}/{source_id}: {e}")
            raise

    async def unhide(self, user_id: str, source_id: str) -> None:
        """Un-hide a source for this user (idempotent — no-op if not hidden)."""
        client = await self._client()
        try:
            await client.table(self.TABLE).delete().eq("user_id", user_id).eq(
                "source_id", source_id
            ).execute()
        except Exception as e:  # noqa: BLE001
            logger.error(f"unhide source failed for {user_id}/{source_id}: {e}")
            raise
