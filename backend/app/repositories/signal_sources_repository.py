from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from loguru import logger

from app.db.supabase_client import get_async_supabase_admin


def compute_health(*, prev_failures: int, ok: bool, dead_threshold: int = 3) -> dict:
    """Pure state machine for source health. flipped_to_dead = crossed threshold this call."""
    if ok:
        return {"health": "ok", "consecutive_failures": 0, "flipped_to_dead": False}
    failures = prev_failures + 1
    was_dead = prev_failures >= dead_threshold
    is_dead = failures >= dead_threshold
    health = "dead" if is_dead else "degraded"
    return {
        "health": health,
        "consecutive_failures": failures,
        "flipped_to_dead": is_dead and not was_dead,
    }


class SignalSourcesRepository:
    TABLE = "signal_sources"

    async def _client(self):
        return await get_async_supabase_admin()

    async def list_enabled(self) -> list[dict[str, Any]]:
        client = await self._client()
        result = (
            await client.table(self.TABLE).select("*").eq("enabled", True).execute()
        )
        return result.data or []

    async def mark_health(
        self,
        source_id: str,
        *,
        ok: bool,
        error: Optional[str] = None,
        dead_threshold: int = 3,
    ) -> dict:
        client = await self._client()
        cur = (
            await client.table(self.TABLE)
            .select("consecutive_failures")
            .eq("id", source_id)
            .execute()
        )
        prev = (cur.data[0]["consecutive_failures"] if cur.data else 0) or 0
        state = compute_health(prev_failures=prev, ok=ok, dead_threshold=dead_threshold)
        now = datetime.now(timezone.utc).isoformat()
        patch: dict[str, Any] = {
            "health": state["health"],
            "consecutive_failures": state["consecutive_failures"],
            "last_fetched_at": now,
        }
        if ok:
            patch["last_ok_at"] = now
            patch["last_error"] = None
        else:
            patch["last_error"] = (error or "")[:500]
        try:
            await client.table(self.TABLE).update(patch).eq("id", source_id).execute()
        except Exception as e:  # noqa: BLE001
            logger.error(f"mark_health failed for {source_id}: {e}")
        return state
