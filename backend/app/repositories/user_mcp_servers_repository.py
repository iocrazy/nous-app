"""G1+G5 — Repository for user_mcp_servers (migration 194).

CRUD on per-user MCP server registrations. Used by:
  - ai_library_chat_wiring: build MCPOutboundRegistry from enabled rows
    at chat start
  - admin / user UI: list / add / update / delete servers (follow-up)

M3 SECURITY NOTE: this repo uses the service-role Supabase client which
bypasses Row Level Security. The RLS policies in migration 194 are
defense-in-depth only. The application layer is the actual access
control boundary:
  - list_for_user / get_by_id / get_by_id_for_user filter by user_id
    in the query
  - update / delete take an explicit owner_user_id parameter and add
    .eq("user_id", owner) so an accidental call without the endpoint's
    ownership check cannot cross-mutate
The router endpoints layer additional ownership checks on top.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional
from uuid import UUID

from loguru import logger

from app.core.secret_box import decrypt as _decrypt_secret
from app.core.secret_box import encrypt as _encrypt_secret
from app.db.supabase_client import get_async_supabase_admin


@dataclass(frozen=True)
class UserMCPServer:
    id: UUID
    user_id: UUID
    name: str
    url: str
    bearer_token: Optional[str]  # plain text in memory; encrypted at rest
    description: Optional[str]
    enabled: bool

    @classmethod
    def from_row(cls, row: dict) -> "UserMCPServer":
        """Decrypt bearer_token on read. Legacy plain-text values pass
        through unchanged (secret_box.decrypt handles back-compat)."""
        raw_token = row.get("bearer_token")
        try:
            decrypted = _decrypt_secret(raw_token) if raw_token else None
        except Exception as exc:
            logger.warning(
                f"[UserMCPServer.from_row] failed to decrypt bearer_token "
                f"for row {row.get('id')}: {exc}"
            )
            decrypted = None
        return cls(
            id=UUID(str(row["id"])),
            user_id=UUID(str(row["user_id"])),
            name=row["name"],
            url=row["url"],
            bearer_token=decrypted,
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
            # P7: encrypt at write — bearer_token is None-passthrough
            stored_token = _encrypt_secret(bearer_token) if bearer_token else None
            result = (
                await client.table(self.TABLE)
                .insert(
                    {
                        "user_id": str(user_id),
                        "name": name,
                        "url": url,
                        "bearer_token": stored_token,
                        "description": description,
                        "enabled": enabled,
                    }
                )
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
        owner_user_id: UUID,
        url: Optional[str] = None,
        bearer_token: Optional[str] = None,
        description: Optional[str] = None,
        enabled: Optional[bool] = None,
    ) -> bool:
        """Update a server. ``owner_user_id`` is REQUIRED — the SQL
        filter `.eq("user_id", owner)` ensures even an accidental call
        without endpoint-layer ownership check cannot mutate another
        user's row (M3 defense-in-depth)."""
        patch: dict = {}
        if url is not None:
            patch["url"] = url
        if bearer_token is not None:
            # P7: encrypt at write
            patch["bearer_token"] = (
                _encrypt_secret(bearer_token) if bearer_token else None
            )
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
                .eq("user_id", str(owner_user_id))  # M3: defensive filter
                .execute()
            )
            return True
        except Exception as exc:
            logger.warning(f"[UserMCPServersRepo] update failed: {exc}")
            return False

    async def delete(self, server_id: UUID, *, owner_user_id: UUID) -> bool:
        """Delete a server. Same defensive owner_user_id filter as
        update — accidental cross-user delete is impossible at the
        SQL level even if the caller forgets the ownership check."""
        try:
            client = await self._client()
            await (
                client.table(self.TABLE)
                .delete()
                .eq("id", str(server_id))
                .eq("user_id", str(owner_user_id))  # M3: defensive filter
                .execute()
            )
            return True
        except Exception as exc:
            logger.warning(f"[UserMCPServersRepo] delete failed: {exc}")
            return False


__all__ = ["UserMCPServer", "UserMCPServersRepository"]
