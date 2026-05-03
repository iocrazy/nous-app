"""G1+G5 — Repository for user_mcp_servers (migration 194).

CRUD on per-user MCP server registrations. Used by:
  - ai_library_chat_wiring: build MCPOutboundRegistry from enabled rows
    at chat start
  - admin / user UI: list / add / update / delete servers (follow-up)
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional
from uuid import UUID

from loguru import logger

from app.db.supabase_client import get_async_supabase_admin


@dataclass(frozen=True)
class UserMCPServer:
    id: UUID
    user_id: UUID
    name: str
    url: str
    bearer_token: Optional[str]
    description: Optional[str]
    enabled: bool

    @classmethod
    def from_row(cls, row: dict) -> "UserMCPServer":
        return cls(
            id=UUID(str(row["id"])),
            user_id=UUID(str(row["user_id"])),
            name=row["name"],
            url=row["url"],
            bearer_token=row.get("bearer_token"),
            description=row.get("description"),
            enabled=bool(row.get("enabled", True)),
        )


class UserMCPServersRepository:
    TABLE = "user_mcp_servers"

    async def _client(self):
        return await get_async_supabase_admin()

    async def list_for_user(
        self,
        user_id: UUID,
        *,
        only_enabled: bool = True,
    ) -> List[UserMCPServer]:
        """Return all server rows for a user. Default: only enabled."""
        try:
            client = await self._client()
            q = (
                client.table(self.TABLE)
                .select("*")
                .eq("user_id", str(user_id))
                .order("name")
            )
            if only_enabled:
                q = q.eq("enabled", True)
            result = await q.execute()
            return [UserMCPServer.from_row(r) for r in (result.data or [])]
        except Exception as exc:
            logger.warning(
                f"[UserMCPServersRepo] list_for_user({user_id}) failed: {exc}"
            )
            return []

    async def get_by_id(self, server_id: UUID) -> Optional[UserMCPServer]:
        try:
            client = await self._client()
            result = (
                await client.table(self.TABLE)
                .select("*")
                .eq("id", str(server_id))
                .maybe_single()
                .execute()
            )
            if not result or not result.data:
                return None
            return UserMCPServer.from_row(result.data)
        except Exception as exc:
            logger.warning(f"[UserMCPServersRepo] get_by_id failed: {exc}")
            return None

    async def create(
        self,
        *,
        user_id: UUID,
        name: str,
        url: str,
        bearer_token: Optional[str] = None,
        description: Optional[str] = None,
        enabled: bool = True,
    ) -> Optional[UserMCPServer]:
        try:
            client = await self._client()
            result = (
                await client.table(self.TABLE)
                .insert({
                    "user_id": str(user_id),
                    "name": name,
                    "url": url,
                    "bearer_token": bearer_token,
                    "description": description,
                    "enabled": enabled,
                })
                .execute()
            )
            if not result.data:
                return None
            return UserMCPServer.from_row(result.data[0])
        except Exception as exc:
            logger.warning(f"[UserMCPServersRepo] create failed: {exc}")
            raise

    async def update(
        self,
        server_id: UUID,
        *,
        url: Optional[str] = None,
        bearer_token: Optional[str] = None,
        description: Optional[str] = None,
        enabled: Optional[bool] = None,
    ) -> bool:
        patch: dict = {}
        if url is not None:
            patch["url"] = url
        if bearer_token is not None:
            patch["bearer_token"] = bearer_token
        if description is not None:
            patch["description"] = description
        if enabled is not None:
            patch["enabled"] = enabled
        if not patch:
            return False
        try:
            client = await self._client()
            await (
                client.table(self.TABLE)
                .update(patch)
                .eq("id", str(server_id))
                .execute()
            )
            return True
        except Exception as exc:
            logger.warning(f"[UserMCPServersRepo] update failed: {exc}")
            return False

    async def delete(self, server_id: UUID) -> bool:
        try:
            client = await self._client()
            await (
                client.table(self.TABLE)
                .delete()
                .eq("id", str(server_id))
                .execute()
            )
            return True
        except Exception as exc:
            logger.warning(f"[UserMCPServersRepo] delete failed: {exc}")
            return False


__all__ = ["UserMCPServer", "UserMCPServersRepository"]
