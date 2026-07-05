# backend/app/repositories/mediahub_model_repository.py

"""Repository for mediahub_models table — admin-configured platform AI models.

ORM 2.0 (Phase 2, M batch): ``MediahubModelRepository`` is the SQLAlchemy 2.0
implementation — reads go through ``read_scope()`` and writes through
``write_scope()``; row objects are converted to SELECT *-shaped dicts by the
``_parity`` value-type sweep. Call sites route through ``get_mediahub_model_repository()``
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
returns native types. mediahub_models has NO uuid columns and NO jsonb columns, so
the parity surface is small:

  id (BIGINT snowflake PK) → STAYS NATIVE int (the 5.3 trap): every consumer
    (admin mediahub_model_router._to_response, ai_router, ai_settings_router) reads
    ``str(row["id"])`` or passes id straight to a response model — no consumer
    does ``int(id)`` math or a type-sensitive ``==``.

  created_at / updated_at (timestamptz) → ``.isoformat()`` ALWAYS: admin
    mediahub_model_router does ``str(row.get("created_at", ""))`` — ``str()`` on an ISO
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

from app.core.secure_settings import MARKER, encrypt_marked, reveal
from app.db.session import read_scope, write_scope
from app.models import MediahubModels
from app.repositories._orm_helpers import _name_to_attr, _orm_obj_to_dict

_NOUS_N2A: Dict[str, str] = _name_to_attr(MediahubModels)
_NOUS_ATTRS = {p.key for p in MediahubModels.__mapper__.column_attrs}

# Public columns exposed by list_enabled (no api_key / app_id / base_url).
_PUBLIC_COLS = (
    MediahubModels.id,
    MediahubModels.name,
    MediahubModels.display_name,
    MediahubModels.type,
    MediahubModels.description,
    MediahubModels.pricing_type,
    MediahubModels.pricing_value,
    MediahubModels.sort_order,
)


def _parity(out: Dict[str, Any]) -> Dict[str, Any]:
    """Strategy-C value-type parity IN PLACE on a SELECT *-shaped dict:
    datetime → ISO str (REST shape); bigint id + Numeric pricing_value LEFT
    NATIVE (the 5.3 trap + the float()-tolerant numeric decision). uuid → str
    (defensive; mediahub_models has none today). NULLs pass through."""
    for key, value in out.items():
        if isinstance(value, _uuid.UUID):
            out[key] = str(value)
        elif isinstance(value, _dt.datetime):
            out[key] = value.isoformat()
        elif isinstance(value, _dt.date):
            out[key] = value.isoformat()
    return out


def _conceal_api_key(values: Dict[str, Any]) -> Dict[str, Any]:
    """Phase 1b (secret-at-rest): encrypt a non-blank plaintext ``api_key``
    before it hits the DB. Idempotent (marker check) so re-writing an
    already-encrypted value never double-encrypts. Returns a NEW dict.

    STRICT — no dev-key fallback: raises ``SecretBoxNotConfigured`` when
    ``MEDIAHUB_TOKEN_ENCRYPTION_KEY`` isn't set, so a platform-model key
    write fails loud instead of landing under the public committed dev key.
    ``app_id`` (a provider app identifier, not a credential per the scout
    design) stays plaintext."""
    api_key = values.get("api_key")
    if not isinstance(api_key, str) or not api_key.strip():
        return values
    if api_key.startswith(MARKER):
        return values
    out = dict(values)
    out["api_key"] = encrypt_marked(api_key)
    return out


def _row(obj: Any) -> Dict[str, Any]:
    """SELECT *-shaped, strategy-C-parity dict for one full ORM row.

    Phase 1b: ``api_key`` is stored encrypted (``enc:v1:`` marker) — reveal
    it here so every full-row read path (get_by_name / list_all / create /
    update RETURNING) stays repo-transparent for consumers (prober, admin
    router key-inherit, provider resolution). ``reveal`` is fail-soft: an
    undecryptable value resolves to "" (logged), never raises."""
    out = _parity(_orm_obj_to_dict(obj, _NOUS_N2A))
    if isinstance(out.get("api_key"), str):
        out["api_key"] = reveal(out["api_key"])
    return out


class MediahubModelRepository:
    """CRUD for mediahub_models table (SQLAlchemy 2.0 ORM)."""

    TABLE = "mediahub_models"

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
                .where(MediahubModels.is_enabled.is_(True))
                .order_by(MediahubModels.sort_order)
            )
            if type_filter:
                stmt = stmt.where(MediahubModels.type == type_filter)
            async with read_scope() as session:
                result = await session.execute(stmt)
                # Partial-column SELECT → mappings() gives DB-column-keyed rows.
                return [_parity(dict(m)) for m in result.mappings().all()]
        except Exception as e:
            logger.error(f"Failed to list enabled mediahub models: {e}")
            return []

    async def get_by_name(self, name: str) -> Optional[Dict[str, Any]]:
        """Get a Mediahub model by name (includes all fields for backend use)."""
        try:
            async with read_scope() as session:
                result = await session.execute(
                    select(MediahubModels).where(MediahubModels.name == name).limit(1)
                )
                row = result.scalars().first()
                return _row(row) if row else None
        except Exception as e:
            logger.error(f"Failed to get mediahub model '{name}': {e}")
            return None

    async def get_by_actual_model(self, actual_model: str) -> Optional[Dict[str, Any]]:
        """Get a Mediahub model by ``actual_model`` (the raw upstream model id,
        e.g. ``doubao-seed-2-0-lite-260428``) — a fallback lookup for
        ``resolve_mediahub_model`` when an ``ai_agents.model`` value stores the
        raw provider id instead of the catalog ``name``. ``name`` values are
        ``mediahub-*`` prefixed and ``actual_model`` values are raw provider ids,
        so the two namespaces never collide. Ordered by ``sort_order`` so a
        hypothetical duplicate resolves deterministically (mirrors
        ``get_by_name``'s ``.limit(1)`` shape)."""
        try:
            async with read_scope() as session:
                result = await session.execute(
                    select(MediahubModels)
                    .where(MediahubModels.actual_model == actual_model)
                    .order_by(MediahubModels.sort_order)
                    .limit(1)
                )
                row = result.scalars().first()
                return _row(row) if row else None
        except Exception as e:
            logger.error(
                f"Failed to get mediahub model by actual_model '{actual_model}': {e}"
            )
            return None

    # ------------------------------------------------------------------
    # Admin CRUD
    # ------------------------------------------------------------------

    async def list_all(self) -> List[Dict[str, Any]]:
        """List all Nous models (admin, includes disabled)."""
        try:
            async with read_scope() as session:
                result = await session.execute(
                    select(MediahubModels).order_by(MediahubModels.sort_order)
                )
                return [_row(r) for r in result.scalars().all()]
        except Exception as e:
            logger.error(f"Failed to list all mediahub models: {e}")
            return []

    async def create(self, data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Create a new Mediahub model. ``api_key`` is encrypted at rest
        (Phase 1b) — encryption happens BEFORE the swallow-to-None try block
        so a missing MEDIAHUB_TOKEN_ENCRYPTION_KEY fails loud (fail-closed
        write), not as an opaque None."""
        values = _conceal_api_key({k: v for k, v in data.items() if k in _NOUS_ATTRS})
        try:
            async with write_scope() as session:
                result = await session.execute(
                    insert(MediahubModels).values(**values).returning(MediahubModels)
                )
                row = result.scalars().first()
                return _row(row) if row else None
        except Exception as e:
            logger.error(f"Failed to create mediahub model: {e}")
            return None

    async def update(
        self, model_id: str, data: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """Update a Mediahub model. ``api_key`` is encrypted at rest (Phase
        1b); encryption runs BEFORE the swallow-to-None try block so a
        missing encryption key fails loud (see ``create``)."""
        # Drop the REST "now()" sentinel; set updated_at via SQL func.now()
        # (the legacy injected {"updated_at": "now()"} as a server-time
        # string — binding that literal would error on a DateTime column).
        values = _conceal_api_key(
            {k: v for k, v in data.items() if k in _NOUS_ATTRS and k != "updated_at"}
        )
        try:
            values["updated_at"] = func.now()
            async with write_scope() as session:
                result = await session.execute(
                    update(MediahubModels)
                    .where(MediahubModels.id == int(model_id))
                    .values(**values)
                    .returning(MediahubModels)
                )
                row = result.scalars().first()
                return _row(row) if row else None
        except Exception as e:
            logger.error(f"Failed to update mediahub model {model_id}: {e}")
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
                    update(MediahubModels)
                    .where(MediahubModels.id == int(model_id))
                    .values(
                        last_test_status=status,
                        last_test_detail=detail,
                        last_tested_at=func.now(),
                    )
                    .returning(MediahubModels)
                )
                row = result.scalars().first()
                return _row(row) if row else None
        except Exception as e:
            logger.error(f"Failed to record test result for {model_id}: {e}")
            return None

    async def delete(self, model_id: str) -> bool:
        """Delete a Mediahub model."""
        try:
            async with write_scope() as session:
                await session.execute(
                    delete(MediahubModels).where(MediahubModels.id == int(model_id))
                )
            return True
        except Exception as e:
            logger.error(f"Failed to delete mediahub model {model_id}: {e}")
            return False


def get_mediahub_model_repository() -> MediahubModelRepository:
    """Return the MediahubModelRepository (SQLAlchemy 2.0 ORM, collapsed post-rollout)."""
    return MediahubModelRepository()
