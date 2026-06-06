"""SQLAlchemy 2.0 ORM impl of AdminTablePreferencesRepository (admin wave).

REST → ORM successor for ``admin_table_preferences`` (Notion-style per-user table
config). ``AdminTablePreferencesRepositoryOrm`` subclasses
``AdminTablePreferencesRepository`` and overrides every data method; the
``TABLE`` / ``COLUMNS`` constants are inherited. Call sites route through
``get_admin_table_preferences_repository()`` (bottom of
``table_preferences_repository.py``).

MODEL: ``app.models.AdminTablePreferences`` (table ``admin_table_preferences``)
— verified reflected.

★ UUID AUDIT ★
==============
The table HAS two uuid columns (``id`` PK, ``user_id``), BUT the read projection
(``COLUMNS = "table_key, filters, sorts, visible_columns, column_order"``) selects
NEITHER. So NO uuid is ever returned → NO uuid coercion is needed. ``user_id`` is
INPUT-only (a str passed from the router, used in the WHERE / upsert payload).

COLUMN-SUBSET SELECTS + STRATEGY-C PARITY
-----------------------------------------
``get`` / ``upsert`` return ONLY the 5 projected columns (matching the legacy):
  table_key (text) → native str.
  filters / sorts (jsonb) → native dict/list (REST returned parsed objects).
  visible_columns / column_order (text[]) → native list[str].
There is NO timestamptz and NO uuid in the projection, so no value-type coercion
is performed (and NO date-range filter exists anywhere in this repo).

WRITE PATHS (the silent-rollback P0 lesson)
===========================================
  upsert(...) reproduces the legacy ``.upsert(payload, on_conflict="user_id,
    table_key")`` via PostgreSQL ``insert(...).on_conflict_do_update(index_elements
    =["user_id","table_key"], set_=...)`` inside ``write_scope()`` (COMMITS). The
    payload binds user_id / table_key / filters / sorts / visible_columns /
    column_order — ALL real mapped columns (no phantom). ``id`` / ``created_at`` /
    ``updated_at`` are left to their server defaults. Returns the 5-column
    projection of the upserted row (or None — REST-contract parity).
  delete(user_id, table_key) DELETEs the matching row inside ``write_scope()``
    (COMMITS) and returns None (the legacy returned None too).

Model-quirk scan: no SQLAlchemy Enum column, no renamed column on this model.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from sqlalchemy import delete as sa_delete
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.db.session import read_scope, write_scope
from app.models import AdminTablePreferences
from app.repositories.admin.table_preferences_repository import (
    AdminTablePreferencesRepository,
)

# The exact 5-column projection the legacy returned (COLUMNS constant), in order.
_PROJECTION = (
    AdminTablePreferences.table_key,
    AdminTablePreferences.filters,
    AdminTablePreferences.sorts,
    AdminTablePreferences.visible_columns,
    AdminTablePreferences.column_order,
)
_PROJECTION_KEYS = (
    "table_key",
    "filters",
    "sorts",
    "visible_columns",
    "column_order",
)


def _project(row: Any) -> Dict[str, Any]:
    """Build the 5-key projection dict from a result Row (keyed exactly like the
    legacy COLUMNS select). No uuid / no timestamptz in the projection → no
    value-type coercion needed (jsonb → dict/list, text[] → list[str] natively)."""
    return {key: getattr(row, key) for key in _PROJECTION_KEYS}


class AdminTablePreferencesRepositoryOrm(AdminTablePreferencesRepository):
    """ORM-backed AdminTablePreferencesRepository (Notion-style table config)."""

    async def get(self, user_id: str, table_key: str) -> Optional[dict[str, Any]]:
        """The 5-column projection for (user_id, table_key), or None."""
        stmt = (
            select(*_PROJECTION)
            .where(AdminTablePreferences.user_id == user_id)
            .where(AdminTablePreferences.table_key == table_key)
            .limit(1)
        )
        async with read_scope() as session:
            result = await session.execute(stmt)
            row = result.first()
        return _project(row) if row else None

    async def upsert(
        self,
        user_id: str,
        table_key: str,
        filters: List[dict[str, Any]],
        sorts: List[dict[str, Any]],
        visible_columns: list[str] | None,
        column_order: list[str] | None,
    ) -> Optional[dict[str, Any]]:
        """Insert-or-update on (user_id, table_key); COMMITS via write_scope().
        Returns the 5-column projection of the upserted row (or None)."""
        payload = {
            "user_id": user_id,
            "table_key": table_key,
            "filters": filters,
            "sorts": sorts,
            "visible_columns": visible_columns,
            "column_order": column_order,
        }
        stmt = (
            pg_insert(AdminTablePreferences)
            .values(**payload)
            .on_conflict_do_update(
                index_elements=["user_id", "table_key"],
                set_={
                    "filters": filters,
                    "sorts": sorts,
                    "visible_columns": visible_columns,
                    "column_order": column_order,
                },
            )
            .returning(*_PROJECTION)
        )
        async with write_scope() as session:
            result = await session.execute(stmt)
            row = result.first()
            out = _project(row) if row else None
        return out

    async def delete(self, user_id: str, table_key: str) -> None:
        """Delete the (user_id, table_key) row; COMMITS via write_scope()."""
        async with write_scope() as session:
            await session.execute(
                sa_delete(AdminTablePreferences)
                .where(AdminTablePreferences.user_id == user_id)
                .where(AdminTablePreferences.table_key == table_key)
            )


__all__ = ["AdminTablePreferencesRepositoryOrm"]
