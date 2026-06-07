"""SQLAlchemy 2.0 ORM implementation of UserMCPServersRepository (deferred-repo finish wave).

REST → ORM successor for the ``user_mcp_servers`` surface, following the
validated ``AgentRepositoryOrm`` pilot template. ``UserMCPServersRepositoryOrm``
subclasses ``UserMCPServersRepository`` and overrides all data methods. Call
sites route through ``get_user_mcp_servers_repository()`` in the base module.

STRATEGY-C VALUE-TYPE PARITY
===========================
The public return shape is the **frozen dataclass ``UserMCPServer``** (NOT a
SELECT-* dict). Both ``id`` and ``user_id`` are ``uuid.UUID`` OBJECTS in the
dataclass. Strategy-C is handled by routing all reads through
``UserMCPServer.from_row({...})`` exactly as REST does — ``from_row`` calls
``UUID(str(...))`` on the id/user_id attributes so there is no mismatch:

  - ``row.id`` / ``row.user_id`` → ``uuid.UUID`` (SQLAlchemy Uuid column) →
    ``str(row.id)`` → ``UUID(str(...))`` inside ``from_row`` — round-trip safe.
  - ``bearer_token`` → TEXT nullable → str | None — the *encrypted* ciphertext
    is stored in the DB; ``from_row`` calls ``_decrypt_secret()`` on read.
    Never returned as plaintext from the DB layer.
  - ``enabled`` → Boolean → bool. ``name``, ``url``, ``description`` → TEXT →
    str | None. No datetime values appear in the public return shape.

SECRETS / P7 COMPLIANCE
=======================
bearer_token is ENCRYPTED at every write path (``create`` and ``update``) via
``_encrypt_secret`` imported from ``app.core.secret_box``. It is DECRYPTED at
every read path via ``UserMCPServer.from_row`` (which calls ``_decrypt_secret``
internally and handles the legacy plain-text back-compat case). Under no
circumstances is a plaintext token passed to SQLAlchemy's write path.

M3 DEFENSE-IN-DEPTH
===================
``update`` and ``delete`` both carry ``owner_user_id`` and add a SQL-level
``WHERE user_mcp_servers.user_id = :owner_user_id`` filter so an accidental
call that bypasses the endpoint-layer ownership check cannot cross-mutate
another user's row. This mirrors the REST impl's ``.eq("user_id", owner)``
chain exactly.

Writes COMMIT via ``write_scope()``. Reads use ``read_scope()``.

⚠️ INTEGRATION-PENDING: ``UserMcpServers`` was hand-derived from migration 194's
DDL (no live reflection was available when it was authored). Validate by running
``tests/integration/test_user_mcp_servers_repository_orm.py`` against prod
(set ``INTEGRATION_DATABASE_URL``) before flipping ``USE_ORM_USER_MCP_SERVERS``.
"""

from __future__ import annotations

from typing import List, Optional
from uuid import UUID

from loguru import logger
from sqlalchemy import delete as sa_delete
from sqlalchemy import select
from sqlalchemy import update as sa_update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.core.secret_box import encrypt as _encrypt_secret
from app.db.session import read_scope, write_scope
from app.models import UserMcpServers
from app.repositories.user_mcp_servers_repository import (
    UserMCPServer,
    UserMCPServersRepository,
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


class UserMCPServersRepositoryOrm(UserMCPServersRepository):
    """ORM-backed UserMCPServersRepository for user_mcp_servers.

    Subclasses the REST repo so call sites that hold a reference to the
    parent type still satisfy isinstance checks. Every data method is
    overridden; none of the supabase-py helpers are reachable at runtime.
    """

    async def list_for_user(
        self,
        user_id: UUID,
        *,
        only_enabled: bool = True,
    ) -> List[UserMCPServer]:
        """Return all server rows for a user, ordered by name.

        On any error (table missing, connection failure) returns ``[]``,
        mirroring the REST fallback.
        """
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
                f"[UserMCPServersRepoOrm] list_for_user({user_id}) failed: {exc}"
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
            logger.warning(
                f"[UserMCPServersRepoOrm] get_by_id({server_id}) failed: {exc}"
            )
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

        Raises on failure (mirrors REST ``create`` which logs + re-raises so
        the router can convert to a 409/500).
        """
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
            logger.warning(f"[UserMCPServersRepoOrm] create failed: {exc}")
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
        filter at SQL level so a cross-user mutation is impossible even if
        the endpoint-layer ownership check is skipped.

        bearer_token is ENCRYPTED at write via ``_encrypt_secret``.
        """
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
            logger.warning(f"[UserMCPServersRepoOrm] update({server_id}) failed: {exc}")
            return False

    async def delete(self, server_id: UUID, *, owner_user_id: UUID) -> bool:
        """Delete a server row. Returns ``True`` on clean execution.

        M3 defense-in-depth: the DELETE carries a ``WHERE user_id = owner``
        filter so an accidental cross-user delete is impossible at SQL level.
        """
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
            logger.warning(f"[UserMCPServersRepoOrm] delete({server_id}) failed: {exc}")
            return False


__all__ = ["UserMCPServersRepositoryOrm"]
