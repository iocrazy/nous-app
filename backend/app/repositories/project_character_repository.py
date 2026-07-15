"""Data access for the project character library (mig 357).

ORM-backed (read_scope/write_scope). Snowflake BIGINT ids ride as strings at
the API boundary (bigIntSafeFetch discipline); ``_serialize`` renders ``id`` /
``project_id`` as str and timestamps as ISO strings, matching what the old
PostgREST path returned. ``ProjectCharacters`` carries no scope mixin —
ownership is scoped by the explicit ``project_id`` predicate in every method
(service-role/RLS-bypass model), so the choke point stays inert.
"""

from __future__ import annotations

import datetime
from typing import Any, Dict, List, Optional

from loguru import logger
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.db.session import read_scope, write_scope
from app.models import ProjectCharacters


def _row_dict(obj: ProjectCharacters) -> Dict[str, Any]:
    """Flatten an ORM row to a plain column→value dict."""
    return {col.name: getattr(obj, col.name) for col in obj.__table__.columns}


def _serialize(row: Dict[str, Any]) -> Dict[str, Any]:
    """REST-shaped dict: BIGINT ids → str, datetime → ISO str, so the value
    types match the PostgREST baseline the frontend expects. ``tags`` (jsonb)
    stays a native dict as PostgREST returned it."""
    out: Dict[str, Any] = {}
    for key, val in row.items():
        if key in ("id", "project_id") and val is not None:
            out[key] = str(val)
        elif isinstance(val, datetime.datetime):
            out[key] = val.isoformat()
        else:
            out[key] = val
    return out


class ProjectCharacterRepository:
    TABLE = "project_characters"

    async def list_by_project(self, project_id: str) -> List[Dict[str, Any]]:
        async with read_scope() as session:
            result = await session.execute(
                select(ProjectCharacters)
                .where(ProjectCharacters.project_id == int(project_id))
                .order_by(
                    ProjectCharacters.sort_order,
                    ProjectCharacters.created_at,
                )
            )
            return [_serialize(_row_dict(o)) for o in result.scalars().all()]

    async def create(self, project_id: str, fields: Dict[str, Any]) -> Dict[str, Any]:
        async with write_scope() as session:
            obj = ProjectCharacters(**fields, project_id=int(project_id))
            session.add(obj)
            await session.flush()
            await session.refresh(obj)  # load server defaults (id, created_at, …)
            return _serialize(_row_dict(obj))

    async def update(
        self, project_id: str, character_id: str, fields: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """Patch one character; None when the row isn't in this project."""
        if not fields:
            return await self._get(project_id, character_id)
        async with write_scope() as session:
            obj = (
                (
                    await session.execute(
                        select(ProjectCharacters)
                        .where(
                            ProjectCharacters.id == int(character_id),
                            ProjectCharacters.project_id == int(project_id),
                        )
                        .limit(1)
                    )
                )
                .scalars()
                .first()
            )
            if obj is None:
                return None
            for key, value in fields.items():
                setattr(obj, key, value)
            await session.flush()
            await session.refresh(obj)
            return _serialize(_row_dict(obj))

    async def delete(self, project_id: str, character_id: str) -> bool:
        async with write_scope() as session:
            obj = (
                (
                    await session.execute(
                        select(ProjectCharacters)
                        .where(
                            ProjectCharacters.id == int(character_id),
                            ProjectCharacters.project_id == int(project_id),
                        )
                        .limit(1)
                    )
                )
                .scalars()
                .first()
            )
            if obj is None:
                return False
            await session.delete(obj)
            return True

    async def upsert_by_name(
        self, project_id: str, names: List[str]
    ) -> List[Dict[str, Any]]:
        """Extract-from-script: materialize derived character names as rows.

        Idempotent on (project_id, name) — existing rows (including manually
        edited ones) are left untouched (ON CONFLICT DO NOTHING), so re-running
        Extract never clobbers curation.
        """
        cleaned = [n.strip() for n in names if n and n.strip()]
        if not cleaned:
            return []
        try:
            stmt = (
                pg_insert(ProjectCharacters)
                .values(
                    [
                        {
                            "project_id": int(project_id),
                            "name": name,
                            "source": "script",
                        }
                        for name in cleaned
                    ]
                )
                .on_conflict_do_nothing(index_elements=["project_id", "name"])
            )
            async with write_scope() as session:
                await session.execute(stmt)
        except Exception as e:  # noqa: BLE001
            logger.error(f"character extract upsert failed for {project_id}: {e}")
            raise
        return await self.list_by_project(project_id)

    async def _get(
        self, project_id: str, character_id: str
    ) -> Optional[Dict[str, Any]]:
        async with read_scope() as session:
            obj = (
                (
                    await session.execute(
                        select(ProjectCharacters)
                        .where(
                            ProjectCharacters.id == int(character_id),
                            ProjectCharacters.project_id == int(project_id),
                        )
                        .limit(1)
                    )
                )
                .scalars()
                .first()
            )
            return _serialize(_row_dict(obj)) if obj else None


def get_project_character_repository() -> ProjectCharacterRepository:
    return ProjectCharacterRepository()
