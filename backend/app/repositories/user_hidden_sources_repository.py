from __future__ import annotations

from loguru import logger
from sqlalchemy import delete as sa_delete
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.db.session import read_scope, write_scope
from app.models import UserHiddenSources


class UserHiddenSourcesRepository:
    """Per-user "close/hide" of signal sources.

    A hidden source keeps collecting globally; it's just excluded from this
    user's feed. Keyed by (user_id, source_id) — see migration 316.

    ORM-backed (read_scope/write_scope). ``UserHiddenSources`` carries no
    scope mixin: ownership is scoped explicitly by the ``user_id`` predicate
    in every method (the service-role/RLS-bypass model, unchanged), so the
    choke point stays inert.
    """

    TABLE = "user_hidden_sources"

    async def list_hidden_ids(self, user_id: str) -> list[str]:
        """Source ids this user has hidden (as strings, for set membership)."""
        async with read_scope() as session:
            result = await session.execute(
                select(UserHiddenSources.source_id).where(
                    UserHiddenSources.user_id == user_id
                )
            )
            return [str(sid) for sid in result.scalars().all()]

    async def hide(self, user_id: str, source_id: str) -> None:
        """Hide a source for this user (idempotent upsert)."""
        try:
            stmt = (
                pg_insert(UserHiddenSources)
                .values(user_id=user_id, source_id=int(source_id))
                .on_conflict_do_nothing(index_elements=["user_id", "source_id"])
            )
            async with write_scope() as session:
                await session.execute(stmt)
        except Exception as e:  # noqa: BLE001
            logger.error(f"hide source failed for {user_id}/{source_id}: {e}")
            raise

    async def unhide(self, user_id: str, source_id: str) -> None:
        """Un-hide a source for this user (idempotent — no-op if not hidden)."""
        try:
            async with write_scope() as session:
                await session.execute(
                    sa_delete(UserHiddenSources).where(
                        UserHiddenSources.user_id == user_id,
                        UserHiddenSources.source_id == int(source_id),
                    )
                )
        except Exception as e:  # noqa: BLE001
            logger.error(f"unhide source failed for {user_id}/{source_id}: {e}")
            raise
