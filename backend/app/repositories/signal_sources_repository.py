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


# Source kinds permitted by the signal_sources CHECK constraint (migration 304).
ALLOWED_KINDS = ("newsnow", "rss", "http_api", "custom")
# Kinds that actually have a working adapter (registry.get_adapter). http_api /
# custom are reserved in the DB CHECK but unimplemented — creating one yields a
# source that fails every fetch, so source-creation is restricted to these.
IMPLEMENTED_KINDS = ("newsnow", "rss")


class SignalSourcesRepository:
    TABLE = "signal_sources"

    async def _client(self):
        return await get_async_supabase_admin()

    async def tier_map(self) -> dict[str, int]:
        """``{source_id(str): tier}`` for every source — the credibility prior
        the code scorer multiplies in. Missing/NULL tier defaults to 2."""
        client = await self._client()
        result = await client.table(self.TABLE).select("id, tier").execute()
        return {str(r["id"]): int(r.get("tier") or 2) for r in (result.data or [])}

    async def list_enabled(self) -> list[dict[str, Any]]:
        client = await self._client()
        result = (
            await client.table(self.TABLE).select("*").eq("enabled", True).execute()
        )
        return result.data or []

    async def list_all(self) -> list[dict[str, Any]]:
        """All sources (enabled + disabled) for the read-only health surface.

        Ordered worst-health-first: 'dead' < 'degraded' < 'ok' sorts ascending,
        so failing sources surface at the top; ties broken by name.
        """
        client = await self._client()
        result = (
            await client.table(self.TABLE)
            .select("*")
            .order("health")
            .order("name")
            .execute()
        )
        return result.data or []

    async def list_visible(self, user_id: str) -> list[dict[str, Any]]:
        """Sources this user may see/manage: system sources (user_id IS NULL)
        plus their own. Other users' private sources are excluded. Ordered
        worst-health-first, then by name."""
        client = await self._client()
        result = (
            await client.table(self.TABLE)
            .select("*")
            .or_(f"user_id.is.null,user_id.eq.{user_id}")
            .order("health")
            .order("name")
            .execute()
        )
        return result.data or []

    async def feed_source_ids(self, user_id: str, hidden_ids: list[str]) -> list[str]:
        """Source ids whose hotspots belong in this user's feed: ENABLED visible
        (system + own) minus the ones they've hidden. The feed query filters
        ``source_id IN (...)`` on this set, so a deleted/disabled (admin-closed)/
        other-user/hidden source's hotspots never surface."""
        hidden = set(hidden_ids)
        rows = await self.list_visible(user_id)
        return [
            str(r["id"])
            for r in rows
            if r.get("enabled", True) and str(r["id"]) not in hidden
        ]

    async def get_source(self, source_id: str) -> dict | None:
        """A single source by id (any owner) — for ownership/existence checks."""
        client = await self._client()
        result = (
            await client.table(self.TABLE)
            .select("*")
            .eq("id", source_id)
            .limit(1)
            .execute()
        )
        return (result.data or [None])[0]

    async def create_source(
        self,
        *,
        user_id: str,
        kind: str,
        name: str,
        config: dict[str, Any],
        category: Optional[str],
        enabled: bool = True,
    ) -> dict[str, Any]:
        """Insert a user-owned source. Its hotspots are scoped to this user via
        the feed's source-id filter (other clients never see them)."""
        client = await self._client()
        row = {
            "user_id": user_id,
            "kind": kind,
            "name": name,
            "config": config or {},
            "category": category,
            "enabled": enabled,
        }
        result = await client.table(self.TABLE).insert(row).execute()
        return (result.data or [row])[0]

    async def admin_create_source(
        self,
        *,
        kind: str,
        name: str,
        config: dict[str, Any],
        category: Optional[str] = None,
        tier: int = 2,
    ) -> dict[str, Any]:
        """Create a SYSTEM source (user_id NULL → visible to everyone). Distinct
        from create_source, which makes a user-private source. Admin-only."""
        client = await self._client()
        row = {
            "user_id": None,
            "kind": kind,
            "name": name,
            "config": config or {},
            "category": category,
            "enabled": True,
            "tier": int(tier),
        }
        result = await client.table(self.TABLE).insert(row).execute()
        return (result.data or [row])[0]

    async def admin_update(
        self,
        source_id: str,
        *,
        enabled: Optional[bool] = None,
        tier: Optional[int] = None,
    ) -> dict | None:
        """Admin-set a source's ``enabled`` (global on/off — disabling stops
        collection AND drops it from every feed) and/or ``tier`` (credibility
        prior). No ownership restriction — admin acts on any source. Returns the
        updated row, or the existing row when nothing changed."""
        patch: dict[str, Any] = {}
        if enabled is not None:
            patch["enabled"] = bool(enabled)
        if tier is not None:
            patch["tier"] = int(tier)
        if not patch:
            return await self.get_source(source_id)
        client = await self._client()
        result = (
            await client.table(self.TABLE).update(patch).eq("id", source_id).execute()
        )
        return (result.data or [None])[0]

    async def delete_source(self, *, user_id: str, source_id: str) -> bool:
        """Delete a source the user OWNS (stops collection). Returns False when
        nothing was deleted (not found, or not owned by this user — the
        ``user_id`` predicate makes deleting others'/system sources a no-op)."""
        client = await self._client()
        result = (
            await client.table(self.TABLE)
            .delete()
            .eq("id", source_id)
            .eq("user_id", user_id)
            .execute()
        )
        return bool(result.data)

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
