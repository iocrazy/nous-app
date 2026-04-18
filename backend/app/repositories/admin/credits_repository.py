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
