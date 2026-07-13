# app/repositories/projects_repository.py

"""Projects Repository — SQLAlchemy 2.0 ORM data access for the MediaTrack
project system (9 tables: projects / project_files / project_folders /
project_members / file_versions / review_comments /
parsed_media / shares / project_collections).

ORM-only (post-rollout collapse — the ``USE_ORM_PROJECTS`` flag and the separate
``ProjectsRepositoryOrm`` subclass have been retired; the ORM bodies now live
directly on ``ProjectsRepository``). Every genuine DB-touching method runs on the
SQLAlchemy session layer. Writes commit via ``write_scope()`` (the silent-
rollback P0 lesson); reads use ``read_scope()``. Error handling: reads swallow +
return None/[]/0 on failure; writes (create_*/update_*/delete_*) log + re-raise.

CONSCIOUS-KEEPS (still on the legacy supabase-py path — this is why the
``get_async_supabase_admin`` import + ``_get_client`` helper are retained):

  - ``enrich_members_with_email`` / ``get_user_email`` — AUTH-API precedent.
    These call ``client.auth.admin`` (Supabase Auth / GoTrue), not a DB table, so
    they are PERMANENTLY out of ORM scope per the migration plan. Never ORM-
    overridden; kept on the auth-admin path. The ORM ``get_members`` below str()s
    user_id (uuid → str), which ``enrich_members_with_email`` needs for its
    ``user_map.get(m["user_id"])`` lookup against ``str(u.id)`` keys.
  (RESOLVED — no longer a conscious-keep) ``get_comments_for_file`` /
  ``create_comment`` / ``get_comment_by_id`` / ``delete_comment`` used to target
  the dropped 043-era ``review_comments`` columns (migration 062 recreated that
  table with resource_id / timecode, no file_id / timestamp_seconds /
  drawing_data), so they were ALREADY 500-ing in production. The deferred
  product decision has been made: a dedicated ``project_file_comments`` table
  (migration 335) now owns this surface, keyed by ``project_files.id`` /
  ``file_versions.id`` (see ``ProjectFileComments`` in ``app/models/teams.py``).
  These four methods now run on the ORM path against that table — see the
  section comment above the methods below.
  (RESOLVED — no longer conscious-keeps) ``update_member`` / ``delete_member``
  used to filter a phantom ``id`` column (project_members has a composite PK
  ``(user_id + project_id)`` and NO ``id``), so they were a SILENT NO-OP in
  production. They now run on the ORM path, identified by ``(project_id,
  user_id)`` — ``member_id`` on the route/wire is the member's ``user_id``.
  ``get_members`` / ``create_member`` were already genuine non-drifted ORM ops.

STRATEGY C — VALUE-TYPE PARITY (per-field, exact former-REST shape)
===================================================================
Supabase REST rendered JSON: bigint → int, uuid → str, timestamptz → ISO str,
date → 'YYYY-MM-DD' str, numeric → str, jsonb → dict. The ORM returns native
types. We coerce ONLY where it matters, to the exact REST shape:

  bigint ids (projects.id / project_files.id / project_folders.id /
    file_versions.id / shares.id / project_collections.id /
    project_members.project_id / parsed_media.id, plus every bigint FK such as
    project_id / media_id / folder_id / parent_id / file_id / project_file_id /
    file_size_bytes) → STAY NATIVE int (the 5.3 trap — these flow into scope /
    membership / FK / dict-key comparisons; a snowflake bigint coerced to str
    would silently break scoping/joins).

  timestamptz (created_at / updated_at / trashed_at / joined_at / expires_at /
    deadline / …) → ``.isoformat()`` ALWAYS. Consumers (response models / the
    frontend) parse ISO; ``==`` / ordering on a native datetime vs an ISO str
    would diverge.

  date columns: none remain in this repo's scope (the sole one,
    ``project_tasks.due_date``, was removed with the table in migration 176 —
    see PR-A3). ``_coerce_temporal`` still narrows a ``datetime`` bound to a
    ``date`` column defensively (``datetime`` sweep runs before ``date``) in
    case a future table reintroduces one.

  uuid columns → str (REST returned str). CONSUMED type-sensitively:
    projects.owner_id (``owner_id != user_id`` ownership checks in update_project
    / delete_project / update_review_status), project_members.user_id (the
    enrich-email dict key), review_comments.author_id. All other uuid columns are
    str for shape parity. A generic uuid-sweep on the output dict reproduces the
    REST shape for every uuid column at once.

  numeric (project_files.fps / file_versions.fps : Numeric; review_comments
    .timecode / parsed_media.download_duration : Double) → LEFT NATIVE (no type-
    sensitive consumer; the iron rule — don't coerce fields nobody compares).

  parsed_media Enum(DownloadStatus) columns are unwrapped to bare strings and the
  renamed ``metadata_`` → 'metadata' column is resolved via the mapper
  (``_orm_obj_to_dict`` / ``_name_to_attr``).

Phantom columns (graceful no-op parity):
  - DISPLAY_CODE (projects): the Projects model has no ``display_code`` column;
    the service's create_project flow does ``update_project(id, {"display_code":
    ...})`` for team projects, wrapped in try/except that swallowed a REST PGRST
    'column does not exist'. Unknown keys are filtered out of write ``values()``
    (``_known_only``) + logged at debug — preserving that swallowed-no-op EXACTLY
    (the ORM would otherwise raise ``unexpected keyword``).
  - Co-fixed prod bug: the legacy ``get_members`` ordered by a phantom
    ``created_at`` column (migration 070's ``ADD COLUMN IF NOT EXISTS`` was a
    no-op over the 047 schema that only has ``joined_at``), so PostgREST 400'd,
    the ``except`` swallowed it, and ``list_members`` silently returned ``[]`` in
    production. Now orders by ``joined_at`` (the real column).

Date/timestamp FILTER binding: there are NO date/timestamp RANGE filters in this
repo — every query filters by equality (id / project_id / file_id / folder_id /
parent_id) or IN, plus ``is_trashed`` bool and ordering. So there is no
timestamptz<VARCHAR binding hazard on reads. Writes coerce ISO-string temporal
values back to native ``date`` / ``datetime`` at the write boundary (asyncpg does
not auto-coerce ISO strings under SQLAlchemy's Date / DateTime — see
``_coerce_temporal``); PostgREST DID, and the request schemas type ``due_date`` /
``deadline`` as ``Optional[str]``.
"""

from __future__ import annotations

import datetime as _dt
import uuid as _uuid
from typing import Any, Dict, List, Optional

from loguru import logger
from sqlalchemy import delete, func, insert, select, update

from app.db.session import read_scope, write_scope

# Retained for the conscious-keep methods (auth-admin + deferred REST surfaces)
# that were never migrated to the ORM path — see the module docstring.
from app.db.supabase_client import get_async_supabase_admin
from app.models import (
    FileVersions,
    ParsedMedia,
    ProjectCollections,
    ProjectFileComments,
    ProjectFiles,
    ProjectFolders,
    ProjectMembers,
    Projects,
    Resources,
    Shares,
)
from app.repositories._orm_helpers import _name_to_attr, _orm_obj_to_dict

# Precomputed DB-column-name → mapped-attribute-name maps (built once). Used so
# reads produce SELECT *-shaped dicts keyed by DB column name, resolving any
# renamed column (e.g. parsed_media.metadata_ → "metadata") via the mapper.
_PROJECTS_N2A: Dict[str, str] = _name_to_attr(Projects)
_FILES_N2A: Dict[str, str] = _name_to_attr(ProjectFiles)
_FOLDERS_N2A: Dict[str, str] = _name_to_attr(ProjectFolders)
_MEMBERS_N2A: Dict[str, str] = _name_to_attr(ProjectMembers)
_VERSIONS_N2A: Dict[str, str] = _name_to_attr(FileVersions)
_MEDIA_N2A: Dict[str, str] = _name_to_attr(ParsedMedia)
_SHARES_N2A: Dict[str, str] = _name_to_attr(Shares)
_COLLECTIONS_N2A: Dict[str, str] = _name_to_attr(ProjectCollections)
_COMMENTS_N2A: Dict[str, str] = _name_to_attr(ProjectFileComments)

# Mapped attribute names per model — for filtering unknown keys out of write
# values() (the DISPLAY_CODE / arbitrary-dict graceful-no-op contract). Bigint
# ids / FK columns are NOT in any of these sweep lists, so they stay native int.
_PROJECTS_ATTRS = {p.key for p in Projects.__mapper__.column_attrs}
_FILES_ATTRS = {p.key for p in ProjectFiles.__mapper__.column_attrs}
_FOLDERS_ATTRS = {p.key for p in ProjectFolders.__mapper__.column_attrs}
_MEMBERS_ATTRS = {p.key for p in ProjectMembers.__mapper__.column_attrs}
_VERSIONS_ATTRS = {p.key for p in FileVersions.__mapper__.column_attrs}
_SHARES_ATTRS = {p.key for p in Shares.__mapper__.column_attrs}
_COLLECTIONS_ATTRS = {p.key for p in ProjectCollections.__mapper__.column_attrs}
_COMMENTS_ATTRS = {p.key for p in ProjectFileComments.__mapper__.column_attrs}


def _temporal_kinds(model: Any) -> Dict[str, str]:
    """Map mapped-attribute-name → 'date' | 'datetime' for the model's temporal
    columns. Built from the mapper so it tracks the schema.

    WHY (write-binding hazard): asyncpg (under SQLAlchemy's Date / DateTime types)
    requires NATIVE ``datetime.date`` / ``datetime`` bind values and does NOT
    auto-coerce an ISO string — ``INSERT ... due_date=$ ('2026-06-09')`` raises
    ``DataError: 'str' object has no attribute 'toordinal'``. PostgREST DID auto-
    coerce ISO strings, and the request schemas type ``due_date`` / ``deadline``
    as ``Optional[str]`` (ISO), so those strings flow straight into write
    ``values()``. We coerce them back to native types at the write boundary (see
    ``_coerce_temporal``)."""
    from sqlalchemy import Date, DateTime

    out: Dict[str, str] = {}
    for prop in model.__mapper__.column_attrs:
        col = prop.columns[0]
        coltype = col.type
        if isinstance(coltype, DateTime):
            out[prop.key] = "datetime"
        elif isinstance(coltype, Date):
            out[prop.key] = "date"
    return out


_FILES_TEMPORAL = _temporal_kinds(ProjectFiles)
_COLLECTIONS_TEMPORAL = _temporal_kinds(ProjectCollections)
_SHARES_TEMPORAL = _temporal_kinds(Shares)
_VERSIONS_TEMPORAL = _temporal_kinds(FileVersions)
_PROJECTS_TEMPORAL = _temporal_kinds(Projects)
_FOLDERS_TEMPORAL = _temporal_kinds(ProjectFolders)


def _coerce_temporal(data: Dict[str, Any], kinds: Dict[str, str]) -> Dict[str, Any]:
    """Coerce ISO-string values for temporal columns to native ``date`` /
    ``datetime`` so asyncpg can bind them (see ``_temporal_kinds``). Native
    ``date`` / ``datetime`` values and non-temporal columns pass through. Returns
    a NEW dict (no mutation). A ``datetime`` bound to a ``date`` column is
    narrowed to ``.date()``; a date-only string to a ``datetime`` column becomes
    midnight UTC (tz-aware, matching the engine's UTC convention)."""
    out = dict(data)
    for key, kind in kinds.items():
        val = out.get(key)
        if val is None or not isinstance(val, str):
            continue
        parsed = _dt.datetime.fromisoformat(val)
        if kind == "date":
            out[key] = parsed.date()
        else:  # datetime
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=_dt.timezone.utc)
            out[key] = parsed
    return out


def _coerce_id_ints(values: Dict[str, Any], keys: tuple[str, ...]) -> Dict[str, Any]:
    """Coerce Snowflake-id values to native ``int`` for asyncpg BIGINT binding.

    Routers and services carry ids as ``str`` end-to-end (JS precision), but
    asyncpg binds BIGINT params strictly — a str raises ``DataError`` instead
    of coercing (the exact failure that left ``project_files`` empty in prod:
    every ``create_file`` since the ORM cutover 500'd on ``$2::BIGINT``).
    Returns a NEW dict (no mutation)."""
    out = dict(values)
    for key in keys:
        if out.get(key) is not None:
            out[key] = int(out[key])
    return out


def _parity(out: Dict[str, Any]) -> Dict[str, Any]:
    """Apply strategy-C value-type parity IN PLACE on a SELECT *-shaped dict:

    - any ``uuid.UUID`` value → str (REST returned strings; the audited
      consumers do ``== user_id`` / dict-key lookups that break on native UUID).
    - any ``datetime`` → ``.isoformat()`` (REST ISO; ``==``/ordering/``str()``
      footgun). Checked BEFORE ``date`` because ``datetime`` ⊂ ``date``.
    - any ``date`` → ``.isoformat()`` → 'YYYY-MM-DD' (REST shape). No mapped
      column in this repo's current scope is a bare ``date`` (the sole prior
      example, project_tasks.due_date, was removed with the table in
      migration 176), but the sweep stays generic for future columns.
    - bigint ids / FKs and numeric (Decimal/float) → LEFT NATIVE (the 5.3 trap
      + the iron rule). NULLs pass through.
    """
    for key, value in out.items():
        if isinstance(value, _uuid.UUID):
            out[key] = str(value)
        elif isinstance(value, _dt.datetime):
            out[key] = value.isoformat()
        elif isinstance(value, _dt.date):
            out[key] = value.isoformat()
    return out


def _row(obj: Any, name_to_attr: Dict[str, str]) -> Dict[str, Any]:
    """SELECT *-shaped, strategy-C-parity dict for one ORM row (enum-unwrapped
    via ``_orm_obj_to_dict`` + ``_plain``, then uuid/datetime/date coerced)."""
    return _parity(_orm_obj_to_dict(obj, name_to_attr))


def _comment_row(obj: Any) -> Dict[str, Any]:
    """project_file_comments row → dict, with id/file_id/version_id stringified.

    Unlike the rest of this repo's bigint ids/FKs (which STAY native int — the
    5.3 trap), the comment surface's wire contract (``CommentResponse`` schema
    / frontend ``ReviewComment``) expects string ids, matching the legacy
    review_comments REST shape this surface replaces. ``_row`` alone leaves
    bigints native, so we stringify the three id-shaped fields here rather
    than in the general helper (which every other table relies on staying
    native for scope/FK comparisons)."""
    out = _row(obj, _COMMENTS_N2A)
    for key in ("id", "file_id", "version_id"):
        if out.get(key) is not None:
            out[key] = str(out[key])
    return out


def _known_only(
    data: Dict[str, Any],
    attrs: set[str],
    temporal: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """Build the write ``values()`` dict for a model: drop keys that are not
    mapped columns (preserving the legacy REST graceful-no-op for phantom
    columns like projects.display_code), then coerce ISO-string temporal values
    to native ``date`` / ``datetime`` (the asyncpg binding hazard — see
    ``_coerce_temporal``). Logs dropped keys once at debug. Returns a NEW dict."""
    known = {k: v for k, v in data.items() if k in attrs}
    dropped = [k for k in data if k not in attrs]
    if dropped:
        logger.debug(
            f"ProjectsRepository: dropping non-column keys {dropped} "
            "(phantom-column graceful no-op, REST parity)"
        )
    if temporal:
        known = _coerce_temporal(known, temporal)
    return known


class ProjectsRepository:
    """Projects and project files data access (async, SQLAlchemy 2.0 ORM).

    Every genuine DB-touching method on the 9 project tables runs on the ORM
    session layer. Only the auth-admin methods (enrich_members_with_email /
    get_user_email) stay on the legacy supabase path — see the module
    docstring."""

    TABLE_PROJECTS = "projects"
    TABLE_FILES = "project_files"
    TABLE_MEDIA = "parsed_media"
    TABLE_VERSIONS = "file_versions"
    TABLE_COMMENTS = "project_file_comments"
    TABLE_FOLDERS = "project_folders"
    TABLE_SHARES = "shares"
    TABLE_MEMBERS = "project_members"
    TABLE_COLLECTIONS = "project_collections"

    def __init__(self):
        pass

    async def _get_client(self):
        """Get async supabase client (loop-aware, safe for Celery workers).

        Retained for the conscious-keep methods only (auth-admin +
        review_comments) — see the module docstring."""
        return await get_async_supabase_admin()

    # ------------------------------------------------------------------ #
    # Projects CRUD
    # ------------------------------------------------------------------ #

    async def get_user_projects(
        self,
        user_id: str,
        team_id: str | None = None,
        project_type: str | None = None,
        starred: bool | None = None,
        archived: bool | None = False,
    ) -> List[Dict[str, Any]]:
        """
        Get projects accessible to a user, ordered by updated_at desc.
        Filters push down to SQL (they used to be applied in-memory in the
        router, and there was no ``archived`` filter at all).

        Args:
            user_id: UUID of the authenticated user.
            team_id: If provided, filter by team_id. If ``"personal"``,
                     return only projects where team_id IS NULL.
                     If None, return all user projects (no team filter).
            project_type: If provided, filter by project_type.
            starred: If provided, filter by is_starred.
            archived: ``False`` (default) → active only (archived_at IS
                NULL). ``True`` → archived only (archived_at IS NOT NULL).
                ``None`` → both (no archived_at filter).

        Returns:
            List of project row dicts.
        """
        try:
            stmt = (
                select(Projects)
                .where(Projects.owner_id == user_id)
                .order_by(Projects.updated_at.desc())
            )
            if team_id == "personal":
                stmt = stmt.where(Projects.team_id.is_(None))
            elif team_id:
                stmt = stmt.where(Projects.team_id == int(team_id))
            if project_type:
                stmt = stmt.where(Projects.project_type == project_type)
            if starred is not None:
                stmt = stmt.where(Projects.is_starred.is_(starred))
            if archived is True:
                stmt = stmt.where(Projects.archived_at.is_not(None))
            elif archived is False:
                stmt = stmt.where(Projects.archived_at.is_(None))
            async with read_scope() as session:
                result = await session.execute(stmt)
                return [_row(r, _PROJECTS_N2A) for r in result.scalars().all()]
        except Exception as e:
            logger.error(f"Failed to get projects for user {user_id}: {e}")
            return []

    async def get_project_by_id(self, project_id: str) -> Optional[Dict[str, Any]]:
        """
        Get a single project by its UUID.

        Args:
            project_id: UUID of the project.

        Returns:
            Project row dict or None.
        """
        try:
            async with read_scope() as session:
                result = await session.execute(
                    select(Projects).where(Projects.id == int(project_id)).limit(1)
                )
                row = result.scalars().first()
                return _row(row, _PROJECTS_N2A) if row else None
        except Exception as e:
            logger.error(f"Failed to get project {project_id}: {e}")
            return None

    async def create_project(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Create a new project.

        Args:
            data: Project data dict.

        Returns:
            Created project row dict.
        """
        try:
            values = _known_only(data, _PROJECTS_ATTRS, _PROJECTS_TEMPORAL)
            # team_id arrives as a str from the request schema but the column is
            # BIGINT and asyncpg's int8 codec is strict — coerce like the read
            # path (list_projects) already does, or the INSERT 500s (DataError).
            if values.get("team_id") is not None:
                values["team_id"] = int(values["team_id"])
            async with write_scope() as session:
                result = await session.execute(
                    insert(Projects).values(**values).returning(Projects)
                )
                row = result.scalars().first()
                out = _row(row, _PROJECTS_N2A) if row else {}
            logger.info(f"Created project: {data.get('name')}")
            return out
        except Exception as e:
            logger.error(f"Failed to create project: {e}")
            raise

    async def update_project(
        self, project_id: str, data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Update an existing project.

        Args:
            project_id: UUID of the project.
            data: Fields to update.

        Returns:
            Updated project row dict.
        """
        try:
            values = _known_only(data, _PROJECTS_ATTRS, _PROJECTS_TEMPORAL)
            if not values:
                # All keys phantom (e.g. {"display_code": ...} on a model without
                # it) → REST no-op'd via swallowed PGRST; return the current row.
                current = await self.get_project_by_id(project_id)
                return current or {}
            async with write_scope() as session:
                result = await session.execute(
                    update(Projects)
                    .where(Projects.id == int(project_id))
                    .values(**values)
                    .returning(Projects)
                )
                row = result.scalars().first()
                out = _row(row, _PROJECTS_N2A) if row else {}
            logger.info(f"Updated project {project_id}")
            return out
        except Exception as e:
            logger.error(f"Failed to update project {project_id}: {e}")
            raise

    async def delete_project(self, project_id: str) -> bool:
        """
        Delete a project by its UUID. Cascade deletes files.

        Args:
            project_id: UUID of the project.

        Returns:
            True if deleted successfully.
        """
        try:
            async with write_scope() as session:
                await session.execute(
                    delete(Projects).where(Projects.id == int(project_id))
                )
            logger.info(f"Deleted project {project_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to delete project {project_id}: {e}")
            raise

    # ------------------------------------------------------------------ #
    # File count
    # ------------------------------------------------------------------ #

    async def get_project_file_count(self, project_id: str) -> int:
        """
        Get the number of non-trashed files in a project.

        Args:
            project_id: UUID of the project.

        Returns:
            File count integer.
        """
        try:
            async with read_scope() as session:
                total = await session.scalar(
                    select(func.count())
                    .select_from(ProjectFiles)
                    .where(ProjectFiles.project_id == int(project_id))
                    .where(ProjectFiles.is_trashed.is_(False))
                )
            return total or 0
        except Exception as e:
            logger.error(f"Failed to get file count for project {project_id}: {e}")
            return 0

    async def get_project_file_counts(self, project_ids: List[str]) -> Dict[str, int]:
        """
        Non-trashed file counts for many projects in ONE query (replaces the
        per-project N+1 in ``get_projects_with_counts``, which used to fire
        one ``get_project_file_count`` query per project via ``asyncio.gather``).

        Args:
            project_ids: List of project ids (str or int).

        Returns:
            Dict mapping str(project_id) -> file count. Projects with zero
            non-trashed files are simply absent (GROUP BY yields no row for
            them) — callers should ``.get(str(pid), 0)``.
        """
        if not project_ids:
            return {}
        try:
            async with read_scope() as session:
                result = await session.execute(
                    select(ProjectFiles.project_id, func.count())
                    .where(ProjectFiles.project_id.in_([int(p) for p in project_ids]))
                    .where(ProjectFiles.is_trashed.is_(False))
                    .group_by(ProjectFiles.project_id)
                )
                return {str(pid): count for pid, count in result.all()}
        except Exception as e:
            logger.error(f"Failed to get file counts: {e}")
            return {}

    async def get_project_members_preview(
        self, project_ids: List[str], limit: int = 3
    ) -> Dict[str, Dict[str, Any]]:
        """Member preview for many projects in ONE pass (list page, B1).

        Returns ``{str(project_id): {"count": int, "members": [{"user_id",
        "username"}, ...]}}`` with at most ``limit`` members each (joined_at
        order). Usernames come from ``user_profiles`` (same enrich pattern as
        team_repository); a user with no profile row keeps ``username=""``.
        Projects with no member rows are absent. Never raises — the list page
        degrades to member-less cards.
        """
        if not project_ids:
            return {}
        try:
            async with read_scope() as session:
                result = await session.execute(
                    select(
                        ProjectMembers.project_id,
                        ProjectMembers.user_id,
                    )
                    .where(ProjectMembers.project_id.in_([int(p) for p in project_ids]))
                    .order_by(ProjectMembers.project_id, ProjectMembers.joined_at)
                )
                rows = result.all()
                if not rows:
                    return {}

                grouped: Dict[str, Dict[str, Any]] = {}
                user_ids: set = set()
                for pid, uid in rows:
                    entry = grouped.setdefault(str(pid), {"count": 0, "members": []})
                    entry["count"] += 1
                    if len(entry["members"]) < limit:
                        entry["members"].append({"user_id": str(uid), "username": ""})
                        user_ids.add(uid)

                if user_ids:
                    from app.models.users import UserProfiles

                    names = await session.execute(
                        select(UserProfiles.id, UserProfiles.username).where(
                            UserProfiles.id.in_(list(user_ids))
                        )
                    )
                    by_id = {str(i): (u or "") for i, u in names.all()}
                    for entry in grouped.values():
                        for m in entry["members"]:
                            m["username"] = by_id.get(m["user_id"], "")
                return grouped
        except Exception as e:  # noqa: BLE001 — enrichment must not sink the list
            logger.error(f"Failed to get member previews: {e}")
            return {}

    # ------------------------------------------------------------------ #
    # Files CRUD
    # ------------------------------------------------------------------ #

    async def get_project_files(
        self, project_id: str, include_trashed: bool = False
    ) -> List[Dict[str, Any]]:
        """
        Get files in a project, ordered by created_at desc.

        Args:
            project_id: UUID of the project.
            include_trashed: Whether to include trashed files.

        Returns:
            List of file row dicts.
        """
        try:
            stmt = select(ProjectFiles).where(
                ProjectFiles.project_id == int(project_id)
            )
            if not include_trashed:
                stmt = stmt.where(ProjectFiles.is_trashed.is_(False))
            stmt = stmt.order_by(ProjectFiles.created_at.desc())
            async with read_scope() as session:
                result = await session.execute(stmt)
                return [_row(r, _FILES_N2A) for r in result.scalars().all()]
        except Exception as e:
            logger.error(f"Failed to get files for project {project_id}: {e}")
            return []

    async def get_file_by_id(self, file_id: str) -> Optional[Dict[str, Any]]:
        """
        Get a single file by its UUID.

        Args:
            file_id: UUID of the file.

        Returns:
            File row dict or None.
        """
        try:
            async with read_scope() as session:
                result = await session.execute(
                    select(ProjectFiles).where(ProjectFiles.id == int(file_id)).limit(1)
                )
                row = result.scalars().first()
                return _row(row, _FILES_N2A) if row else None
        except Exception as e:
            logger.error(f"Failed to get file {file_id}: {e}")
            return None

    async def create_file(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Create a new file record.

        Args:
            data: File data dict.

        Returns:
            Created file row dict.
        """
        try:
            values = _coerce_id_ints(
                _known_only(data, _FILES_ATTRS, _FILES_TEMPORAL),
                ("project_id", "folder_id", "media_id"),
            )
            async with write_scope() as session:
                result = await session.execute(
                    insert(ProjectFiles).values(**values).returning(ProjectFiles)
                )
                row = result.scalars().first()
                out = _row(row, _FILES_N2A) if row else {}
            logger.info(
                f"Created file '{data.get('filename')}' in project "
                f"{data.get('project_id')}"
            )
            return out
        except Exception as e:
            logger.error(f"Failed to create file: {e}")
            raise

    async def update_file(self, file_id: str, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Update an existing file record.

        Args:
            file_id: UUID of the file.
            data: Fields to update.

        Returns:
            Updated file row dict.
        """
        try:
            values = _coerce_id_ints(
                _known_only(data, _FILES_ATTRS, _FILES_TEMPORAL),
                ("project_id", "folder_id", "media_id"),
            )
            if not values:
                current = await self.get_file_by_id(file_id)
                return current or {}
            async with write_scope() as session:
                result = await session.execute(
                    update(ProjectFiles)
                    .where(ProjectFiles.id == int(file_id))
                    .values(**values)
                    .returning(ProjectFiles)
                )
                row = result.scalars().first()
                out = _row(row, _FILES_N2A) if row else {}
            logger.info(f"Updated file {file_id}")
            return out
        except Exception as e:
            logger.error(f"Failed to update file {file_id}: {e}")
            raise

    async def delete_file(self, file_id: str) -> bool:
        """
        Delete a file record by its UUID.

        Args:
            file_id: UUID of the file.

        Returns:
            True if deleted successfully.
        """
        try:
            async with write_scope() as session:
                await session.execute(
                    delete(ProjectFiles).where(ProjectFiles.id == int(file_id))
                )
            logger.info(f"Deleted file {file_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to delete file {file_id}: {e}")
            raise

    # ------------------------------------------------------------------ #
    # Media metadata (for link-media feature)
    # ------------------------------------------------------------------ #

    async def get_media_metadata(self, media_id: str) -> Optional[Dict[str, Any]]:
        """
        Fetch media metadata from the parsed_media table for linking.

        Args:
            media_id: UUID of the media.

        Returns:
            Media row dict or None.
        """
        try:
            async with read_scope() as session:
                result = await session.execute(
                    select(ParsedMedia).where(ParsedMedia.id == int(media_id)).limit(1)
                )
                row = result.scalars().first()
                # parsed_media has Enum(DownloadStatus) columns + a renamed
                # metadata_ → "metadata" column; _row handles both via
                # _orm_obj_to_dict (_plain) + _name_to_attr.
                return _row(row, _MEDIA_N2A) if row else None
        except Exception as e:
            logger.error(f"Failed to get media metadata {media_id}: {e}")
            return None

    async def get_media_creator(self, media_id: str) -> Optional[str]:
        """creator_id of the resources row linked to this parsed_media id,
        or None when no resource row exists (orphan/system media).

        parsed_media has no ownership column (dropped in migration 083);
        ownership lives on resources.creator_id via resources.media_id.

        This backs a security check, so it FAILS CLOSED: raises on lookup
        failure — callers must not treat an error as an ownership pass.
        """
        try:
            async with read_scope() as session:
                creator = await session.scalar(
                    select(Resources.creator_id)
                    .where(Resources.media_id == int(media_id))
                    .limit(1)
                )
                return str(creator) if creator else None
        except Exception as e:
            logger.error(f"Failed to get media creator for {media_id}: {e}")
            raise

    # ------------------------------------------------------------------ #
    # File versions
    # ------------------------------------------------------------------ #

    async def get_file_versions(self, file_id: str) -> List[Dict[str, Any]]:
        """Get all versions of a file, ordered by version_number DESC."""
        try:
            async with read_scope() as session:
                result = await session.execute(
                    select(FileVersions)
                    .where(FileVersions.file_id == int(file_id))
                    .order_by(FileVersions.version_number.desc())
                )
                return [_row(r, _VERSIONS_N2A) for r in result.scalars().all()]
        except Exception as e:
            logger.error(f"Failed to get versions for file {file_id}: {e}")
            return []

    async def get_next_version_number(self, file_id: str) -> int:
        """Get the next version number for a file (max + 1)."""
        try:
            async with read_scope() as session:
                current_max = await session.scalar(
                    select(func.max(FileVersions.version_number)).where(
                        FileVersions.file_id == int(file_id)
                    )
                )
            return (current_max + 1) if current_max is not None else 1
        except Exception as e:
            logger.error(f"Failed to get next version for file {file_id}: {e}")
            return 1

    async def create_version(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Create a new file version record."""
        try:
            values = _coerce_id_ints(
                _known_only(data, _VERSIONS_ATTRS, _VERSIONS_TEMPORAL),
                ("file_id",),
            )
            async with write_scope() as session:
                result = await session.execute(
                    insert(FileVersions).values(**values).returning(FileVersions)
                )
                row = result.scalars().first()
                out = _row(row, _VERSIONS_N2A) if row else {}
            logger.info(
                f"Created version {data.get('version_number')} for file "
                f"{data.get('file_id')}"
            )
            return out
        except Exception as e:
            logger.error(f"Failed to create version: {e}")
            raise

    # ------------------------------------------------------------------ #
    # Project file comments (project_file_comments, migration 335).
    # The 043-era review_comments surface died with migration 062; the
    # resources review system owns review_comments now. This surface has
    # its own table keyed by project_files.id / file_versions.id.
    # ------------------------------------------------------------------ #

    async def get_comments_for_file(
        self, file_id: str, version_id: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Comments for a file, timestamp_seconds ASC nulls-last, created ASC."""
        try:
            stmt = select(ProjectFileComments).where(
                ProjectFileComments.file_id == int(file_id)
            )
            if version_id:
                stmt = stmt.where(ProjectFileComments.version_id == int(version_id))
            stmt = stmt.order_by(
                ProjectFileComments.timestamp_seconds.asc().nulls_last(),
                ProjectFileComments.created_at.asc(),
            )
            async with read_scope() as session:
                result = await session.execute(stmt)
                return [_comment_row(r) for r in result.scalars().all()]
        except Exception as e:
            logger.error(f"Failed to get comments for file {file_id}: {e}")
            raise

    async def create_comment(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Create a new project-file comment."""
        try:
            values = _coerce_id_ints(
                _known_only(data, _COMMENTS_ATTRS), ("file_id", "version_id")
            )
            async with write_scope() as session:
                result = await session.execute(
                    insert(ProjectFileComments)
                    .values(**values)
                    .returning(ProjectFileComments)
                )
                row = result.scalars().first()
                out = _comment_row(row) if row else {}
            logger.info(f"Created comment on file {data.get('file_id')}")
            return out
        except Exception as e:
            logger.error(f"Failed to create comment: {e}")
            raise

    async def get_comment_by_id(self, comment_id: str) -> Optional[Dict[str, Any]]:
        """Get a single comment by id."""
        try:
            async with read_scope() as session:
                result = await session.execute(
                    select(ProjectFileComments)
                    .where(ProjectFileComments.id == int(comment_id))
                    .limit(1)
                )
                row = result.scalars().first()
                return _comment_row(row) if row else None
        except Exception as e:
            logger.error(f"Failed to get comment {comment_id}: {e}")
            raise

    async def delete_comment(self, comment_id: str) -> bool:
        """Delete a comment by id."""
        try:
            async with write_scope() as session:
                await session.execute(
                    delete(ProjectFileComments).where(
                        ProjectFileComments.id == int(comment_id)
                    )
                )
            logger.info(f"Deleted comment {comment_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to delete comment {comment_id}: {e}")
            raise

    # ------------------------------------------------------------------ #
    # Review status
    # ------------------------------------------------------------------ #

    async def update_review_status(
        self, file_id: str, status: Optional[str]
    ) -> Dict[str, Any]:
        """Update the review status of a project file."""
        try:
            async with write_scope() as session:
                result = await session.execute(
                    update(ProjectFiles)
                    .where(ProjectFiles.id == int(file_id))
                    .values(review_status=status)
                    .returning(ProjectFiles)
                )
                row = result.scalars().first()
                out = _row(row, _FILES_N2A) if row else {}
            logger.info(f"Updated review status for file {file_id} to {status}")
            return out
        except Exception as e:
            logger.error(f"Failed to update review status for file {file_id}: {e}")
            raise

    # ------------------------------------------------------------------ #
    # Folders
    # ------------------------------------------------------------------ #

    async def get_folders(
        self, project_id: str, parent_id: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Get folders in a project, optionally filtered by parent."""
        try:
            stmt = select(ProjectFolders).where(
                ProjectFolders.project_id == int(project_id)
            )
            if parent_id:
                stmt = stmt.where(ProjectFolders.parent_id == int(parent_id))
            else:
                stmt = stmt.where(ProjectFolders.parent_id.is_(None))
            stmt = stmt.order_by(ProjectFolders.name)
            async with read_scope() as session:
                result = await session.execute(stmt)
                return [_row(r, _FOLDERS_N2A) for r in result.scalars().all()]
        except Exception as e:
            logger.error(f"Failed to get folders for project {project_id}: {e}")
            return []

    async def get_folder(
        self, folder_id: str, project_id: str
    ) -> Optional[Dict[str, Any]]:
        """Get a single folder by ID, scoped to project."""
        try:
            async with read_scope() as session:
                result = await session.execute(
                    select(ProjectFolders)
                    .where(ProjectFolders.id == int(folder_id))
                    .where(ProjectFolders.project_id == int(project_id))
                    .limit(1)
                )
                row = result.scalars().first()
                return _row(row, _FOLDERS_N2A) if row else None
        except Exception as e:
            logger.error(f"Failed to get folder {folder_id}: {e}")
            return None

    async def create_folder(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Create a new folder."""
        try:
            values = _coerce_id_ints(
                _known_only(data, _FOLDERS_ATTRS, _FOLDERS_TEMPORAL),
                ("project_id", "parent_id"),
            )
            async with write_scope() as session:
                result = await session.execute(
                    insert(ProjectFolders).values(**values).returning(ProjectFolders)
                )
                row = result.scalars().first()
                return _row(row, _FOLDERS_N2A) if row else {}
        except Exception as e:
            logger.error(f"Failed to create folder: {e}")
            raise

    async def update_folder(
        self, folder_id: str, project_id: str, data: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """Update a folder, scoped to project."""
        try:
            values = _coerce_id_ints(
                _known_only(data, _FOLDERS_ATTRS, _FOLDERS_TEMPORAL),
                ("parent_id",),
            )
            if not values:
                return await self.get_folder(folder_id, project_id)
            async with write_scope() as session:
                result = await session.execute(
                    update(ProjectFolders)
                    .where(ProjectFolders.id == int(folder_id))
                    .where(ProjectFolders.project_id == int(project_id))
                    .values(**values)
                    .returning(ProjectFolders)
                )
                row = result.scalars().first()
                return _row(row, _FOLDERS_N2A) if row else None
        except Exception as e:
            logger.error(f"Failed to update folder {folder_id}: {e}")
            raise

    async def delete_folder_record(self, folder_id: str, project_id: str) -> bool:
        """Delete a folder record."""
        try:
            async with write_scope() as session:
                await session.execute(
                    delete(ProjectFolders)
                    .where(ProjectFolders.id == int(folder_id))
                    .where(ProjectFolders.project_id == int(project_id))
                )
            return True
        except Exception as e:
            logger.error(f"Failed to delete folder {folder_id}: {e}")
            raise

    async def reparent_folder_children(
        self, folder_id: str, new_parent_id: Optional[str]
    ) -> None:
        """Move files and sub-folders to a new parent when deleting a folder."""
        try:
            target = int(new_parent_id) if new_parent_id else None
            async with write_scope() as session:
                await session.execute(
                    update(ProjectFiles)
                    .where(ProjectFiles.folder_id == int(folder_id))
                    .values(folder_id=target)
                )
                await session.execute(
                    update(ProjectFolders)
                    .where(ProjectFolders.parent_id == int(folder_id))
                    .values(parent_id=target)
                )
        except Exception as e:
            logger.error(f"Failed to reparent children of folder {folder_id}: {e}")
            raise

    # ------------------------------------------------------------------ #
    # Shares
    # ------------------------------------------------------------------ #

    async def get_shares_by_project(self, project_id: str) -> List[Dict[str, Any]]:
        """Get all shares for files in a project."""
        try:
            async with read_scope() as session:
                file_ids = (
                    (
                        await session.execute(
                            select(ProjectFiles.id).where(
                                ProjectFiles.project_id == int(project_id)
                            )
                        )
                    )
                    .scalars()
                    .all()
                )
                if not file_ids:
                    return []
                result = await session.execute(
                    select(Shares)
                    .where(Shares.project_file_id.in_(file_ids))
                    .order_by(Shares.created_at.desc())
                )
                return [_row(r, _SHARES_N2A) for r in result.scalars().all()]
        except Exception as e:
            logger.error(f"Failed to get shares for project {project_id}: {e}")
            return []

    async def get_file_in_project(
        self, file_id: str, project_id: str
    ) -> Optional[Dict[str, Any]]:
        """Get a file verifying it belongs to the project (id + filename only)."""
        try:
            async with read_scope() as session:
                result = await session.execute(
                    select(ProjectFiles.id, ProjectFiles.filename)
                    .where(ProjectFiles.id == int(file_id))
                    .where(ProjectFiles.project_id == int(project_id))
                    .limit(1)
                )
                row = result.mappings().first()
                if not row:
                    return None
                # id is bigint → stays native int; filename is text. No uuid /
                # datetime here, so no _parity sweep needed.
                return dict(row)
        except Exception as e:
            logger.error(f"Failed to get file {file_id} in project {project_id}: {e}")
            return None

    async def create_share(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Create a new share record."""
        try:
            values = _known_only(data, _SHARES_ATTRS, _SHARES_TEMPORAL)
            async with write_scope() as session:
                result = await session.execute(
                    insert(Shares).values(**values).returning(Shares)
                )
                row = result.scalars().first()
                return _row(row, _SHARES_N2A) if row else {}
        except Exception as e:
            logger.error(f"Failed to create share: {e}")
            raise

    # ------------------------------------------------------------------ #
    # Members  (composite PK user_id + project_id; NO id column.)
    #
    # All four DB ops (get_members / create_member / update_member /
    # delete_member) run on the ORM path, keyed by the composite PK. The route
    # segment ``/{project_id}/members/{member_id}`` carries the member's
    # ``user_id`` (uuid str). update_member / delete_member previously filtered a
    # phantom ``id`` column and were a SILENT NO-OP in production — fixed here.
    # ------------------------------------------------------------------ #

    async def get_members(self, project_id: str) -> List[Dict[str, Any]]:
        """Get all members of a project, ordered by joined_at.

        NOTE: ordered by ``joined_at`` (the real column). It previously ordered
        by ``created_at`` — a phantom column (migration 070's
        ``ADD COLUMN IF NOT EXISTS created_at`` was a no-op over the 047 schema
        that only has ``joined_at``), so PostgREST 400'd, the ``except``
        swallowed it, and ``list_members`` silently returned ``[]`` in
        production. Fixed to order by joined_at so the real member list returns."""
        try:
            async with read_scope() as session:
                result = await session.execute(
                    select(ProjectMembers)
                    .where(ProjectMembers.project_id == int(project_id))
                    .order_by(ProjectMembers.joined_at)
                )
                return [_row(r, _MEMBERS_N2A) for r in result.scalars().all()]
        except Exception as e:
            logger.error(f"Failed to get members for project {project_id}: {e}")
            return []

    async def create_member(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Create a new member record."""
        try:
            values = _known_only(data, _MEMBERS_ATTRS)
            async with write_scope() as session:
                result = await session.execute(
                    insert(ProjectMembers).values(**values).returning(ProjectMembers)
                )
                row = result.scalars().first()
                return _row(row, _MEMBERS_N2A) if row else {}
        except Exception as e:
            logger.error(f"Failed to create member: {e}")
            raise

    async def update_member(
        self, member_id: str, project_id: str, data: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """Update a member's role, scoped to project.

        Identified by the composite PK ``(project_id, user_id)`` — ``member_id``
        is the member's ``user_id`` (uuid str), which is what the router path
        ``/{project_id}/members/{member_id}`` and the frontend send. Returns the
        updated row dict (strategy-C parity) or ``None`` when no such member
        exists (the service turns that into a 404). The former impl filtered a
        phantom ``id`` column (project_members has no ``id``) and was a SILENT
        NO-OP in production."""
        try:
            values = _known_only(data, _MEMBERS_ATTRS)
            if not values:
                async with read_scope() as session:
                    row = (
                        (
                            await session.execute(
                                select(ProjectMembers)
                                .where(ProjectMembers.user_id == member_id)
                                .where(ProjectMembers.project_id == int(project_id))
                                .limit(1)
                            )
                        )
                        .scalars()
                        .first()
                    )
                    return _row(row, _MEMBERS_N2A) if row else None
            async with write_scope() as session:
                result = await session.execute(
                    update(ProjectMembers)
                    .where(ProjectMembers.user_id == member_id)
                    .where(ProjectMembers.project_id == int(project_id))
                    .values(**values)
                    .returning(ProjectMembers)
                )
                row = result.scalars().first()
                return _row(row, _MEMBERS_N2A) if row else None
        except Exception as e:
            logger.error(f"Failed to update member {member_id}: {e}")
            raise

    async def delete_member(self, member_id: str, project_id: str) -> bool:
        """Delete a member, scoped to project.

        Identified by the composite PK ``(project_id, user_id)`` — ``member_id``
        is the member's ``user_id``. The former impl filtered a phantom ``id``
        column and was a SILENT NO-OP in production."""
        try:
            async with write_scope() as session:
                await session.execute(
                    delete(ProjectMembers)
                    .where(ProjectMembers.user_id == member_id)
                    .where(ProjectMembers.project_id == int(project_id))
                )
            return True
        except Exception as e:
            logger.error(f"Failed to delete member {member_id}: {e}")
            raise

    # ------------------------------------------------------------------ #
    # Members — auth-admin enrichment (CONSCIOUS-KEEP, Auth-API precedent).
    # These call client.auth.admin (Supabase Auth / GoTrue), not a DB table, so
    # they are permanently out of ORM scope per the migration plan. The ORM
    # get_members above str()s user_id (uuid → str), which enrich_members_with_
    # email needs for its user_map.get(m["user_id"]) lookup against str(u.id).
    # ------------------------------------------------------------------ #

    async def enrich_members_with_email(
        self, members: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """Enrich member records with user email from auth."""
        if not members:
            return members
        try:
            client = await self._get_client()
            user_ids = [m["user_id"] for m in members]
            users_result = await client.auth.admin.list_users()
            user_map = {
                str(u.id): u.email for u in users_result if str(u.id) in user_ids
            }
            for m in members:
                m["email"] = user_map.get(m["user_id"], "")
            return members
        except Exception as e:
            logger.warning(f"Failed to enrich members with email: {e}")
            return members

    async def get_user_email(self, user_id: str) -> str:
        """Get a user's email from auth."""
        try:
            client = await self._get_client()
            user = await client.auth.admin.get_user_by_id(user_id)
            return user.user.email if user.user else ""
        except Exception:
            return ""

    # ------------------------------------------------------------------ #
    # Collections
    # ------------------------------------------------------------------ #

    async def get_collections(self, project_id: str) -> List[Dict[str, Any]]:
        """Get all collections for a project."""
        try:
            async with read_scope() as session:
                result = await session.execute(
                    select(ProjectCollections)
                    .where(ProjectCollections.project_id == int(project_id))
                    .order_by(ProjectCollections.created_at.desc())
                )
                return [_row(r, _COLLECTIONS_N2A) for r in result.scalars().all()]
        except Exception as e:
            logger.error(f"Failed to get collections for project {project_id}: {e}")
            return []

    async def create_collection(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Create a new collection."""
        try:
            values = _known_only(data, _COLLECTIONS_ATTRS, _COLLECTIONS_TEMPORAL)
            async with write_scope() as session:
                result = await session.execute(
                    insert(ProjectCollections)
                    .values(**values)
                    .returning(ProjectCollections)
                )
                row = result.scalars().first()
                return _row(row, _COLLECTIONS_N2A) if row else {}
        except Exception as e:
            logger.error(f"Failed to create collection: {e}")
            raise

    async def delete_collection(self, collection_id: str, project_id: str) -> bool:
        """Delete a collection, scoped to project."""
        try:
            async with write_scope() as session:
                await session.execute(
                    delete(ProjectCollections)
                    .where(ProjectCollections.id == int(collection_id))
                    .where(ProjectCollections.project_id == int(project_id))
                )
            return True
        except Exception as e:
            logger.error(f"Failed to delete collection {collection_id}: {e}")
            raise


def get_projects_repository() -> "ProjectsRepository":
    """Return the ProjectsRepository (ORM-only after the post-rollout collapse).

    The ``USE_ORM_PROJECTS`` flag and the separate ``ProjectsRepositoryOrm``
    subclass have been retired; the ORM bodies live directly on the class."""
    return ProjectsRepository()
