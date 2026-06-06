"""Repository for admin team management."""

from __future__ import annotations

from typing import Any, Optional

from app.db import get_async_supabase_admin


class AdminTeamsRepository:
    TEAMS_TABLE = "teams"
    MEMBERS_TABLE = "team_members"
    QUOTAS_TABLE = "team_quotas"
    COLLECTIONS_TABLE = "collections"

    async def _client(self):
        return await get_async_supabase_admin()

    # ─── Teams ─────────────────────────────────────────────────────────

    async def list_teams(
        self,
        *,
        search: Optional[str],
        offset: int,
        limit: int,
    ) -> tuple[list[dict[str, Any]], int]:
        client = await self._client()
        query = client.table(self.TEAMS_TABLE).select("*", count="exact")
        if search:
            query = query.ilike("name", f"%{search}%")
        query = query.order("created_at", desc=True).range(offset, offset + limit - 1)
        result = await query.execute()
        return result.data or [], result.count or 0

    async def get(self, team_id: str) -> Optional[dict[str, Any]]:
        client = await self._client()
        result = (
            await client.table(self.TEAMS_TABLE)
            .select("*")
            .eq("id", team_id)
            .maybe_single()
            .execute()
        )
        return result.data if result else None

    async def update(self, team_id: str, changes: dict[str, Any]) -> None:
        client = await self._client()
        await client.table(self.TEAMS_TABLE).update(changes).eq("id", team_id).execute()

    async def delete(self, team_id: str) -> None:
        client = await self._client()
        await client.table(self.TEAMS_TABLE).delete().eq("id", team_id).execute()

    async def unlink_collections(self, team_id: str) -> None:
        client = await self._client()
        await (
            client.table(self.COLLECTIONS_TABLE)
            .update({"team_id": None})
            .eq("team_id", team_id)
            .execute()
        )

    # ─── Members ───────────────────────────────────────────────────────

    async def list_members(self, team_id: str) -> list[dict[str, Any]]:
        client = await self._client()
        result = (
            await client.table(self.MEMBERS_TABLE)
            .select("*")
            .eq("team_id", team_id)
            .order("joined_at", desc=False)
            .execute()
        )
        return result.data or []

    async def get_member(self, team_id: str, user_id: str) -> Optional[dict[str, Any]]:
        client = await self._client()
        result = (
            await client.table(self.MEMBERS_TABLE)
            .select("*")
            .eq("team_id", team_id)
            .eq("user_id", user_id)
            .maybe_single()
            .execute()
        )
        return result.data if result else None

    async def update_member_role(self, team_id: str, user_id: str, role: str) -> None:
        client = await self._client()
        await (
            client.table(self.MEMBERS_TABLE)
            .update({"role": role})
            .eq("team_id", team_id)
            .eq("user_id", user_id)
            .execute()
        )

    async def delete_member(self, team_id: str, user_id: str) -> None:
        client = await self._client()
        await (
            client.table(self.MEMBERS_TABLE)
            .delete()
            .eq("team_id", team_id)
            .eq("user_id", user_id)
            .execute()
        )

    # ─── Points balances ───────────────────────────────────────────────

    async def get_points_balance(self, team_id: str) -> int:
        client = await self._client()
        result = (
            await client.table(self.QUOTAS_TABLE)
            .select("points_balance")
            .eq("team_id", team_id)
            .maybe_single()
            .execute()
        )
        if not result or not result.data:
            return 0
        return result.data.get("points_balance", 0)

    async def batch_points_balances(self, team_ids: list[str]) -> dict[str, int]:
        if not team_ids:
            return {}
        client = await self._client()
        result = (
            await client.table(self.QUOTAS_TABLE)
            .select("team_id, points_balance")
            .in_("team_id", team_ids)
            .execute()
        )
        return {
            str(r["team_id"]): r.get("points_balance", 0) for r in (result.data or [])
        }


def get_admin_teams_repository() -> "AdminTeamsRepository":
    """Return the right AdminTeamsRepository implementation per env.

    ORM when ``USE_ORM_ADMIN_TEAMS`` is set AND the SQLAlchemy engine is
    configured; otherwise the legacy supabase-py REST path. A flag-on but
    engine-missing deploy logs once and falls back to REST (never crashes).
    """
    from app.core.config import settings

    if settings.USE_ORM_ADMIN_TEAMS:
        from app.db.engine import is_configured

        if is_configured():
            from app.repositories.admin.teams_repository_orm import (
                AdminTeamsRepositoryOrm,
            )

            return AdminTeamsRepositoryOrm()
        from loguru import logger

        logger.warning(
            "USE_ORM_ADMIN_TEAMS=true but SUPAVISOR_DATABASE_URL is empty "
            "— falling back to supabase-py path"
        )
    return AdminTeamsRepository()
