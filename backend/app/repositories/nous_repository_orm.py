# app/repositories/nous_repository_orm.py

"""SQLAlchemy 2.0 ORM implementation of NousRepository (Phase 2, M batch).

REST → ORM successor for the ``nous_models`` table (admin-configured platform
AI models). Same Strangler-Fig single-inheritance pattern as the validated
projects/logs/storyboard migrations: ``NousRepositoryOrm`` subclasses
``NousRepository`` and overrides every DB method; the ``_get_client`` helper is
inherited but never reached on the ORM path. Call sites route through
``get_nous_repository()`` (bottom of ``nous_repository.py``).

PHANTOM-COLUMN PRE-FLIGHT
=========================
Two write paths, both pass-through dicts whose keys come from typed Pydantic
schemas (NousModelCreate / NousModelUpdate):

  create(data) : data = NousModelCreate.model_dump() → name / display_name /
                 category / actual_provider / actual_model / api_key /
                 pricing_type / pricing_value / is_enabled / sort_order /
                 app_id / base_url — ALL mapped columns on NousModels. OK.
  update(id,d) : data = NousModelUpdate.model_dump(exclude_none=True) + the repo
                 injects ``updated_at="now()"`` (a server-time sentinel STRING
                 under REST). Every NousModelUpdate field is a mapped column.
                 The injected ``updated_at`` sentinel is handled specially (see
                 UPDATED_AT note) — it is NOT bound as the literal string
                 "now()". No phantom columns.

UPDATED_AT sentinel
-------------------
The legacy ``update`` injects ``{"updated_at": "now()"}`` — a PostgREST
server-default trigger string. Binding the literal string "now()" to a
DateTime column via asyncpg would raise (invalid input syntax for timestamptz),
so the ORM override drops the sentinel from ``values()`` and instead sets
``updated_at=func.now()`` (a real SQL ``now()`` call) — reproducing the REST
behaviour exactly (the row's updated_at advances to the DB clock on update).

STRATEGY C — VALUE-TYPE PARITY (per-field, exact REST shape)
============================================================
REST rendered JSON: bigint → int, timestamptz → ISO str, numeric → str. The ORM
returns native types. nous_models has NO uuid columns and NO jsonb columns, so
the parity surface is small:

  id (BIGINT snowflake PK) → STAYS NATIVE int (the 5.3 trap). CONSUMER AUDIT:
    every consumer (admin nous_router._to_response, ai_router, ai_settings_
    router) reads ``str(row["id"])`` or passes id straight to a response model —
    ``str()`` works identically on int and str, and no consumer does ``int(id)``
    math or a type-sensitive ``==``. Native int is correct (and matches REST,
    which returned a JSON number for a bigint).

  created_at / updated_at (timestamptz) → ``.isoformat()`` ALWAYS. CONSUMER
    AUDIT: admin nous_router does ``str(row.get("created_at", ""))`` — ``str()``
    on an ISO string is a no-op (parity), on a native datetime would yield a
    space-separated form (divergence). isoformat() keeps it REST-shaped.

  pricing_value (Numeric) → LEFT NATIVE (Decimal). NUMERIC DECISION: the
    pricing/cost column. CONSUMER AUDIT — every consumer wraps it in
    ``float(row["pricing_value"])`` before any arithmetic:
      - admin nous_router._to_response: ``pricing_value=float(row["pricing_value"])``
      - ai_router billing: ``pricing_value = float(nous_model["pricing_value"])``
        then ``duration_seconds / 3600 * pricing_value`` math.
    ``float(x)`` works on BOTH a REST str ("8") AND a native Decimal — so a
    native Decimal is safe and the float() call normalises it before any math.
    REST returned a str for Numeric; we do NOT str() it because no consumer is
    str-sensitive and float(Decimal) is correct. (FastAPI's jsonable_encoder
    serialises Decimal fine for the public list_enabled / list_all responses,
    matching the REST number/string shape the frontend already tolerates.)

  pricing_type (Text) → native str, CONSUMED by ``== "per_hour"`` (str==str, OK).
  is_enabled (bool) / sort_order (int) → native, passed straight to response.

There are NO date columns and NO date/timestamptz RANGE filters in this repo
(every query filters by equality on name/category/is_enabled and orders by
sort_order), so there is no timestamptz<VARCHAR binding hazard.

Writes commit via ``write_scope()`` (the silent-rollback P0 lesson). Reads use
``read_scope()``. Error handling mirrors the legacy EXACTLY: list_* reads
swallow + return []; get_by_name swallows + returns None; create/update swallow
+ return None (NOT re-raise — the legacy returns None on failure); delete
swallows + returns False.
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
from app.repositories.nous_repository import NousRepository

_NOUS_N2A: Dict[str, str] = _name_to_attr(NousModels)
_NOUS_ATTRS = {p.key for p in NousModels.__mapper__.column_attrs}

# Public columns exposed by list_enabled (no api_key / app_id / base_url).
_PUBLIC_COLS = (
    NousModels.id,
    NousModels.name,
    NousModels.display_name,
    NousModels.category,
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


class NousRepositoryOrm(NousRepository):
    """ORM-backed NousRepository. Overrides every DB method on nous_models."""

    async def list_enabled(
        self, category: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        try:
            stmt = (
                select(*_PUBLIC_COLS)
                .where(NousModels.is_enabled.is_(True))
                .order_by(NousModels.sort_order)
            )
            if category:
                stmt = stmt.where(NousModels.category == category)
            async with read_scope() as session:
                result = await session.execute(stmt)
                # Partial-column SELECT → mappings() gives DB-column-keyed rows.
                return [_parity(dict(m)) for m in result.mappings().all()]
        except Exception as e:
            logger.error(f"Failed to list enabled nous models: {e}")
            return []

    async def get_by_name(self, name: str) -> Optional[Dict[str, Any]]:
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

    async def list_all(self) -> List[Dict[str, Any]]:
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

    async def delete(self, model_id: str) -> bool:
        try:
            async with write_scope() as session:
                await session.execute(
                    delete(NousModels).where(NousModels.id == int(model_id))
                )
            return True
        except Exception as e:
            logger.error(f"Failed to delete nous model {model_id}: {e}")
            return False


__all__ = ["NousRepositoryOrm"]
