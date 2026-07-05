# app/repositories/episode_repository.py

"""Episode Repository — SQLAlchemy 2.0 ORM data access for the ``episodes``
dimension (spec v3 §2.1).

ORM-only (read_scope / write_scope), matching the house idiom in
``projects_repository.py`` / ``script_scene_repository.py``. Reads swallow +
return None/[] on failure; writes log + re-raise. Every bigint id/FK is
``_bigint``-coerced at the boundary (5.3 trap); read dicts go through
strategy-C value-type parity (datetime → ISO str; bigint ids stay native int).
"""

from __future__ import annotations

import datetime as _dt
import uuid as _uuid
from typing import Any, Dict, List, Optional

from loguru import logger
from sqlalchemy import delete as sa_delete
from sqlalchemy import insert, select, update

from app.db.session import read_scope, write_scope
from app.models import Episodes
from app.repositories._orm_helpers import _name_to_attr, _orm_obj_to_dict

_EPISODES_N2A: Dict[str, str] = _name_to_attr(Episodes)
_EPISODES_ATTRS = {p.key for p in Episodes.__mapper__.column_attrs}
_EPISODE_BIGINT_FIELDS = ("project_id",)


def _bigint(v: Any) -> Optional[int]:
    """Coerce a bigint id/FK bind value to native int; None passes through."""
    return None if v is None else int(v)


def _parity(out: Dict[str, Any]) -> Dict[str, Any]:
    """Strategy-C value-type parity IN PLACE: uuid → str, datetime/date → ISO
    str. Bigint ids/FKs stay native int. NULLs pass through."""
    for key, value in out.items():
        if isinstance(value, _uuid.UUID):
            out[key] = str(value)
        elif isinstance(value, _dt.datetime):
            out[key] = value.isoformat()
        elif isinstance(value, _dt.date):
            out[key] = value.isoformat()
    return out


def _row(obj: Any) -> Dict[str, Any]:
    """SELECT *-shaped, strategy-C-parity dict for one Episodes row."""
    return _parity(_orm_obj_to_dict(obj, _EPISODES_N2A))


def _episode_write_values(data: Dict[str, Any]) -> Dict[str, Any]:
    """Keep only mapped columns (graceful no-op for unknown keys) and
    bigint-coerce the id/FK fields."""
    known = {k: v for k, v in data.items() if k in _EPISODES_ATTRS}
    for field in _EPISODE_BIGINT_FIELDS:
        if field in known and known[field] is not None:
            known[field] = _bigint(known[field])
    return known


class EpisodeRepository:
    """Episodes data access (async, SQLAlchemy 2.0 ORM)."""

    def __init__(self):
        pass

    async def list_by_project(self, project_id: str) -> List[Dict[str, Any]]:
        """All episodes for a project, ordered by sort_order."""
        try:
            async with read_scope() as session:
                result = await session.execute(
                    select(Episodes)
                    .where(Episodes.project_id == _bigint(project_id))
                    .order_by(Episodes.sort_order.asc())
                )
                return [_row(r) for r in result.scalars().all()]
        except Exception as e:
            logger.error(f"Failed to list episodes for project {project_id}: {e}")
            return []

    async def get_by_id(self, episode_id: str) -> Optional[Dict[str, Any]]:
        """A single episode by id, or None."""
        try:
            async with read_scope() as session:
                result = await session.execute(
                    select(Episodes).where(Episodes.id == _bigint(episode_id)).limit(1)
                )
                row = result.scalars().first()
                return _row(row) if row else None
        except Exception as e:
            logger.error(f"Failed to get episode {episode_id}: {e}")
            return None

    async def create(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Create an episode."""
        try:
            values = _episode_write_values(data)
            async with write_scope() as session:
                result = await session.execute(
                    insert(Episodes).values(**values).returning(Episodes)
                )
                row = result.scalars().first()
                out = _row(row) if row else {}
            logger.info(f"Created episode in project {data.get('project_id')}")
            return out
        except Exception as e:
            logger.error(f"Failed to create episode: {e}")
            raise

    async def update(
        self, episode_id: str, data: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """Update an episode (title / sort_order). Returns the updated row or
        None when no such episode exists."""
        try:
            values = {
                k: v
                for k, v in _episode_write_values(data).items()
                if k not in ("id", "project_id", "created_at")
            }
            if not values:
                return await self.get_by_id(episode_id)
            async with write_scope() as session:
                result = await session.execute(
                    update(Episodes)
                    .where(Episodes.id == _bigint(episode_id))
                    .values(**values)
                    .returning(Episodes)
                )
                row = result.scalars().first()
                out = _row(row) if row else None
            logger.info(f"Updated episode {episode_id}")
            return out
        except Exception as e:
            logger.error(f"Failed to update episode {episode_id}: {e}")
            raise

    async def delete(self, episode_id: str) -> bool:
        """Delete an episode."""
        try:
            async with write_scope() as session:
                await session.execute(
                    sa_delete(Episodes).where(Episodes.id == _bigint(episode_id))
                )
            logger.info(f"Deleted episode {episode_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to delete episode {episode_id}: {e}")
            raise


def get_episode_repository() -> "EpisodeRepository":
    """Return the EpisodeRepository (ORM-only)."""
    return EpisodeRepository()
