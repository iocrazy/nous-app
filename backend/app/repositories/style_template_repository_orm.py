"""SQLAlchemy 2.0 ORM implementation of StyleTemplateRepository (Batch L1).

REST → ORM successor for the ``style_templates`` surface, following the
validated ``AgentRepositoryOrm`` pilot template. ``StyleTemplateRepositoryOrm``
subclasses ``StyleTemplateRepository`` and overrides the data methods; the
non-DB ``TABLE_NAME`` constant is inherited. Call sites route through
``get_style_template_repository()``.

NOTE ON LIVE CALLERS
====================
``style_templates_router`` is a 301 redirect to ``/skills`` (deprecated), so
this repo currently has NO live call sites. The ORM subclass + factory are
added anyway for batch parity and so a future un-deprecation inherits the
migration for free.

STRATEGY-C VALUE-TYPE PARITY (per-field, exact REST shape)
==========================================================
Supabase REST renders ``uuid`` → STRING, ``bigint`` → int, ``timestamptz`` →
ISO string. The ORM returns native ``uuid.UUID`` / ``int`` / ``datetime``.

  style_templates.id : bigint → STAYS native int (the 5.3 scope-zeroing trap —
    never str a bigint). REST returned an int (JSON number); precision is a
    FRONTEND concern handled by bigIntSafeFetch, not the backend.
  style_templates.team_id : bigint → STAYS native int.
  style_templates.created_by : uuid → STR. No live Python consumer reads it
    type-sensitively today (no callers at all), but we coerce for exact REST
    shape parity (cheap, and future-proofs any UUID(...) / dict-key consumer if
    the redirect is ever lifted). HTTP responses are unaffected either way.
  style_templates.created_at / updated_at : timestamptz → ``.isoformat()``
    ALWAYS (the unconditional template rule — cheap, matches REST, dodges the
    silent ``str(datetime)`` SPACE-vs-``T`` footgun).
  is_public (bool) / name / prompt_content / description / category (text) →
    native (REST returned the same Python types).

Renamed-col/enum scan: ``StyleTemplates`` has NEITHER a ``mapped_column("db")``
rename NOR any SQLAlchemy ``Enum`` column (every ``name == key``; no enum
types). So ``_plain`` is NOT load-bearing here — we still route through
``_name_to_attr`` + ``_orm_obj_to_dict`` for mechanical parity with the other
ORM repos and to stay correct under a future column rename.

Writes commit via ``write_scope()`` (the silent-rollback P0 lesson).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from loguru import logger
from sqlalchemy import delete, insert, or_, select, update

from app.db.session import read_scope, write_scope
from app.models import StyleTemplates
from app.repositories._orm_helpers import _name_to_attr, _orm_obj_to_dict
from app.repositories.style_template_repository import StyleTemplateRepository

# style_templates DB-column-name → mapped-attribute-name (built once). Every
# name == key here, but resolve via the map for parity / rename-safety.
_STYLE_TEMPLATES_NAME_TO_ATTR: Dict[str, str] = _name_to_attr(StyleTemplates)

# uuid columns coerced to str at the dict boundary (REST-parity; see docstring).
_STYLE_TEMPLATE_UUID_STR_COLS = ("created_by",)

# timestamptz columns ISO-coerced at the boundary (template rule).
_STYLE_TEMPLATE_TS_ISO_COLS = ("created_at", "updated_at")


def _style_template_to_dict(obj: Any) -> Dict[str, Any]:
    """SELECT *-shaped dict for a ``style_templates`` ORM row with strategy-C
    parity: created_by uuid → str, created_at/updated_at → ISO str, bigint
    id/team_id stay native int. NULLs pass through unchanged."""
    out = _orm_obj_to_dict(obj, _STYLE_TEMPLATES_NAME_TO_ATTR)
    for col in _STYLE_TEMPLATE_UUID_STR_COLS:
        val = out.get(col)
        if val is not None:
            out[col] = str(val)
    for col in _STYLE_TEMPLATE_TS_ISO_COLS:
        val = out.get(col)
        if isinstance(val, datetime):
            out[col] = val.isoformat()
    # Generic guard: any other timestamp column (e.g. a future addition) still
    # gets ISO-coerced, so the template rule holds without a code change.
    for key, val in out.items():
        if isinstance(val, datetime):
            out[key] = val.isoformat()
    return out


class StyleTemplateRepositoryOrm(StyleTemplateRepository):
    """ORM-backed StyleTemplateRepository.

    Overrides the BaseRepository CRUD surface (create / update / get_by_id /
    hard_delete) plus ``list_templates`` for the ``style_templates`` table.
    ``delete`` (which calls ``hard_delete``) is inherited unchanged."""

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    async def get_by_id(self, record_id: str) -> Optional[Dict[str, Any]]:
        """Fetch a style template by id; returns None if not found."""
        try:
            async with read_scope() as session:
                result = await session.execute(
                    select(StyleTemplates)
                    .where(StyleTemplates.id == int(record_id))
                    .limit(1)
                )
                row = result.scalars().first()
                return _style_template_to_dict(row) if row else None
        except Exception as e:
            logger.error(f"Failed to get style_templates {record_id}: {e}")
            return None

    async def list_templates(
        self,
        team_id: Optional[str] = None,
        category: Optional[str] = None,
        include_public: bool = True,
    ) -> List[Dict[str, Any]]:
        """List templates filtered by team, category, public visibility.

        team_id + include_public → team's own OR public; team_id only →
        team's own; neither → public only. Ordered by created_at DESC.
        Mirrors the REST or_/eq filter shape exactly."""
        try:
            stmt = select(StyleTemplates)
            if team_id and include_public:
                stmt = stmt.where(
                    or_(
                        StyleTemplates.team_id == int(team_id),
                        StyleTemplates.is_public.is_(True),
                    )
                )
            elif team_id:
                stmt = stmt.where(StyleTemplates.team_id == int(team_id))
            else:
                stmt = stmt.where(StyleTemplates.is_public.is_(True))

            if category:
                stmt = stmt.where(StyleTemplates.category == category)

            stmt = stmt.order_by(StyleTemplates.created_at.desc())
            async with read_scope() as session:
                result = await session.execute(stmt)
                return [_style_template_to_dict(r) for r in result.scalars().all()]
        except Exception as e:
            logger.error(f"Failed to list style templates: {e}")
            return []

    # ------------------------------------------------------------------
    # Writes (COMMITTING via write_scope)
    # ------------------------------------------------------------------

    async def create(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Insert a style template; returns the inserted row dict. Committing.
        Raises if no row returned (BaseRepository contract)."""
        try:
            async with write_scope() as session:
                result = await session.execute(
                    insert(StyleTemplates).values(**data).returning(StyleTemplates)
                )
                row = result.scalars().first()
                if row is None:
                    raise RuntimeError("Insert into style_templates returned no data")
                out = _style_template_to_dict(row)
            logger.info("Created style_templates record")
            return out
        except Exception as e:
            logger.error(f"Failed to create style_templates: {e}")
            raise

    async def update(self, record_id: str, data: Dict[str, Any]) -> Dict[str, Any]:
        """PATCH-style update; returns the updated row dict or {} if no match.
        Committing."""
        try:
            async with write_scope() as session:
                result = await session.execute(
                    update(StyleTemplates)
                    .where(StyleTemplates.id == int(record_id))
                    .values(**data)
                    .returning(StyleTemplates)
                )
                row = result.scalars().first()
                return _style_template_to_dict(row) if row else {}
        except Exception as e:
            logger.error(f"Failed to update style_templates {record_id}: {e}")
            raise

    async def hard_delete(self, record_id: str) -> None:
        """Hard-delete a style template by id. Committing."""
        try:
            async with write_scope() as session:
                await session.execute(
                    delete(StyleTemplates).where(StyleTemplates.id == int(record_id))
                )
            logger.info(f"Deleted style_templates {record_id}")
        except Exception as e:
            logger.error(f"Failed to delete style_templates {record_id}: {e}")
            raise


__all__ = ["StyleTemplateRepositoryOrm"]
