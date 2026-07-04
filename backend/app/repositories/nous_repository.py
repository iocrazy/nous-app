# backend/app/repositories/nous_repository.py

"""Repository for nous_models table — admin-configured platform AI models.

ORM 2.0 (Phase 2, M batch): ``NousRepository`` is the SQLAlchemy 2.0
implementation — reads go through ``read_scope()`` and writes through
``write_scope()``; row objects are converted to SELECT *-shaped dicts by the
``_parity`` value-type sweep. Call sites route through ``get_nous_repository()``
(bottom of this file). The legacy supabase-py REST branch and the
``USE_ORM_NOUS`` flag were retired post-rollout (prod runs 100% ORM).

UPDATED_AT sentinel
-------------------
The legacy ``update`` injected ``{"updated_at": "now()"}`` — a PostgREST
server-default trigger string. Binding the literal string "now()" to a DateTime
column via asyncpg would raise (invalid input syntax for timestamptz), so
``update`` drops the sentinel from ``values()`` and instead sets
``updated_at=func.now()`` (a real SQL ``now()`` call) — the row's updated_at
advances to the DB clock on update.

STRATEGY C — VALUE-TYPE PARITY (per-field, exact REST shape)
============================================================
REST rendered JSON: bigint → int, timestamptz → ISO str, numeric → str. The ORM
returns native types. nous_models has NO uuid columns and NO jsonb columns, so
the parity surface is small:

  id (BIGINT snowflake PK) → STAYS NATIVE int (the 5.3 trap): every consumer
    (admin nous_router._to_response, ai_router, ai_settings_router) reads
    ``str(row["id"])`` or passes id straight to a response model — no consumer
    does ``int(id)`` math or a type-sensitive ``==``.

  created_at / updated_at (timestamptz) → ``.isoformat()`` ALWAYS: admin
    nous_router does ``str(row.get("created_at", ""))`` — ``str()`` on an ISO
    string is a no-op (parity).

  pricing_value (Numeric pricing/cost column) → LEFT NATIVE (Decimal): every
    consumer wraps it in ``float(row["pricing_value"])`` before any arithmetic,
    and ``float(x)`` works on both a REST str and a native Decimal.

  pricing_type (Text) → native str; is_enabled (bool) / sort_order (int) →
    native, passed straight to the response.

There are NO date/timestamptz RANGE filters in this repo (every query filters by
equality on name/type/is_enabled and orders by sort_order), so there is no
timestamptz<VARCHAR binding hazard. Writes commit via ``write_scope()`` (the
silent-rollback P0 lesson); reads use ``read_scope()``. Error handling: list_*
reads swallow + return []; get_by_name swallows + returns None; create/update
swallow + return None (NOT re-raise); delete swallows + returns False.
"""

from __future__ import annotations

import datetime as _dt
import uuid as _uuid
from typing import Any, Dict, List, Optional

from loguru import logger
from sqlalchemy import delete, func, insert, select, update

from app.db.session import read_scope, write_scope
from app.models import NousModels
from app.repositories._orm_helpers import _name_to_attr, _orm_obj_to_dict

_NOUS_N2A: Dict[str, str] = _name_to_attr(NousModels)
_NOUS_ATTRS = {p.key for p in NousModels.__mapper__.column_attrs}

# Public columns exposed by list_enabled (no api_key / app_id / base_url).
_PUBLIC_COLS = (
    NousModels.id,
    NousModels.name,
    NousModels.display_name,
    NousModels.type,
    NousModels.description,
    NousModels.pricing_type,
    NousModels.pricing_value,
    NousModels.sort_order,
)


def _parity(out: Dict[str, Any]) -> Dict[str, Any]:
    """Strategy-C value-type parity IN PLACE on a SELECT *-shaped dict:
    datetime → ISO str (REST shape); bigint id + Numeric pricing_value LEFT
    NATIVE (the 5.3 trap + the float()-tolerant numeric decision). uuid → str
    (defensive; nous_models has none today). NULLs pass through."""
    for key, value in out.items():
        if isinstance(value, _uuid.UUID):
            out[key] = str(value)
        elif isinstance(value, _dt.datetime):
            out[key] = value.isoformat()
        elif isinstance(value, _dt.date):
            out[key] = value.isoformat()
    return out


def _row(obj: Any) -> Dict[str, Any]:
    """SELECT *-shaped, strategy-C-parity dict for one full ORM row."""
    return _parity(_orm_obj_to_dict(obj, _NOUS_N2A))


class NousRepository:
    """CRUD for nous_models table (SQLAlchemy 2.0 ORM)."""

    TABLE = "nous_models"

    # ------------------------------------------------------------------
    # Public queries (no API keys exposed)
    # ------------------------------------------------------------------

    async def list_enabled(
        self, type_filter: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """List enabled Nous models, optionally filtered by model type.

        Returns public fields only (no api_key, app_id, base_url).
        """
        try:
            stmt = (
                select(*_PUBLIC_COLS)
                .where(NousModels.is_enabled.is_(True))
                .order_by(NousModels.sort_order)
            )
            if type_filter:
                stmt = stmt.where(NousModels.type == type_filter)
            async with read_scope() as session:
                result = await session.execute(stmt)
                # Partial-column SELECT → mappings() gives DB-column-keyed rows.
                return [_parity(dict(m)) for m in result.mappings().all()]
        except Exception as e:
            logger.error(f"Failed to list enabled nous models: {e}")
            return []

    async def get_by_name(self, name: str) -> Optional[Dict[str, Any]]:
        """Get a Nous model by name (includes all fields for backend use)."""
        try:
            async with read_scope() as session:
                result = await session.execute(
                    select(NousModels).where(NousModels.name == name).limit(1)
                )
                row = result.scalars().first()
                return _row(row) if row else None
        except Exception as e:
            logger.error(f"Failed to get nous model '{name}': {e}")
            return None

    # ------------------------------------------------------------------
    # Admin CRUD
    # ------------------------------------------------------------------

    async def list_all(self) -> List[Dict[str, Any]]:
        """List all Nous models (admin, includes disabled)."""
        try:
            async with read_scope() as session:
                result = await session.execute(
                    select(NousModels).order_by(NousModels.sort_order)
                )
                return [_row(r) for r in result.scalars().all()]
        except Exception as e:
            logger.error(f"Failed to list all nous models: {e}")
            return []

    async def create(self, data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Create a new Nous model."""
        try:
            values = {k: v for k, v in data.items() if k in _NOUS_ATTRS}
            async with write_scope() as session:
                result = await session.execute(
                    insert(NousModels).values(**values).returning(NousModels)
                )
                row = result.scalars().first()
                return _row(row) if row else None
        except Exception as e:
            logger.error(f"Failed to create nous model: {e}")
            return None

    async def update(
        self, model_id: str, data: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """Update a Nous model."""
        try:
            # Drop the REST "now()" sentinel; set updated_at via SQL func.now()
            # (the legacy injected {"updated_at": "now()"} as a server-time
            # string — binding that literal would error on a DateTime column).
            values = {
                k: v for k, v in data.items() if k in _NOUS_ATTRS and k != "updated_at"
            }
            values["updated_at"] = func.now()
            async with write_scope() as session:
                result = await session.execute(
                    update(NousModels)
                    .where(NousModels.id == int(model_id))
                    .values(**values)
                    .returning(NousModels)
                )
                row = result.scalars().first()
                return _row(row) if row else None
        except Exception as e:
            logger.error(f"Failed to update nous model {model_id}: {e}")
            return None

    async def record_test_result(
        self, model_id: str, status: str, detail: str
    ) -> Optional[Dict[str, Any]]:
        """Persist the last connectivity-test result on the row.

        Distinct from ``update``: writes only the last_test_* columns + a
        server-side ``last_tested_at``, leaving ``updated_at`` untouched (a
        connectivity probe is not an edit).
        """
        try:
            async with write_scope() as session:
                result = await session.execute(
                    update(NousModels)
                    .where(NousModels.id == int(model_id))
                    .values(
                        last_test_status=status,
                        last_test_detail=detail,
                        last_tested_at=func.now(),
                    )
                    .returning(NousModels)
                )
                row = result.scalars().first()
                return _row(row) if row else None
        except Exception as e:
            logger.error(f"Failed to record test result for {model_id}: {e}")
            return None

    async def delete(self, model_id: str) -> bool:
        """Delete a Nous model."""
        try:
            async with write_scope() as session:
                await session.execute(
                    delete(NousModels).where(NousModels.id == int(model_id))
                )
            return True
        except Exception as e:
            logger.error(f"Failed to delete nous model {model_id}: {e}")
            return False


def get_nous_repository() -> NousRepository:
    """Return the NousRepository (SQLAlchemy 2.0 ORM, collapsed post-rollout)."""
    return NousRepository()
