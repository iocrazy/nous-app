"""Repository for admin credits/points management.

Scoped to package + pricing config operations. Stats/reads/transactions
remain in the router for now because they mix business logic with
direct queries and are staged for later migration.
"""

from __future__ import annotations

from typing import Any, Optional

from app.db import get_async_supabase_admin


class AdminCreditsRepository:
    PACKAGES_TABLE = "point_packages"
    PRICING_TABLE = "point_pricing"
    TRANSACTIONS_TABLE = "point_transactions"
    ORDERS_TABLE = "orders"
    TEAMS_TABLE = "teams"

    async def _client(self):
        return await get_async_supabase_admin()

    # ─── Packages ──────────────────────────────────────────────────

    async def list_packages(self) -> list[dict[str, Any]]:
        client = await self._client()
        result = (
            await client.table(self.PACKAGES_TABLE)
            .select("*")
            .order("sort_order", desc=False)
            .execute()
        )
        return result.data or []

    async def create_package(
        self, payload: dict[str, Any]
    ) -> Optional[dict[str, Any]]:
        client = await self._client()
        result = (
            await client.table(self.PACKAGES_TABLE)
            .insert(payload)
            .execute()
        )
        return (result.data or [None])[0]

    async def update_package(
        self, package_id: str, payload: dict[str, Any]
    ) -> Optional[dict[str, Any]]:
        client = await self._client()
        result = (
            await client.table(self.PACKAGES_TABLE)
            .update(payload)
            .eq("id", package_id)
            .execute()
        )
        return (result.data or [None])[0]

    async def delete_package(self, package_id: str) -> None:
        client = await self._client()
        await (
            client.table(self.PACKAGES_TABLE)
            .delete()
            .eq("id", package_id)
            .execute()
        )

    # ─── Pricing ───────────────────────────────────────────────────

    async def list_pricing(self) -> list[dict[str, Any]]:
        client = await self._client()
        result = (
            await client.table(self.PRICING_TABLE)
            .select("*")
            .order("action_type", desc=False)
            .execute()
        )
        return result.data or []

    async def update_pricing(
        self, action_type: str, payload: dict[str, Any]
    ) -> Optional[dict[str, Any]]:
        client = await self._client()
        result = (
            await client.table(self.PRICING_TABLE)
            .update(payload)
            .eq("action_type", action_type)
            .execute()
        )
        return (result.data or [None])[0]

    # ─── Transactions ──────────────────────────────────────────────

    async def list_transactions(
        self,
        *,
        team_id: Optional[str],
        type: Optional[str],
        sort_by: str,
        sort_desc: bool,
        offset: int,
        limit: int,
    ) -> tuple[list[dict[str, Any]], int]:
        client = await self._client()
        query = client.table(self.TRANSACTIONS_TABLE).select("*", count="exact")
        if team_id:
            query = query.eq("team_id", team_id)
        if type:
            query = query.eq("type", type)
        query = query.order(sort_by, desc=sort_desc).range(
            offset, offset + limit - 1
        )
        result = await query.execute()
        return result.data or [], result.count or 0

    # ─── Orders ────────────────────────────────────────────────────

    async def list_orders(
        self,
        *,
        payment_status: Optional[str],
        payment_method: Optional[str],
        team_id: Optional[str],
        sort_by: str,
        sort_desc: bool,
        offset: int,
        limit: int,
    ) -> tuple[list[dict[str, Any]], int]:
        client = await self._client()
        query = client.table(self.ORDERS_TABLE).select("*", count="exact")
        if payment_status:
            query = query.eq("payment_status", payment_status)
        if payment_method:
            query = query.eq("payment_method", payment_method)
        if team_id:
            query = query.eq("team_id", team_id)
        query = query.order(sort_by, desc=sort_desc).range(
            offset, offset + limit - 1
        )
        result = await query.execute()
        return result.data or [], result.count or 0

    # ─── Enrichment helpers ────────────────────────────────────────

    async def get_teams_by_ids(
        self, team_ids: list[str]
    ) -> list[dict[str, Any]]:
        if not team_ids:
            return []
        client = await self._client()
        result = (
            await client.table(self.TEAMS_TABLE)
            .select("id, name, is_personal, owner_id")
            .in_("id", team_ids)
            .execute()
        )
        return result.data or []

    async def get_package_names(
        self, package_ids: list[str]
    ) -> dict[str, str]:
        if not package_ids:
            return {}
        client = await self._client()
        result = (
            await client.table(self.PACKAGES_TABLE)
            .select("id, name")
            .in_("id", package_ids)
            .execute()
        )
        return {str(p["id"]): p["name"] for p in (result.data or [])}

    # ─── Stats aggregates ──────────────────────────────────────────

    async def all_quotas_balances(self) -> list[dict[str, Any]]:
        client = await self._client()
        result = (
            await client.table("team_quotas").select("points_balance").execute()
        )
        return result.data or []

    async def transactions_by_type(
        self, type_val: str, columns: str = "amount"
    ) -> list[dict[str, Any]]:
        client = await self._client()
        result = (
            await client.table(self.TRANSACTIONS_TABLE)
            .select(columns)
            .eq("type", type_val)
            .execute()
        )
        return result.data or []

    async def orders_by_status(
        self,
        *,
        payment_status: str,
        columns: str = "amount_cents",
        since_iso: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        client = await self._client()
        query = (
            client.table(self.ORDERS_TABLE)
            .select(columns)
            .eq("payment_status", payment_status)
        )
        if since_iso:
            query = query.gte("paid_at", since_iso)
        result = await query.execute()
        return result.data or []

    async def teams_count(self) -> int:
        client = await self._client()
        result = (
            await client.table(self.TEAMS_TABLE)
            .select("id", count="exact")
            .execute()
        )
        return result.count or 0

    async def orders_count_by_status(self, payment_status: str) -> int:
        client = await self._client()
        result = (
            await client.table(self.ORDERS_TABLE)
            .select("id", count="exact")
            .eq("payment_status", payment_status)
            .execute()
        )
        return result.count or 0

    async def revenue_chart_rows(self, since_iso: str) -> list[dict[str, Any]]:
        client = await self._client()
        result = (
            await client.table(self.ORDERS_TABLE)
            .select("paid_at, amount_cents, points_amount")
            .eq("payment_status", "paid")
            .gte("paid_at", since_iso)
            .order("paid_at", desc=False)
            .execute()
        )
        return result.data or []
