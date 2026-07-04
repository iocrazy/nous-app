# app/repositories/libraries_repository.py

"""Libraries Repository — data access layer for team libraries.

ORM 2.0 (Batch L1): ``LibrariesRepository`` is the SQLAlchemy 2.0
implementation — reads go through ``read_scope()`` and writes through
``write_scope()``; row objects are converted to SELECT *-shaped dicts by the
``_library_to_dict`` value-type sweep. Call sites route through
``get_libraries_repository()`` (bottom of this file). The legacy supabase-py
REST branch and the ``USE_ORM_LIBRARIES`` flag were retired post-rollout (prod
runs 100% ORM).

STRATEGY-C VALUE-TYPE PARITY (per-field, exact REST shape)
==========================================================
Supabase REST rendered ``uuid`` → STRING, ``bigint`` → int, ``timestamptz`` →
ISO string. The ORM returns native ``uuid.UUID`` / ``int`` / ``datetime``.

  libraries.id : bigint → STAYS native int (the 5.3 scope-zeroing trap — never
    str a bigint). REST returned an int.
  libraries.scope_id / scope_type : TEXT → native str (REST returned str). Note
    scope_id is the FK to ``teams.id`` rendered as TEXT in this table (the
    well-documented libraries ``scope_id text`` quirk); it is already a str both
    ways. No coercion. ⚠ There is NO ``team_id`` column — scope is the
    ``scope_type`` + ``scope_id`` pair.
  libraries.created_by : uuid → STR. The current consumer (libraries_router /
    permission_service) serializes the dict straight to an HTTP response and
    never reads created_by type-sensitively in Python — but we coerce for exact
    REST shape parity (cheap, future-proofs any UUID(...) / dict-key consumer).
  libraries.created_at / updated_at : timestamptz → ``.isoformat()`` ALWAYS
    (the unconditional template rule).
  name / visibility / icon / color (text), sort_order (int) → native.

Renamed-col/enum scan: ``Libraries`` has NEITHER a ``mapped_column("db")``
rename NOR any SQLAlchemy ``Enum`` column (it has DB-side CHECK constraints on
scope_type / visibility, but those are NOT PG/SQLAlchemy enum types). So
``_plain`` is NOT load-bearing; we route through ``_name_to_attr`` +
``_orm_obj_to_dict`` for mechanical parity / rename-safety.

REST-contract parity preserved: create/update return ``{}`` when no row comes
back (NOT a raise); delete returns ``True``. Writes commit via
``write_scope()`` (the silent-rollback P0 lesson).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from loguru import logger
from sqlalchemy import delete, insert, select, update

from app.db.session import read_scope, write_scope
from app.models import Libraries
from app.repositories._orm_helpers import _name_to_attr, _orm_obj_to_dict

# libraries DB-column-name → mapped-attribute-name (built once).
_LIBRARIES_NAME_TO_ATTR: Dict[str, str] = _name_to_attr(Libraries)

# uuid columns coerced to str at the dict boundary (REST-parity).
_LIBRARY_UUID_STR_COLS = ("created_by",)

# timestamptz columns ISO-coerced at the boundary (template rule).
_LIBRARY_TS_ISO_COLS = ("created_at", "updated_at")


def _library_to_dict(obj: Any) -> Dict[str, Any]:
    """SELECT *-shaped dict for a ``libraries`` ORM row with strategy-C parity:
    created_by uuid → str, created_at/updated_at → ISO str, bigint id stays
    native int, text scope_id/scope_type stay str. NULLs pass through."""
    out = _orm_obj_to_dict(obj, _LIBRARIES_NAME_TO_ATTR)
    for col in _LIBRARY_UUID_STR_COLS:
        val = out.get(col)
        if val is not None:
            out[col] = str(val)
    for col in _LIBRARY_TS_ISO_COLS:
        val = out.get(col)
        if isinstance(val, datetime):
            out[col] = val.isoformat()
    # Generic guard: any other timestamp column still gets ISO-coerced.
    for key, val in out.items():
        if isinstance(val, datetime):
            out[key] = val.isoformat()
    return out


class LibrariesRepository:
    """Library data access (async, SQLAlchemy 2.0 ORM)."""

    def __init__(self):
        pass

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    async def get_by_id(self, library_id: str) -> Optional[Dict[str, Any]]:
        """Fetch a library by id; returns None if not found."""
        try:
            async with read_scope() as session:
                result = await session.execute(
                    select(Libraries).where(Libraries.id == int(library_id)).limit(1)
                )
                row = result.scalars().first()
                return _library_to_dict(row) if row else None
        except Exception as e:
            logger.error(f"Failed to get library {library_id}: {e}")
            raise

    async def list_by_scope(
        self, scope_type: str, scope_id: str
    ) -> List[Dict[str, Any]]:
        """List libraries for a scope, ordered by sort_order then created_at
        (both ascending) — matches the REST ordering exactly."""
        try:
            async with read_scope() as session:
                result = await session.execute(
                    select(Libraries)
                    .where(Libraries.scope_type == scope_type)
                    .where(Libraries.scope_id == scope_id)
                    .order_by(Libraries.sort_order.asc(), Libraries.created_at.asc())
                )
                return [_library_to_dict(r) for r in result.scalars().all()]
        except Exception as e:
            logger.error(f"Failed to list libraries: {e}")
            raise

    # ------------------------------------------------------------------
    # Writes (COMMITTING via write_scope)
    # ------------------------------------------------------------------

    async def create(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Insert a library; returns the inserted row dict or {} if no row
        returned (REST-contract parity — does NOT raise on empty). Committing."""
        try:
            async with write_scope() as session:
                result = await session.execute(
                    insert(Libraries).values(**data).returning(Libraries)
                )
                row = result.scalars().first()
                out = _library_to_dict(row) if row else {}
            logger.info(f"Created library: {data.get('name')}")
            return out
        except Exception as e:
            logger.error(f"Failed to create library: {e}")
            raise

    async def update(self, library_id: str, data: Dict[str, Any]) -> Dict[str, Any]:
        """PATCH-style update; returns the updated row dict or {} if no match.
        Committing."""
        try:
            async with write_scope() as session:
                result = await session.execute(
                    update(Libraries)
                    .where(Libraries.id == int(library_id))
                    .values(**data)
                    .returning(Libraries)
                )
                row = result.scalars().first()
                return _library_to_dict(row) if row else {}
        except Exception as e:
            logger.error(f"Failed to update library {library_id}: {e}")
            raise

    async def delete(self, library_id: str) -> bool:
        """Hard-delete a library by id. Returns True. Committing."""
        try:
            async with write_scope() as session:
                await session.execute(
                    delete(Libraries).where(Libraries.id == int(library_id))
                )
            logger.info(f"Deleted library: {library_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to delete library {library_id}: {e}")
            raise


def get_libraries_repository() -> LibrariesRepository:
    """Return the LibrariesRepository (SQLAlchemy 2.0 ORM, collapsed post-rollout)."""
    return LibrariesRepository()
