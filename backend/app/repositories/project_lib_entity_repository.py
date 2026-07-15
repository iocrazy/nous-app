"""Data access for the generalized project library (mig 358) — locations +
props in one table, keyed by entity_type. Mirrors project_character_repository
(ORM-backed; snowflake ids ride as strings; Extract upsert never clobbers
curation). ``ProjectLibEntities`` carries no scope mixin — ownership is scoped
by the explicit ``project_id`` predicate in every method.
"""

from __future__ import annotations

import datetime
from typing import Any, Dict, List, Optional

from loguru import logger
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.db.session import read_scope, write_scope
from app.models import ProjectLibEntities


def _row_dict(obj: ProjectLibEntities) -> Dict[str, Any]:
    """Flatten an ORM row to a plain column→value dict."""
    return {col.name: getattr(obj, col.name) for col in obj.__table__.columns}


def _serialize(row: Dict[str, Any]) -> Dict[str, Any]:
    """REST-shaped dict: BIGINT ids → str, datetime → ISO str. ``tags`` (jsonb)
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


class ProjectLibEntityRepository:
    TABLE = "project_lib_entities"

    async def list_by_project(
        self, project_id: str, entity_type: str
    ) -> List[Dict[str, Any]]:
        async with read_scope() as session:
            result = await session.execute(
                select(ProjectLibEntities)
                .where(
                    ProjectLibEntities.project_id == int(project_id),
                    ProjectLibEntities.entity_type == entity_type,
                )
                .order_by(
                    ProjectLibEntities.sort_order,
                    ProjectLibEntities.created_at,
                )
            )
            return [_serialize(_row_dict(o)) for o in result.scalars().all()]

    async def create(
        self, project_id: str, entity_type: str, fields: Dict[str, Any]
    ) -> Dict[str, Any]:
        async with write_scope() as session:
            obj = ProjectLibEntities(
                **fields, project_id=int(project_id), entity_type=entity_type
            )
            session.add(obj)
            await session.flush()
            await session.refresh(obj)
            return _serialize(_row_dict(obj))

    async def update(
        self, project_id: str, entity_type: str, entity_id: str, fields: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        if not fields:
            return await self._get(project_id, entity_type, entity_id)
        async with write_scope() as session:
            obj = (
                (
                    await session.execute(
                        select(ProjectLibEntities)
                        .where(
                            ProjectLibEntities.id == int(entity_id),
                            ProjectLibEntities.project_id == int(project_id),
                            ProjectLibEntities.entity_type == entity_type,
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

    async def delete(self, project_id: str, entity_type: str, entity_id: str) -> bool:
        async with write_scope() as session:
            obj = (
                (
                    await session.execute(
                        select(ProjectLibEntities)
                        .where(
                            ProjectLibEntities.id == int(entity_id),
                            ProjectLibEntities.project_id == int(project_id),
                            ProjectLibEntities.entity_type == entity_type,
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
        self, project_id: str, entity_type: str, names: List[str]
    ) -> List[Dict[str, Any]]:
        """Extract: materialize derived names as rows. Idempotent on
        (project, type, name); ON CONFLICT DO NOTHING so re-running never
        clobbers curated rows."""
        cleaned = [n.strip() for n in names if n and n.strip()]
        if not cleaned:
            return []
        try:
            stmt = (
                pg_insert(ProjectLibEntities)
                .values(
                    [
                        {
                            "project_id": int(project_id),
                            "entity_type": entity_type,
                            "name": name,
                            "source": "script",
                        }
                        for name in cleaned
                    ]
                )
                .on_conflict_do_nothing(
                    index_elements=["project_id", "entity_type", "name"]
                )
            )
            async with write_scope() as session:
                await session.execute(stmt)
        except Exception as e:  # noqa: BLE001
            logger.error(
                f"lib entity extract upsert failed for {project_id}/{entity_type}: {e}"
            )
            raise
        return await self.list_by_project(project_id, entity_type)

    async def _get(
        self, project_id: str, entity_type: str, entity_id: str
    ) -> Optional[Dict[str, Any]]:
        async with read_scope() as session:
            obj = (
                (
                    await session.execute(
                        select(ProjectLibEntities)
                        .where(
                            ProjectLibEntities.id == int(entity_id),
                            ProjectLibEntities.project_id == int(project_id),
                            ProjectLibEntities.entity_type == entity_type,
                        )
                        .limit(1)
                    )
                )
                .scalars()
                .first()
            )
            return _serialize(_row_dict(obj)) if obj else None


def get_project_lib_entity_repository() -> ProjectLibEntityRepository:
    return ProjectLibEntityRepository()
