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
from sqlalchemy import delete as sa_delete
from sqlalchemy import select
from sqlalchemy import update as sa_update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.core.secret_box import decrypt as _decrypt_secret
from app.core.secret_box import encrypt as _encrypt_secret
from app.db.session import read_scope, write_scope
from app.models import UserMcpServers


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


def _row_to_dict(row: UserMcpServers) -> dict:
    """Convert an ORM row to the dict shape expected by ``UserMCPServer.from_row``.

    Passes the *encrypted* bearer_token through unchanged — ``from_row``
    handles decryption. ``id`` and ``user_id`` are passed as ``str()`` so
    ``from_row``'s ``UUID(str(...))`` wrapping is idempotent.
    """
    return {
        "id": str(row.id),
        "user_id": str(row.user_id),
        "name": row.name,
        "url": row.url,
        "bearer_token": row.bearer_token,  # encrypted ciphertext; from_row decrypts
        "description": row.description,
        "enabled": row.enabled,
    }


class UserMCPServersRepository:
    """SQLAlchemy 2.0 repository for ``user_mcp_servers`` (migration 194 / 263).

    STRATEGY-C VALUE-TYPE PARITY: the public return shape is the frozen
    dataclass ``UserMCPServer`` with ``id`` / ``user_id`` as ``uuid.UUID``
    OBJECTS. Reads route through ``UserMCPServer.from_row`` (which calls
    ``UUID(str(...))``), so the ORM row's ``uuid.UUID`` columns round-trip safely.

    SECRETS / P7: ``bearer_token`` is ENCRYPTED at every write (``create`` /
    ``update``) via ``_encrypt_secret`` and DECRYPTED at every read via
    ``UserMCPServer.from_row`` (``_decrypt_secret``). Plaintext never reaches
    the DB write path.

    M3 DEFENSE-IN-DEPTH: ``update`` / ``delete`` add a SQL-level
    ``WHERE user_id = :owner_user_id`` filter so an accidental call bypassing
    the endpoint-layer ownership check cannot cross-mutate another user's row.

    Writes COMMIT via ``write_scope()``. Reads use ``read_scope()``.
    """

    TABLE = "user_mcp_servers"

    async def list_for_user(
        self,
        user_id: UUID,
        *,
        only_enabled: bool = True,
    ) -> List[UserMCPServer]:
        """Return all server rows for a user, ordered by name. Default: only
        enabled. On any error (table missing, connection failure) returns
        ``[]``."""
        try:
            stmt = (
                select(UserMcpServers)
                .where(UserMcpServers.user_id == str(user_id))
                .order_by(UserMcpServers.name)
            )
            if only_enabled:
                stmt = stmt.where(UserMcpServers.enabled.is_(True))

            async with read_scope() as session:
                result = await session.execute(stmt)
                rows = result.scalars().all()

            return [UserMCPServer.from_row(_row_to_dict(r)) for r in rows]
        except Exception as exc:
            logger.warning(
                f"[UserMCPServersRepo] list_for_user({user_id}) failed: {exc}"
            )
            return []

    async def get_by_id(self, server_id: UUID) -> Optional[UserMCPServer]:
        """Return a single row by primary key, or ``None`` on miss/error."""
        try:
            async with read_scope() as session:
                result = await session.execute(
                    select(UserMcpServers).where(UserMcpServers.id == str(server_id))
                )
                row = result.scalars().first()

            if row is None:
                return None
            return UserMCPServer.from_row(_row_to_dict(row))
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
        """Insert a new server row. bearer_token is ENCRYPTED before storage.

        Raises on failure (logs + re-raises so the router can convert to a
        409/500)."""
        try:
            # P7: encrypt at write — None is a valid (no-auth) value
            stored_token = _encrypt_secret(bearer_token) if bearer_token else None

            stmt = (
                pg_insert(UserMcpServers)
                .values(
                    user_id=str(user_id),
                    name=name,
                    url=url,
                    bearer_token=stored_token,
                    description=description,
                    enabled=enabled,
                )
                .returning(
                    UserMcpServers.id,
                    UserMcpServers.user_id,
                    UserMcpServers.name,
                    UserMcpServers.url,
                    UserMcpServers.bearer_token,
                    UserMcpServers.description,
                    UserMcpServers.enabled,
                )
            )

            async with write_scope() as session:
                result = await session.execute(stmt)
                row_mapping = result.mappings().first()

            if not row_mapping:
                return None
            return UserMCPServer.from_row(dict(row_mapping))
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
        """Update a server. Returns ``False`` if no fields were provided.

        M3 defense-in-depth: the UPDATE carries a ``WHERE user_id = owner``
        filter at SQL level so a cross-user mutation is impossible even if the
        endpoint-layer ownership check is skipped. bearer_token is ENCRYPTED at
        write via ``_encrypt_secret``."""
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
            stmt = (
                sa_update(UserMcpServers)
                .where(UserMcpServers.id == str(server_id))
                .where(
                    UserMcpServers.user_id == str(owner_user_id)
                )  # M3: defensive filter
                .values(**patch)
            )
            async with write_scope() as session:
                await session.execute(stmt)
            return True
        except Exception as exc:
            logger.warning(f"[UserMCPServersRepo] update({server_id}) failed: {exc}")
            return False

    async def delete(self, server_id: UUID, *, owner_user_id: UUID) -> bool:
        """Delete a server row. Returns ``True`` on clean execution.

        M3 defense-in-depth: the DELETE carries a ``WHERE user_id = owner``
        filter so an accidental cross-user delete is impossible at SQL level."""
        try:
            stmt = (
                sa_delete(UserMcpServers)
                .where(UserMcpServers.id == str(server_id))
                .where(
                    UserMcpServers.user_id == str(owner_user_id)
                )  # M3: defensive filter
            )
            async with write_scope() as session:
                await session.execute(stmt)
            return True
        except Exception as exc:
            logger.warning(f"[UserMCPServersRepo] delete({server_id}) failed: {exc}")
            return False


def get_user_mcp_servers_repository() -> "UserMCPServersRepository":
    """Return the UserMCPServersRepository (SQLAlchemy 2.0, ORM-only).

    The legacy supabase-py REST path and the ``USE_ORM_USER_MCP_SERVERS`` flag
    were retired post-rollout; prod runs 100% ORM. Kept as a factory so call
    sites remain import-stable.
    """
    return UserMCPServersRepository()


__all__ = [
    "UserMCPServer",
    "UserMCPServersRepository",
    "get_user_mcp_servers_repository",
]
