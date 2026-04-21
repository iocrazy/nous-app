"""Repository for admin alert rules + alert history."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from app.db import get_async_supabase_admin


class AlertRulesRepository:
    RULES_TABLE = "alert_rules"
    HISTORY_TABLE = "alert_history"

    async def _client(self):
        return await get_async_supabase_admin()

    # ─── Rules ──────────────────────────────────────────────────────────

    async def list_rules(self) -> tuple[list[dict[str, Any]], int]:
        client = await self._client()
        result = await (
            client.table(self.RULES_TABLE)
            .select("*", count="exact")
            .order("created_at", desc=True)
            .execute()
        )
        return result.data or [], result.count or 0

    async def list_active_rules(self) -> list[dict[str, Any]]:
        client = await self._client()
        result = await (
            client.table(self.RULES_TABLE).select("*").eq("is_active", True).execute()
        )
        return result.data or []

    async def create_rule(self, payload: dict[str, Any]) -> Optional[dict[str, Any]]:
        client = await self._client()
        result = await client.table(self.RULES_TABLE).insert(payload).execute()
        return result.data[0] if result.data else None

    async def update_rule(
        self, rule_id: str, changes: dict[str, Any]
    ) -> Optional[dict[str, Any]]:
        client = await self._client()
        # Always stamp updated_at so monitoring sees the change
        changes = {
            **changes,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        result = await (
            client.table(self.RULES_TABLE).update(changes).eq("id", rule_id).execute()
        )
        return result.data[0] if result.data else None

    async def delete_rule(self, rule_id: str) -> None:
        client = await self._client()
        await client.table(self.RULES_TABLE).delete().eq("id", rule_id).execute()

    async def auto_unmute_rule(self, rule_id: str) -> None:
        """Clear the mute flag when mute_until has passed."""
        await self.update_rule(rule_id, {"is_muted": False, "mute_until": None})

    # ─── History ────────────────────────────────────────────────────────

    async def list_history(
        self,
        *,
        page: int,
        page_size: int,
        rule_id: Optional[str] = None,
        resolved: Optional[bool] = None,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
    ) -> tuple[list[dict[str, Any]], int]:
        client = await self._client()
        query = client.table(self.HISTORY_TABLE).select("*", count="exact")
        if rule_id:
            query = query.eq("rule_id", rule_id)
        if resolved is not None:
            query = query.eq("resolved", resolved)
        if start_date:
            query = query.gte("created_at", start_date.isoformat())
        if end_date:
            query = query.lte("created_at", end_date.isoformat())

        offset = (page - 1) * page_size
        result = await (
            query.order("created_at", desc=True)
            .range(offset, offset + page_size - 1)
            .execute()
        )
        return result.data or [], result.count or 0

    async def insert_history(self, payload: dict[str, Any]) -> None:
        client = await self._client()
        await client.table(self.HISTORY_TABLE).insert(payload).execute()

    async def resolve_history(self, alert_id: str) -> None:
        client = await self._client()
        await (
            client.table(self.HISTORY_TABLE)
            .update(
                {
                    "resolved": True,
                    "resolved_at": datetime.now(timezone.utc).isoformat(),
                }
            )
            .eq("id", alert_id)
            .execute()
        )

    # ─── Metric queries (for alert evaluation) ─────────────────────────

    async def request_status_codes(self, since_iso: str) -> list[dict[str, Any]]:
        client = await self._client()
        result = await (
            client.table("api_request_logs")
            .select("status_code")
            .gte("timestamp", since_iso)
            .execute()
        )
        return result.data or []

    async def request_response_times(self, since_iso: str) -> list[dict[str, Any]]:
        client = await self._client()
        result = await (
            client.table("api_request_logs")
            .select("response_time_ms")
            .gte("timestamp", since_iso)
            .execute()
        )
        return result.data or []

    async def app_log_count_by_levels(self, levels: list[str], since_iso: str) -> int:
        client = await self._client()
        result = await (
            client.table("application_logs")
            .select("id", count="exact")
            .in_("level", levels)
            .gte("logged_at", since_iso)
            .execute()
        )
        return result.count or 0

    async def app_log_count_by_level(self, level: str, since_iso: str) -> int:
        client = await self._client()
        result = await (
            client.table("application_logs")
            .select("id", count="exact")
            .eq("level", level)
            .gte("logged_at", since_iso)
            .execute()
        )
        return result.count or 0
