"""SQLAlchemy 2.0 ORM implementation of the Script*Repository classes (L1b).

REST → ORM successors for the script_projects / script_chapters / script_assets
/ script_storyboard_links surface, following the validated ``AgentRepositoryOrm``
pilot template. Each ``Script*RepositoryOrm`` subclasses its REST counterpart and
overrides the data methods; the non-DB ``TABLE_NAME`` constant is inherited. Call
sites route through ``get_script_*_repository()``.

STRATEGY-C VALUE-TYPE PARITY (per-field, exact REST shape)
==========================================================
Supabase REST renders ``uuid`` → STRING, ``bigint`` → int, ``timestamptz`` →
ISO string. The ORM returns native ``uuid.UUID`` / ``int`` / ``datetime``.

  ALL ids (id / script_id / project_id / team_id / parent_chapter_id /
  chapter_id / storyboard_project_id / storyboard_node_id) : bigint → STAY
  native int (the 5.3 scope-zeroing trap — never str a bigint). REST returned
  int; the consumer (script_service) feeds id straight back into where-clauses
  and never does ``UUID(...)`` or a bigint==str compare.

  script_projects.created_by : uuid → STR for shape parity (consumer audit
  below found no type-sensitive read — coercion is cheap and matches REST).

  created_at / updated_at : timestamptz → ``.isoformat()`` ALWAYS (the
  unconditional template rule).

  settings_json / viewport_json / data_json / content_json (JSONB) → native
  dict; status / name / display_code / asset_type / text cols → native str;
  position_x / position_y / width / height (double precision) → native float
  (no consumer is type-sensitive — left native per the iron rule).

Consumer audit (script_service is the only caller of these repos):
  - create_project passes ``created_by`` as INPUT; reads ``project["id"]`` only
    to feed it back into ``update(id, ...)`` (a where-clause value) — NOT
    ``UUID(...)``, NOT a comparison. No type-sensitive uuid OUTPUT read exists,
    so created_by coercion is shape-parity only; bigint ids MUST stay int.

Model-quirk scan: none of the four tables has a renamed column or a SQLAlchemy
``Enum`` column (script_projects.status is a plain Text col with a server
default, not a PG enum). ``_plain`` is not load-bearing; reads route through
``_name_to_attr`` + ``_orm_obj_to_dict`` for mechanical parity / rename-safety.

Write-input audit: create/update/bulk_upsert receive str team_id / created_by /
script_id from the service. created_by binds via the Uuid type processor;
bigint FKs (script_id / project_id / team_id) are coerced to int before bind
(SQLAlchemy's asyncpg int8 codec is strict about str-for-bigint). No raw
``values()`` type hazard beyond that coercion.

Writes commit via ``write_scope()`` (the silent-rollback P0 lesson). bulk_upsert
is ON CONFLICT (id) — idempotent.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from loguru import logger
from sqlalchemy import delete, func, insert, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.db.session import read_scope, write_scope
from app.models import (
    ScriptAssets,
    ScriptChapters,
    ScriptProjects,
    ScriptStoryboardLinks,
)
from app.repositories._orm_helpers import _name_to_attr, _orm_obj_to_dict
from app.repositories.script_repository import (
    ScriptAssetRepository,
    ScriptChapterRepository,
    ScriptProjectRepository,
    ScriptStoryboardLinkRepository,
)

# DB-column-name → mapped-attribute-name maps (built once per model).
_PROJECT_N2A: Dict[str, str] = _name_to_attr(ScriptProjects)
_CHAPTER_N2A: Dict[str, str] = _name_to_attr(ScriptChapters)
_ASSET_N2A: Dict[str, str] = _name_to_attr(ScriptAssets)
_LINK_N2A: Dict[str, str] = _name_to_attr(ScriptStoryboardLinks)


def _to_dict(obj: Any, name_to_attr: Dict[str, str]) -> Dict[str, Any]:
    """SELECT *-shaped dict for a script-domain ORM row with strategy-C parity:
    created_by uuid → str (only present on script_projects), all timestamptz →
    ISO str, bigint ids stay native int. NULLs pass through unchanged."""
    out = _orm_obj_to_dict(obj, name_to_attr)
    val = out.get("created_by")
    if val is not None:
        out["created_by"] = str(val)
    for key, value in out.items():
        if isinstance(value, datetime):
            out[key] = value.isoformat()
    return out


def _bigint(value: Any) -> Any:
    """Coerce a snowflake-as-str id to int for a BIGINT bind/where (asyncpg's
    int8 codec rejects str). Passes None / already-int through unchanged."""
    if value is None or isinstance(value, int):
        return value
    return int(value)


# ─── script_projects ────────────────────────────────────────────────────


class ScriptProjectRepositoryOrm(ScriptProjectRepository):
    """ORM-backed ScriptProjectRepository (script_projects)."""

    async def get_by_id(self, record_id: str) -> Optional[Dict[str, Any]]:
        try:
            async with read_scope() as session:
                result = await session.execute(
                    select(ScriptProjects)
                    .where(ScriptProjects.id == _bigint(record_id))
                    .limit(1)
                )
                row = result.scalars().first()
                return _to_dict(row, _PROJECT_N2A) if row else None
        except Exception as e:
            logger.error(f"Failed to get script_projects {record_id}: {e}")
            return None

    async def create(self, data: Dict[str, Any]) -> Dict[str, Any]:
        try:
            async with write_scope() as session:
                result = await session.execute(
                    insert(ScriptProjects).values(**data).returning(ScriptProjects)
                )
                row = result.scalars().first()
                if row is None:
                    raise RuntimeError("Insert into script_projects returned no data")
                out = _to_dict(row, _PROJECT_N2A)
            logger.info("Created script_projects record")
            return out
        except Exception as e:
            logger.error(f"Failed to create script_projects: {e}")
            raise

    async def update(self, record_id: str, data: Dict[str, Any]) -> Dict[str, Any]:
        try:
            async with write_scope() as session:
                result = await session.execute(
                    update(ScriptProjects)
                    .where(ScriptProjects.id == _bigint(record_id))
                    .values(**data)
                    .returning(ScriptProjects)
                )
                row = result.scalars().first()
                return _to_dict(row, _PROJECT_N2A) if row else {}
        except Exception as e:
            logger.error(f"Failed to update script_projects {record_id}: {e}")
            raise

    async def soft_delete(self, record_id: str) -> None:
        """Set status='deleted' (BaseRepository soft-delete contract)."""
        try:
            async with write_scope() as session:
                await session.execute(
                    update(ScriptProjects)
                    .where(ScriptProjects.id == _bigint(record_id))
                    .values(status="deleted")
                )
            logger.info(f"Soft-deleted script_projects {record_id}")
        except Exception as e:
            logger.error(f"Failed to soft-delete script_projects {record_id}: {e}")
            raise

    async def list_by_project(
        self,
        project_id: int,
        page: int = 1,
        limit: int = 20,
        search: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Paginated list of non-deleted scripts for a project, newest-updated
        first, with an exact total count. Mirrors the REST envelope shape
        {items, total, page, limit} and the ilike search escaping."""
        try:
            offset = (page - 1) * limit
            base = select(ScriptProjects).where(
                ScriptProjects.project_id == _bigint(project_id),
                ScriptProjects.status != "deleted",
            )
            if search:
                escaped = search.replace("%", r"\%").replace("_", r"\_")
                base = base.where(ScriptProjects.name.ilike(f"%{escaped}%"))

            async with read_scope() as session:
                total = await session.scalar(
                    select(func.count()).select_from(base.subquery())
                )
                data_result = await session.execute(
                    base.order_by(ScriptProjects.updated_at.desc())
                    .offset(offset)
                    .limit(limit)
                )
                items = [_to_dict(r, _PROJECT_N2A) for r in data_result.scalars().all()]
            return {
                "items": items,
                "total": total or 0,
                "page": page,
                "limit": limit,
            }
        except Exception as e:
            logger.error(
                "Failed to list script projects for project %s: %s", project_id, e
            )
            return {"items": [], "total": 0, "page": page, "limit": limit}


# ─── script_chapters ────────────────────────────────────────────────────


class ScriptChapterRepositoryOrm(ScriptChapterRepository):
    """ORM-backed ScriptChapterRepository (script_chapters)."""

    async def create(self, data: Dict[str, Any]) -> Dict[str, Any]:
        try:
            async with write_scope() as session:
                result = await session.execute(
                    insert(ScriptChapters).values(**data).returning(ScriptChapters)
                )
                row = result.scalars().first()
                if row is None:
                    raise RuntimeError("Insert into script_chapters returned no data")
                out = _to_dict(row, _CHAPTER_N2A)
            logger.info("Created script_chapters record")
            return out
        except Exception as e:
            logger.error(f"Failed to create script_chapters: {e}")
            raise

    async def update(self, record_id: str, data: Dict[str, Any]) -> Dict[str, Any]:
        try:
            async with write_scope() as session:
                result = await session.execute(
                    update(ScriptChapters)
                    .where(ScriptChapters.id == _bigint(record_id))
                    .values(**data)
                    .returning(ScriptChapters)
                )
                row = result.scalars().first()
                return _to_dict(row, _CHAPTER_N2A) if row else {}
        except Exception as e:
            logger.error(f"Failed to update script_chapters {record_id}: {e}")
            raise

    async def hard_delete(self, record_id: str) -> None:
        try:
            async with write_scope() as session:
                await session.execute(
                    delete(ScriptChapters).where(
                        ScriptChapters.id == _bigint(record_id)
                    )
                )
            logger.info(f"Deleted script_chapters {record_id}")
        except Exception as e:
            logger.error(f"Failed to delete script_chapters {record_id}: {e}")
            raise

    async def bulk_upsert(
        self, script_id: str, chapters: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """Upsert (ON CONFLICT id) all chapters, stamping script_id. Returns the
        upserted rows. Committing + idempotent.

        Executed row-by-row inside ONE ``write_scope()`` (a single committing
        transaction): a multi-row VALUES INSERT cannot mix rows that supply an
        explicit ``id`` (existing chapters to update) with rows that omit it
        (new chapters relying on the ``generate_snowflake_id()`` server
        default) — SQLAlchemy raises CompileError on the heterogeneous PK set.
        Per-row statements sidestep that while preserving the supabase-py
        ``upsert(on_conflict="id")`` semantics exactly (insert-or-update by id),
        and the shared transaction keeps the batch atomic."""
        if not chapters:
            return []
        try:
            out: List[Dict[str, Any]] = []
            async with write_scope() as session:
                for ch in chapters:
                    row = {**ch, "script_id": _bigint(script_id)}
                    if "id" in row and row["id"] is not None:
                        row["id"] = _bigint(row["id"])
                    stmt = pg_insert(ScriptChapters).values(**row)
                    # ON CONFLICT (id) DO UPDATE every supplied non-PK column.
                    update_cols = {
                        k: getattr(stmt.excluded, k) for k in row if k != "id"
                    }
                    stmt = stmt.on_conflict_do_update(
                        index_elements=[ScriptChapters.id], set_=update_cols
                    ).returning(ScriptChapters)
                    result = await session.execute(stmt)
                    obj = result.scalars().first()
                    if obj is not None:
                        out.append(_to_dict(obj, _CHAPTER_N2A))
            logger.info(
                f"Bulk-upserted {len(chapters)} chapters for script {script_id}"
            )
            return out
        except Exception as e:
            logger.error(
                "Failed to bulk-upsert chapters for script %s: %s", script_id, e
            )
            raise

    async def get_by_script(self, script_id: str) -> List[Dict[str, Any]]:
        try:
            async with read_scope() as session:
                result = await session.execute(
                    select(ScriptChapters)
                    .where(ScriptChapters.script_id == _bigint(script_id))
                    .order_by(ScriptChapters.sort_order)
                )
                return [_to_dict(r, _CHAPTER_N2A) for r in result.scalars().all()]
        except Exception as e:
            logger.error(f"Failed to get chapters for script {script_id}: {e}")
            return []


# ─── script_assets ──────────────────────────────────────────────────────


class ScriptAssetRepositoryOrm(ScriptAssetRepository):
    """ORM-backed ScriptAssetRepository (script_assets)."""

    async def create(self, data: Dict[str, Any]) -> Dict[str, Any]:
        try:
            async with write_scope() as session:
                result = await session.execute(
                    insert(ScriptAssets).values(**data).returning(ScriptAssets)
                )
                row = result.scalars().first()
                if row is None:
                    raise RuntimeError("Insert into script_assets returned no data")
                out = _to_dict(row, _ASSET_N2A)
            logger.info("Created script_assets record")
            return out
        except Exception as e:
            logger.error(f"Failed to create script_assets: {e}")
            raise

    async def update(self, record_id: str, data: Dict[str, Any]) -> Dict[str, Any]:
        try:
            async with write_scope() as session:
                result = await session.execute(
                    update(ScriptAssets)
                    .where(ScriptAssets.id == _bigint(record_id))
                    .values(**data)
                    .returning(ScriptAssets)
                )
                row = result.scalars().first()
                return _to_dict(row, _ASSET_N2A) if row else {}
        except Exception as e:
            logger.error(f"Failed to update script_assets {record_id}: {e}")
            raise

    async def hard_delete(self, record_id: str) -> None:
        try:
            async with write_scope() as session:
                await session.execute(
                    delete(ScriptAssets).where(ScriptAssets.id == _bigint(record_id))
                )
            logger.info(f"Deleted script_assets {record_id}")
        except Exception as e:
            logger.error(f"Failed to delete script_assets {record_id}: {e}")
            raise

    async def list_by_script(
        self, script_id: str, asset_type: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        try:
            stmt = (
                select(ScriptAssets)
                .where(ScriptAssets.script_id == _bigint(script_id))
                .order_by(ScriptAssets.sort_order)
            )
            if asset_type:
                stmt = stmt.where(ScriptAssets.asset_type == asset_type)
            async with read_scope() as session:
                result = await session.execute(stmt)
                return [_to_dict(r, _ASSET_N2A) for r in result.scalars().all()]
        except Exception as e:
            logger.error(f"Failed to list assets for script {script_id}: {e}")
            return []


# ─── script_storyboard_links ────────────────────────────────────────────


class ScriptStoryboardLinkRepositoryOrm(ScriptStoryboardLinkRepository):
    """ORM-backed ScriptStoryboardLinkRepository (script_storyboard_links)."""

    async def create(self, data: Dict[str, Any]) -> Dict[str, Any]:
        try:
            async with write_scope() as session:
                result = await session.execute(
                    insert(ScriptStoryboardLinks)
                    .values(**data)
                    .returning(ScriptStoryboardLinks)
                )
                row = result.scalars().first()
                if row is None:
                    raise RuntimeError(
                        "Insert into script_storyboard_links returned no data"
                    )
                out = _to_dict(row, _LINK_N2A)
            logger.info("Created script_storyboard_links record")
            return out
        except Exception as e:
            logger.error(f"Failed to create script_storyboard_links: {e}")
            raise

    async def hard_delete(self, record_id: str) -> None:
        try:
            async with write_scope() as session:
                await session.execute(
                    delete(ScriptStoryboardLinks).where(
                        ScriptStoryboardLinks.id == _bigint(record_id)
                    )
                )
            logger.info(f"Deleted script_storyboard_links {record_id}")
        except Exception as e:
            logger.error(f"Failed to delete script_storyboard_links {record_id}: {e}")
            raise

    async def list_by_chapter(self, chapter_id: str) -> List[Dict[str, Any]]:
        try:
            async with read_scope() as session:
                result = await session.execute(
                    select(ScriptStoryboardLinks)
                    .where(ScriptStoryboardLinks.chapter_id == _bigint(chapter_id))
                    .order_by(ScriptStoryboardLinks.created_at)
                )
                return [_to_dict(r, _LINK_N2A) for r in result.scalars().all()]
        except Exception as e:
            logger.error(f"Failed to list links for chapter {chapter_id}: {e}")
            return []

    async def list_by_storyboard(
        self, storyboard_project_id: str
    ) -> List[Dict[str, Any]]:
        try:
            async with read_scope() as session:
                result = await session.execute(
                    select(ScriptStoryboardLinks)
                    .where(
                        ScriptStoryboardLinks.storyboard_project_id
                        == _bigint(storyboard_project_id)
                    )
                    .order_by(ScriptStoryboardLinks.created_at)
                )
                return [_to_dict(r, _LINK_N2A) for r in result.scalars().all()]
        except Exception as e:
            logger.error(
                "Failed to list links for storyboard %s: %s",
                storyboard_project_id,
                e,
            )
            return []


__all__ = [
    "ScriptProjectRepositoryOrm",
    "ScriptChapterRepositoryOrm",
    "ScriptAssetRepositoryOrm",
    "ScriptStoryboardLinkRepositoryOrm",
]
