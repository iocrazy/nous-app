from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from loguru import logger

from app.db.supabase_client import get_async_supabase_admin

# The three personal flags a user can toggle on a hotspot.
STATE_FLAGS = ("is_read", "is_saved", "is_hidden")


class HotspotUserStateRepository:
    """Per-user read/saved/hidden state for global hotspots."""

    TABLE = "hotspot_user_state"

    async def _client(self):
        return await get_async_supabase_admin()

    async def get_states(
        self, user_id: str, hotspot_ids: list[str]
    ) -> dict[str, dict[str, bool]]:
        """Batch-fetch state for a set of hotspot ids → ``{hotspot_id: flags}``.

        Missing rows simply don't appear (caller treats them as all-false).
        """
        if not hotspot_ids:
            return {}
        client = await self._client()
        result = (
            await client.table(self.TABLE)
            .select("hotspot_id, is_read, is_saved, is_hidden")
            .eq("user_id", user_id)
            .in_("hotspot_id", hotspot_ids)
            .execute()
        )
        out: dict[str, dict[str, bool]] = {}
        for row in result.data or []:
            out[str(row["hotspot_id"])] = {f: bool(row.get(f)) for f in STATE_FLAGS}
        return out

    async def list_ids_where(self, user_id: str, *, flag: str) -> list[str]:
        """Hotspot ids where ``flag`` is true for this user (saved/hidden views)."""
        if flag not in STATE_FLAGS:
            raise ValueError(f"unknown flag: {flag}")
        client = await self._client()
        result = (
            await client.table(self.TABLE)
            .select("hotspot_id")
            .eq("user_id", user_id)
            .eq(flag, True)
            .execute()
        )
        return [str(r["hotspot_id"]) for r in (result.data or [])]

    async def set_state(
        self,
        user_id: str,
        hotspot_id: str,
        *,
        is_read: Optional[bool] = None,
        is_saved: Optional[bool] = None,
        is_hidden: Optional[bool] = None,
    ) -> dict[str, bool]:
        """Upsert the given flags (only the non-None ones). Returns the row's
        resulting flags. Defaults absent flags to false on first insert."""
        patch: dict[str, Any] = {"user_id": user_id, "hotspot_id": hotspot_id}
        provided = {
            "is_read": is_read,
            "is_saved": is_saved,
            "is_hidden": is_hidden,
        }
        for key, val in provided.items():
            if val is not None:
                patch[key] = val
        patch["updated_at"] = datetime.now(timezone.utc).isoformat()
        client = await self._client()
        try:
            result = (
                await client.table(self.TABLE)
                .upsert(patch, on_conflict="user_id,hotspot_id")
                .execute()
            )
            row = (result.data or [{}])[0]
            return {f: bool(row.get(f)) for f in STATE_FLAGS}
        except Exception as e:  # noqa: BLE001
            logger.error(f"set_state failed for {user_id}/{hotspot_id}: {e}")
            raise
