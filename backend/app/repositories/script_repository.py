"""Script Repository Layer — data access for the script_projects /
script_chapters / script_assets / script_storyboard_links surface.

ORM 2.0 (post-rollout collapse). The four ``Script*Repository`` classes are the
SQLAlchemy 2.0 implementation: every DB method goes through ``read_scope()`` /
``write_scope()`` and builds SELECT *-shaped dicts via ``_orm_obj_to_dict`` +
a precomputed ``_name_to_attr`` map per model. Call sites go through the
``get_script_*_repository()`` factories (bottom of this file), which now
unconditionally return these classes.

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
  found no type-sensitive read — coercion is cheap and matches REST).

  created_at / updated_at : timestamptz → ``.isoformat()`` ALWAYS (the
  unconditional template rule).

  settings_json / viewport_json / data_json / content_json (JSONB) → native
  dict; status / name / display_code / asset_type / text cols → native str;
  position_x / position_y / width / height (double precision) → native float
  (no consumer is type-sensitive — left native per the iron rule).

Write-input audit: create/update/bulk_upsert receive str team_id / created_by /
script_id / project_id / parent_chapter_id / chapter_id /
storyboard_project_id / storyboard_node_id from the service (e.g.
``require_team_id()`` returns str). created_by binds via the Uuid type
processor; every bigint FK/id column is run through ``_coerce_bigint_cols()``
(built on ``_bigint()``) on the write ``values()`` dict before bind
(asyncpg's int8 codec is strict about str-for-bigint — a bare str crashes the
INSERT/UPDATE with a DataError, not a validation error). This was audited and
enforced on 2026-07-04 (fix/script-sb-create-bigint) after prod 500s on
``script_projects`` / ``storyboard_projects`` create surfaced that the
promise above was never wired into the create/update paths, only bulk_upsert
and WHERE clauses. Writes commit via ``write_scope()`` (the silent-rollback
P0 lesson). bulk_upsert is ON CONFLICT (id) — idempotent.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from loguru import logger
from sqlalchemy import delete, func, insert, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.db.session import read_scope, write_scope
from app.models import (
    Projects,
    ScriptAssets,
    ScriptChapters,
    ScriptProjects,
    ScriptStoryboardLinks,
)
from app.repositories._orm_helpers import _name_to_attr, _orm_obj_to_dict
from app.repositories.base_repository import BaseRepository

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


def _coerce_bigint_cols(data: Dict[str, Any], cols: tuple) -> Dict[str, Any]:
    """Return a NEW dict (immutable — never mutate the caller's ``data``) with
    each of ``cols`` coerced through ``_bigint`` when present. Every write path
    that binds a caller-supplied dict containing bigint FK/id columns (team_id /
    project_id / script_id / parent_chapter_id / chapter_id /
    storyboard_project_id / storyboard_node_id) must run it through this first
    — the service layer passes these as ``str`` (e.g. ``require_team_id``
    returns str) and asyncpg's int8 codec rejects a str bind."""
    out = dict(data)
    for col in cols:
        if col in out:
            out[col] = _bigint(out[col])
    return out


# ─── script_projects ────────────────────────────────────────────────────


class ScriptProjectRepository(BaseRepository):
    """CRUD + list operations for script_projects."""

    TABLE_NAME = "script_projects"

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

    async def get_by_episode(self, episode_id: str) -> Optional[Dict[str, Any]]:
        """The live (non-deleted) script currently attached to an episode, if
        any. Mirrors the "existing" lookup inside ``get_or_create_for_episode``
        (same filter, same order) — used by the manual reassign guard
        (``script_projects_router._assert_episode_unowned``) to reject
        attaching a SECOND script to an already-owned episode. That reassign
        path (a plain ``update`` with ``episode_id``) has never enforced "at
        most one script per episode" the way the auto-provision path does
        (advisory lock) — this closes that gap at the APPLICATION level only
        (no new DB constraint: see ``get_or_create_for_episode``'s docstring
        for why a DB unique index on ``episode_id`` is deliberately NOT
        added — existing prod duplicate rows would break it under CI
        auto-apply). An app-level check adds protection going forward
        without touching any existing data or failing a migration."""
        try:
            eid = _bigint(episode_id)
            async with read_scope() as session:
                result = await session.execute(
                    select(ScriptProjects)
                    .where(
                        ScriptProjects.episode_id == eid,
                        ScriptProjects.status != "deleted",
                    )
                    .order_by(ScriptProjects.updated_at.desc())
                    .limit(1)
                )
                row = result.scalars().first()
                return _to_dict(row, _PROJECT_N2A) if row else None
        except Exception as e:
            logger.error(f"Failed to get script_projects by episode {episode_id}: {e}")
            return None

    async def create(self, data: Dict[str, Any]) -> Dict[str, Any]:
        try:
            values = _coerce_bigint_cols(data, ("id", "project_id", "team_id"))
            async with write_scope() as session:
                result = await session.execute(
                    insert(ScriptProjects).values(**values).returning(ScriptProjects)
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
            values = _coerce_bigint_cols(data, ("project_id", "team_id", "episode_id"))
            async with write_scope() as session:
                result = await session.execute(
                    update(ScriptProjects)
                    .where(ScriptProjects.id == _bigint(record_id))
                    .values(**values)
                    .returning(ScriptProjects)
                )
                row = result.scalars().first()
                return _to_dict(row, _PROJECT_N2A) if row else {}
        except Exception as e:
            logger.error(f"Failed to update script_projects {record_id}: {e}")
            raise

    async def get_or_create_for_episode(
        self, data: Dict[str, Any], episode_id: Any
    ) -> Dict[str, Any]:
        """Race-safe get-or-create keyed on ``episode_id`` — the auto-provision
        invariant is "at most ONE non-deleted script per episode".

        A per-episode Postgres advisory xact lock serialises concurrent
        provisions for the SAME episode: a double-fire (double navigation /
        double click while the first provision is still in flight) can never
        insert a second empty row — the loser blocks on the lock until the
        winner commits, then finds and returns the winner's row. This is a true
        kill, not a window-narrowing check-then-insert (which is not race-safe
        without either a unique index or this lock). Mirrors the
        ``pg_advisory_xact_lock`` idiom already documented in
        episode_repository.

        No DB unique index is added deliberately (see PR rationale): the manual
        reassign path (``update`` with ``episode_id``) does NOT enforce this
        invariant, so a partial unique index ``(episode_id) WHERE status<>'deleted'``
        would turn a legitimate reassign-to-occupied-episode into a raw 500, and
        the existing prod duplicate rows would make the ``CREATE INDEX`` itself
        fail under CI auto-apply. The app-level lock is the surgical guard.
        """
        eid = _bigint(episode_id)
        values = _coerce_bigint_cols(
            {**data, "episode_id": eid},
            ("id", "project_id", "team_id", "episode_id"),
        )
        try:
            async with write_scope() as session:
                # Serialise same-episode provisions. hashtextextended(text, int8)
                # → int8 yields a namespaced 64-bit advisory key from the id, so
                # it never collides with episode_repository's hashtext() locks.
                await session.execute(
                    text(
                        "SELECT pg_advisory_xact_lock("
                        "hashtextextended('script_provision:' || :eid, 0))"
                    ),
                    {"eid": str(eid)},
                )
                existing = await session.execute(
                    select(ScriptProjects)
                    .where(
                        ScriptProjects.episode_id == eid,
                        ScriptProjects.status != "deleted",
                    )
                    .order_by(ScriptProjects.updated_at.desc())
                    .limit(1)
                )
                found = existing.scalars().first()
                if found is not None:
                    out = _to_dict(found, _PROJECT_N2A)
                    logger.info(
                        f"Reused existing script for episode {episode_id} "
                        "(get-or-create)"
                    )
                    return out
                result = await session.execute(
                    insert(ScriptProjects).values(**values).returning(ScriptProjects)
                )
                row = result.scalars().first()
                if row is None:
                    raise RuntimeError("Insert into script_projects returned no data")
                out = _to_dict(row, _PROJECT_N2A)
            logger.info(f"Created script for episode {episode_id} (get-or-create)")
            return out
        except Exception as e:
            logger.error(
                f"Failed to get-or-create script for episode {episode_id}: {e}"
            )
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
                f"Failed to list script projects for project {project_id}: {e}"
            )
            return {"items": [], "total": 0, "page": page, "limit": limit}

    async def list_recent_for_user(
        self, user_id: str, limit: int = 8
    ) -> List[Dict[str, Any]]:
        """Recently-edited non-deleted scripts across every project OWNED by
        ``user_id``, newest-edited first, capped at ``limit``.

        Joins ``projects`` for owner scoping (mirrors the projects-list
        endpoint's ``owner_id == user_id`` visibility), the project name, AND
        the PROJECT's ``team_id`` — deliberately ``Projects.team_id``, not
        ``script_projects.team_id`` (script_projects.team_id is NOT NULL and
        falls back to the creator's personal team even for a personal
        project, so using it would misroute a personal script to a team
        route). The Recent view spans every team the caller owns projects
        in, not just the team currently open in the UI, so each item must
        carry its own team for the frontend to navigate to it directly
        instead of assuming "current page's team" (cross-team recent items
        were silently unopenable before this field existed). One query — no
        per-script project lookup. Returns the recent-items wire shape
        ``{id, name, project_id, project_name, team_id, updated_at}`` with
        bigint ids stringified (JS precision; the recent-view contract uses
        string ids, unlike the rest of this repo which keeps bigints native;
        ``team_id`` is ``None`` for a personal project with no team).
        Never raises — a failure degrades the Recent view to empty."""
        try:
            async with read_scope() as session:
                result = await session.execute(
                    select(
                        ScriptProjects.id,
                        ScriptProjects.name,
                        ScriptProjects.project_id,
                        ScriptProjects.updated_at,
                        Projects.name.label("project_name"),
                        Projects.team_id,
                    )
                    .join(Projects, Projects.id == ScriptProjects.project_id)
                    .where(Projects.owner_id == user_id)
                    .where(ScriptProjects.status != "deleted")
                    .order_by(ScriptProjects.updated_at.desc())
                    .limit(limit)
                )
                rows = result.mappings().all()
            return [
                {
                    "id": str(r["id"]),
                    "name": r["name"],
                    "project_id": str(r["project_id"]),
                    "project_name": r["project_name"],
                    "team_id": (
                        str(r["team_id"]) if r["team_id"] is not None else None
                    ),
                    "updated_at": (
                        r["updated_at"].isoformat() if r["updated_at"] else None
                    ),
                }
                for r in rows
            ]
        except Exception as e:
            logger.error(f"Failed to list recent scripts for user {user_id}: {e}")
            return []


# ─── script_chapters ────────────────────────────────────────────────────


class ScriptChapterRepository(BaseRepository):
    """CRUD for script_chapters."""

    TABLE_NAME = "script_chapters"

    async def create(self, data: Dict[str, Any]) -> Dict[str, Any]:
        try:
            values = _coerce_bigint_cols(data, ("id", "script_id", "parent_chapter_id"))
            async with write_scope() as session:
                result = await session.execute(
                    insert(ScriptChapters).values(**values).returning(ScriptChapters)
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
            values = _coerce_bigint_cols(data, ("script_id", "parent_chapter_id"))
            async with write_scope() as session:
                result = await session.execute(
                    update(ScriptChapters)
                    .where(ScriptChapters.id == _bigint(record_id))
                    .values(**values)
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

    async def delete(self, chapter_id: str) -> None:
        """Hard-delete a chapter by ID."""
        await self.hard_delete(chapter_id)

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
                    row = _coerce_bigint_cols(
                        {**ch, "script_id": script_id},
                        ("id", "script_id", "parent_chapter_id"),
                    )
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
            logger.error(f"Failed to bulk-upsert chapters for script {script_id}: {e}")
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


class ScriptAssetRepository(BaseRepository):
    """CRUD for script_assets."""

    TABLE_NAME = "script_assets"

    async def create(self, data: Dict[str, Any]) -> Dict[str, Any]:
        try:
            values = _coerce_bigint_cols(data, ("id", "script_id"))
            async with write_scope() as session:
                result = await session.execute(
                    insert(ScriptAssets).values(**values).returning(ScriptAssets)
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
            values = _coerce_bigint_cols(data, ("script_id",))
            async with write_scope() as session:
                result = await session.execute(
                    update(ScriptAssets)
                    .where(ScriptAssets.id == _bigint(record_id))
                    .values(**values)
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

    async def delete(self, asset_id: str) -> None:
        """Hard-delete an asset by ID."""
        await self.hard_delete(asset_id)

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


class ScriptStoryboardLinkRepository(BaseRepository):
    """CRUD for script_storyboard_links."""

    TABLE_NAME = "script_storyboard_links"

    async def create(self, data: Dict[str, Any]) -> Dict[str, Any]:
        try:
            values = _coerce_bigint_cols(
                data,
                ("id", "chapter_id", "storyboard_project_id", "storyboard_node_id"),
            )
            async with write_scope() as session:
                result = await session.execute(
                    insert(ScriptStoryboardLinks)
                    .values(**values)
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

    async def delete(self, link_id: str) -> None:
        """Hard-delete a link by ID."""
        await self.hard_delete(link_id)

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
                f"Failed to list links for storyboard {storyboard_project_id}: {e}"
            )
            return []


# ─── repository factories ─────────────────────────────────────────────────
#
# Post-rollout the four factories unconditionally return their (now ORM-only)
# class — no flag branch, no engine-missing fallback. Kept as factories so the
# script_service call sites are zero-touch.


def get_script_project_repository() -> ScriptProjectRepository:
    """Return the ScriptProjectRepository (ORM-backed script_projects)."""
    return ScriptProjectRepository()


def get_script_chapter_repository() -> ScriptChapterRepository:
    """Return the ScriptChapterRepository (ORM-backed script_chapters)."""
    return ScriptChapterRepository()


def get_script_asset_repository() -> ScriptAssetRepository:
    """Return the ScriptAssetRepository (ORM-backed script_assets)."""
    return ScriptAssetRepository()


def get_script_storyboard_link_repository() -> ScriptStoryboardLinkRepository:
    """Return the ScriptStoryboardLinkRepository (ORM-backed links)."""
    return ScriptStoryboardLinkRepository()
