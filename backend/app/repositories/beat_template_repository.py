# app/repositories/beat_template_repository.py

"""Beat Template Repository — SQLAlchemy 2.0 ORM data access for user custom
beat-sheet templates (``beat_templates``).

ORM-only (read_scope / write_scope), matching ``script_beat_repository.py``:
reads swallow + return None/[] on failure; writes log + re-raise (write_scope
rolls back on any raise). The bigint id is ``_bigint``-coerced at the boundary
(the 5.3 trap — a snowflake bound as str silently misses). Read dicts go through
strategy-C value-type parity (uuid → str, datetime → ISO str; the bigint id
STAYS native int; ``anchors`` JSONB stays a native list).

Ownership is NOT enforced here (the router guard verify_beat_template_access
does that) — except ``list_by_user`` which filters to the caller's rows.
"""

from __future__ import annotations

import datetime as _dt
import uuid as _uuid
from typing import Any, Dict, List, Optional

from loguru import logger
from sqlalchemy import delete as sa_delete
from sqlalchemy import func, insert, select, update

from app.db.session import read_scope, write_scope
from app.models import BeatTemplates
from app.repositories._orm_helpers import _name_to_attr, _orm_obj_to_dict

_TPL_N2A: Dict[str, str] = _name_to_attr(BeatTemplates)


def _bigint(v: Any) -> Optional[int]:
    """Coerce a bigint id bind value to native int; None passes through."""
    return None if v is None else int(v)


def _parity(out: Dict[str, Any]) -> Dict[str, Any]:
    """Strategy-C value-type parity IN PLACE: uuid → str, datetime → ISO str.
    Bigint id and JSONB (list) stay native. NULLs pass through."""
    for key, value in out.items():
        if isinstance(value, _uuid.UUID):
            out[key] = str(value)
        elif isinstance(value, _dt.datetime):
            out[key] = value.isoformat()
    return out


def _row(obj: Any) -> Dict[str, Any]:
    """SELECT *-shaped, strategy-C-parity dict for one BeatTemplates row."""
    return _parity(_orm_obj_to_dict(obj, _TPL_N2A))


class BeatTemplateRepository:
    """Custom beat-template data access (async, SQLAlchemy 2.0 ORM)."""

    def __init__(self):
        pass

    # ------------------------------------------------------------------ #
    # Reads
    # ------------------------------------------------------------------ #

    async def list_by_user(self, user_id: str) -> List[Dict[str, Any]]:
        """All templates owned by ``user_id``, newest first."""
        try:
            async with read_scope() as session:
                result = await session.execute(
                    select(BeatTemplates)
                    .where(BeatTemplates.user_id == user_id)
                    .order_by(BeatTemplates.created_at.desc())
                )
                return [_row(r) for r in result.scalars().all()]
        except Exception as e:
            logger.error(f"Failed to list beat templates for user {user_id}: {e}")
            return []

    async def get_by_id(self, template_id: str) -> Optional[Dict[str, Any]]:
        """A single template by id, or None (used by the ownership guard)."""
        try:
            async with read_scope() as session:
                result = await session.execute(
                    select(BeatTemplates)
                    .where(BeatTemplates.id == _bigint(template_id))
                    .limit(1)
                )
                row = result.scalars().first()
                return _row(row) if row else None
        except Exception as e:
            logger.error(f"Failed to get beat template {template_id}: {e}")
            return None

    # ------------------------------------------------------------------ #
    # Writes — create / rename / delete
    # ------------------------------------------------------------------ #

    async def create(
        self, user_id: str, name: str, anchors: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """Create a template owned by ``user_id``."""
        try:
            async with write_scope() as session:
                result = await session.execute(
                    insert(BeatTemplates)
                    .values(user_id=user_id, name=name, anchors=anchors)
                    .returning(BeatTemplates)
                )
                row = result.scalars().first()
                out = _row(row) if row else {}
            logger.info(f"Created beat template for user {user_id}")
            return out
        except Exception as e:
            logger.error(f"Failed to create beat template: {e}")
            raise

    async def rename(self, template_id: str, name: str) -> Optional[Dict[str, Any]]:
        """Rename a template. ``updated_at`` is bumped to now()."""
        try:
            async with write_scope() as session:
                result = await session.execute(
                    update(BeatTemplates)
                    .where(BeatTemplates.id == _bigint(template_id))
                    .values(name=name, updated_at=func.now())
                    .returning(BeatTemplates)
                )
                row = result.scalars().first()
                out = _row(row) if row else None
            logger.info(f"Renamed beat template {template_id}")
            return out
        except Exception as e:
            logger.error(f"Failed to rename beat template {template_id}: {e}")
            raise

    async def delete(self, template_id: str) -> bool:
        """Delete a template."""
        try:
            async with write_scope() as session:
                await session.execute(
                    sa_delete(BeatTemplates).where(
                        BeatTemplates.id == _bigint(template_id)
                    )
                )
            logger.info(f"Deleted beat template {template_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to delete beat template {template_id}: {e}")
            raise


def get_beat_template_repository() -> "BeatTemplateRepository":
    """Return the BeatTemplateRepository (ORM-only)."""
    return BeatTemplateRepository()
