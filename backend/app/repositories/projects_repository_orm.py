# app/repositories/projects_repository_orm.py

"""SQLAlchemy 2.0 ORM implementation of ProjectsRepository (Phase 2, L-solo).

REST → ORM successor for the MediaTrack project system: the ``ProjectsService``
data surface over 10 tables (projects / project_files / project_folders /
project_members / project_tasks / file_versions / review_comments /
parsed_media / shares / project_collections). Same Strangler-Fig single-
inheritance pattern as the 8 already-migrated repos: ``ProjectsRepositoryOrm``
subclasses ``ProjectsRepository`` and overrides every DB method; the non-DB
helpers (``_classify`` lives in the service, the ``TABLE_*`` constants are
inherited) stay put. The two AUTH-admin methods (``enrich_members_with_email`` /
``get_user_email``) are NOT overridden — they call ``client.auth.admin`` (not a
DB table) and inherit the legacy supabase path via MRO. Call sites route through
``get_projects_repository()``.

PHANTOM-COLUMN PRE-FLIGHT (template-v2 mandatory first step)
===========================================================
Every write path was diffed against its ORM model's mapped attributes, tracing
the actual keys ``projects_service.py`` (+ projects_router) passes:

  create_project   : owner_id / name / team_id / display_code? ... → Projects.
                     NOTE: the service NEVER sets display_code unless team_id;
                     and ``display_code`` is NOT on the Projects model. See the
                     DISPLAY_CODE phantom note below.
  update_project   : arbitrary dict (display_code / name / ...) → see note.
  create_file      : project_id / filename / file_type / mime_type / file_path /
                     file_size_bytes / uploaded_by / notes / media_id /
                     duration_seconds / resolution / cover_image_path +
                     ffprobe metadata (video_codec / audio_codec / fps /
                     video_bitrate_kbps / audio_bitrate_kbps / audio_channels /
                     audio_sample_rate) — ALL present on ProjectFiles. OK.
  update_file      : current_version / file_path / file_size_bytes / mime_type /
                     filename / is_trashed / trashed_at / folder_id /
                     review_status + ffprobe metadata — all on ProjectFiles. OK.
  create_version   : file_id / version_number / filename / file_path /
                     file_size_bytes / mime_type / uploaded_by / notes +
                     ffprobe metadata — all on FileVersions. OK.
  create_folder    : project_id / name / created_by / parent_id — all on
                     ProjectFolders. OK.
  update_folder    : name (rename) — on ProjectFolders. OK.
  reparent_*       : project_files.folder_id / project_folders.parent_id — OK.
  create_share     : project_file_id / share_type / shared_by / share_name /
                     share_code / password / allow_download / status /
                     expires_at — all on Shares. OK.
  create_task      : project_id / title / created_by / description / task_type /
                     assignee_id / due_date / status — all on ProjectTasks. OK.
  update_task      : arbitrary dict (title/status/assignee_id/due_date/...) —
                     all on ProjectTasks. OK.
  create_member    : project_id / user_id / role / invited_by — all on
                     ProjectMembers (composite PK user_id+project_id). OK.
  update_member    : role — on ProjectMembers. NOTE: legacy filters by
                     ``id`` + ``project_id``; ProjectMembers has NO ``id`` (PK
                     is user_id+project_id). See MEMBER_ID phantom note below.
  create_collection: project_id / collection_code / collection_name /
                     max_file_size_mb / created_by / allowed_types / deadline —
                     all on ProjectCollections. OK.
  create_comment   : file_id / author_id / content / timestamp_seconds /
                     version_id / drawing_data — *** HARD PHANTOM-COLUMN HIT ***
                     migration 062 DROPPED the original (043) review_comments
                     table and recreated it with resource_id / timecode and NO
                     file_id / timestamp_seconds / drawing_data. The legacy REST
                     comment methods (get_comments_for_file / create_comment /
                     get_comment_by_id / delete_comment) target the OLD schema —
                     they are ALREADY BROKEN against the live DB (PostgREST
                     PGRST on the missing file_id column). See COMMENT note.

Resolved phantom columns
------------------------
PARITY DISCIPLINE: an inert mechanical migration must reproduce the legacy
behavior EXACTLY on flip — it must NOT repair a broken/no-op endpoint or change
behavior. Phantom columns are handled accordingly:

1. DISPLAY_CODE (projects): the Projects ORM model has no ``display_code``
   column. The service's create_project flow does
   ``update_project(id, {"display_code": ...})`` ONLY for team projects, wrapped
   in a try/except that logs a warning and continues — so under REST this was a
   PGRST 'column does not exist' that got swallowed (graceful no-op) on the live
   schema today. To preserve that EXACT observable behavior we filter unknown
   keys out of the ``values()`` for projects writes and log a debug line (the
   ORM would otherwise raise a hard ``unexpected keyword`` and break the
   swallowed-warning contract). Documented graceful-no-op.
2. MEMBER_ID (project_members): ProjectMembers' PK is (user_id, project_id) —
   there is NO ``id`` column. The legacy ``update_member`` / ``delete_member``
   filter by ``id == member_id`` + ``project_id``. Under REST that ``id`` filter
   is a PGRST 'column id does not exist' → swallowed/no-match, so those endpoints
   are a SILENT NO-OP today. We do NOT override update_member / delete_member —
   they INHERIT the legacy REST parent so flag-ON keeps the no-op identically.
   DEFERRED PRODUCT DECISION (applies ONLY to update_member / delete_member, not
   a mechanical concern): repairing those two to filter by user_id would be a
   behavior change, forbidden on a parity migration — it needs a separate
   product decision.
   get_members / create_member ARE overridden (genuine, non-drifted DB ops, NOT
   deferred). CO-FIXED PROD BUG: the legacy ``get_members`` ordered by
   ``created_at`` — a phantom column (migration 070's ``ADD COLUMN IF NOT
   EXISTS created_at`` was a no-op over the 047 schema that only has
   ``joined_at``), so PostgREST 400'd, the ``except`` swallowed it, and
   ``list_members`` silently returned ``[]`` in production. The legacy method is
   fixed in this PR to order by ``joined_at`` (the real column), matching this
   ORM override — so both paths now return the real member list in joined_at
   order (strict parity restored AND the prod bug fixed).
3. COMMENT methods: migration 062 DROPPED the original (043) review_comments
   table and recreated it with resource_id / timecode (no file_id /
   timestamp_seconds / drawing_data), so the legacy comment methods
   (get_comments_for_file / create_comment / get_comment_by_id / delete_comment)
   target the dropped 043 columns and are ALREADY 500-ing in production. We do
   NOT override them — they INHERIT the legacy REST parent so flag-ON 500s
   identically to today (true parity). Beyond parity, a naive ORM remap would be
   WRONG: the comment surface is reached via the project_files path
   (_verify_file_in_project passes a project_files.id as file_id), so file_id is
   NOT a resources.id — writing it as review_comments.resource_id (FK →
   resources.id) would FK-fail or mis-associate. DEFERRED PRODUCT DECISION: the
   comment surface is half-migrated to the resources review system
   (reviews_router / ReviewService) and needs an ownership decision, not a
   mechanical port here.

STRATEGY C — VALUE-TYPE PARITY (per-field, exact REST shape)
============================================================
Supabase REST rendered JSON: bigint → int, uuid → str, timestamptz → ISO str,
date → 'YYYY-MM-DD' str, numeric → str, jsonb → dict. The ORM returns native
types. We coerce ONLY where it matters, to the exact REST shape:

  bigint ids (projects.id / project_files.id / project_folders.id /
    project_tasks.id / file_versions.id / shares.id / project_collections.id /
    project_members.project_id / parsed_media.id, plus every bigint FK such as
    project_id / media_id / folder_id / parent_id / file_id / project_file_id /
    file_size_bytes / storage_size / datasize_bytes) → STAY NATIVE int (the 5.3
    trap — these flow into scope / membership / FK / dict-key comparisons; the
    service does ``int(media["duration"])`` math and bare-int FK passthrough;
    coercing a snowflake bigint to str would silently break scoping/joins).

  timestamptz (created_at / updated_at / trashed_at / joined_at / expires_at /
    deadline / published_at / download_time / ai_generated_at / last_viewed_at
    / …) → ``.isoformat()`` ALWAYS (sweep every datetime in the output dict).
    REST returned ISO strings; the service ALREADY produces ISO strings for the
    columns it sets (``trashed_at`` / ``expires_at`` via ``.isoformat()``), so
    parity keeps writes and reads format-consistent. CONSUMED: response models /
    the frontend parse ISO; ``==`` / ordering on a native datetime vs an ISO str
    would diverge.

  date (project_tasks.due_date) → ``.isoformat()`` → 'YYYY-MM-DD' (REST shape).
    The datetime sweep runs FIRST and ``datetime`` is a subclass of ``date``, so
    we check ``datetime`` before ``date`` (a bare ``date`` only matches the date
    branch). The service may pass ``due_date`` as an ISO 'YYYY-MM-DD' string on
    write (router input) — SQLAlchemy's Date type accepts both a ``date`` and an
    ISO string, and the read coerces the native ``date`` back to the same str.

  uuid columns — coerced to str per-consumer (REST returned str). Audit of every
  consumer that touches a uuid in the OUTPUT of a repo method (through the repo,
  i.e. affected by USE_ORM_PROJECTS):
    - projects.owner_id : CONSUMED type-sensitively. projects_service does
      ``project["owner_id"] != user_id`` (user_id is a str from auth) in
      update_project / delete_project / update_review_status. Native uuid.UUID
      != str is ALWAYS True → would forbid every owner. MUST be str. ✓
    - project_files.uploaded_by / media_id-derived / folder_id : uploaded_by is
      uuid → str for shape parity (no type-sensitive consumer, but REST shape).
    - project_members.user_id : CONSUMED. enrich_members_with_email builds
      ``{str(u.id): email}`` and looks up ``user_map.get(m["user_id"])`` — the
      key is a str, so m["user_id"] MUST be a str to match. MUST be str. ✓
    - project_members.invited_by : uuid → str (shape parity).
    - review_comments.author_id : CONSUMED. delete_comment does
      ``comment.get("author_id") != user_id`` (user_id str). MUST be str. ✓
    - project_folders.created_by / project_tasks.created_by / assignee_id /
      workflow_node_id / shares.shared_by / project_collections.created_by /
      projects.workflow_id : uuid → str (shape parity; passed through to HTTP /
      not compared type-sensitively, but matches REST's str).
    - parsed_media.* uuid: none (parsed_media PK is bigint; no uuid columns).
  Decision: a generic uuid-sweep on the output dict (any ``uuid.UUID`` value →
  str) reproduces REST shape for EVERY uuid column at once — cheaper and more
  future-proof than enumerating, and every consumer audit above wants str. We
  also enum-unwrap (``_plain`` via ``_orm_obj_to_dict``) for parsed_media's
  Enum(DownloadStatus) columns and resolve the renamed ``metadata_`` →
  ``metadata`` column via ``_name_to_attr``.

  numeric (project_files.fps / file_versions.fps : Numeric; review_comments
    .timecode / parsed_media.download_duration : Double) → LEFT NATIVE. No
    consumer is type-sensitive: fps is passed straight to HTTP; the service
    computes fps itself as a float; timecode is read by the frontend. REST gave
    a str for Numeric and a float for Double — but per the iron rule we do not
    coerce fields no type-sensitive consumer touches. (A Numeric fps reads back
    as Decimal; FastAPI's jsonable_encoder serializes Decimal fine.)

Date/timestamp FILTER binding (v3): the ONLY date/timestamp range filters in
the 43 methods are NONE — every projects query filters by equality (id /
project_id / file_id / folder_id / parent_id) or IN, plus ``is_trashed`` bool
and ordering by created_at/updated_at/version_number/sort_order/name. No
``WHERE created_at </>`` range filter exists, so there is no timestamptz<VARCHAR
hazard here (unlike LogsRepository). Confirmed by scanning all 43 methods.

Writes commit via ``write_scope()`` (the silent-rollback P0 lesson). Reads use
``read_scope()``. Error handling mirrors the legacy exactly: reads swallow +
return None/[]/0 on failure; writes (create_*/update_*/delete_*) log + re-raise.
"""

from __future__ import annotations

import datetime as _dt
import uuid as _uuid
from typing import Any, Dict, List, Optional

from loguru import logger
from sqlalchemy import delete, func, insert, select, update

from app.db.session import read_scope, write_scope
from app.models import (
    FileVersions,
    ParsedMedia,
    ProjectCollections,
    ProjectFiles,
    ProjectFolders,
    ProjectMembers,
    Projects,
    ProjectTasks,
    Shares,
)
from app.repositories._orm_helpers import _name_to_attr, _orm_obj_to_dict
from app.repositories.projects_repository import ProjectsRepository

# Precomputed DB-column-name → mapped-attribute-name maps (built once). Used so
# reads produce SELECT *-shaped dicts keyed by DB column name, resolving any
# renamed column (e.g. parsed_media.metadata_ → "metadata") via the mapper.
_PROJECTS_N2A: Dict[str, str] = _name_to_attr(Projects)
_FILES_N2A: Dict[str, str] = _name_to_attr(ProjectFiles)
_FOLDERS_N2A: Dict[str, str] = _name_to_attr(ProjectFolders)
_MEMBERS_N2A: Dict[str, str] = _name_to_attr(ProjectMembers)
_TASKS_N2A: Dict[str, str] = _name_to_attr(ProjectTasks)
_VERSIONS_N2A: Dict[str, str] = _name_to_attr(FileVersions)
_MEDIA_N2A: Dict[str, str] = _name_to_attr(ParsedMedia)
_SHARES_N2A: Dict[str, str] = _name_to_attr(Shares)
_COLLECTIONS_N2A: Dict[str, str] = _name_to_attr(ProjectCollections)

# Mapped attribute names per model — for filtering unknown keys out of write
# values() (the DISPLAY_CODE / arbitrary-dict graceful-no-op contract). Bigint
# ids / FK columns are NOT in any of these sweep lists, so they stay native int.
_PROJECTS_ATTRS = {p.key for p in Projects.__mapper__.column_attrs}
_FILES_ATTRS = {p.key for p in ProjectFiles.__mapper__.column_attrs}
_FOLDERS_ATTRS = {p.key for p in ProjectFolders.__mapper__.column_attrs}
_TASKS_ATTRS = {p.key for p in ProjectTasks.__mapper__.column_attrs}
_MEMBERS_ATTRS = {p.key for p in ProjectMembers.__mapper__.column_attrs}
_VERSIONS_ATTRS = {p.key for p in FileVersions.__mapper__.column_attrs}
_SHARES_ATTRS = {p.key for p in Shares.__mapper__.column_attrs}
_COLLECTIONS_ATTRS = {p.key for p in ProjectCollections.__mapper__.column_attrs}


def _temporal_kinds(model: Any) -> Dict[str, str]:
    """Map mapped-attribute-name → 'date' | 'datetime' for the model's temporal
    columns. Built from the mapper so it tracks the schema.

    WHY (v3 binding hazard, repro'd in the L-solo integration run): asyncpg
    (under SQLAlchemy's Date / DateTime types) requires NATIVE
    ``datetime.date`` / ``datetime`` bind values and does NOT auto-coerce an ISO
    string — ``INSERT ... due_date=$ ('2026-06-09')`` raises
    ``DataError: 'str' object has no attribute 'toordinal'``. PostgREST DID
    auto-coerce ISO strings, and the request schemas type ``due_date`` /
    ``deadline`` as ``Optional[str]`` (ISO), so those strings flow straight into
    write ``values()``. We coerce them back to native types at the write
    boundary (see ``_coerce_temporal``)."""
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


_TASKS_TEMPORAL = _temporal_kinds(ProjectTasks)
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


def _parity(out: Dict[str, Any]) -> Dict[str, Any]:
    """Apply strategy-C value-type parity IN PLACE on a SELECT *-shaped dict:

    - any ``uuid.UUID`` value → str (REST returned strings; the audited
      consumers do ``== user_id`` / dict-key lookups that break on native UUID).
    - any ``datetime`` → ``.isoformat()`` (REST ISO; ``==``/ordering/``str()``
      footgun). Checked BEFORE ``date`` because ``datetime`` ⊂ ``date``.
    - any ``date`` (e.g. project_tasks.due_date) → ``.isoformat()`` →
      'YYYY-MM-DD' (REST shape).
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
            "ProjectsRepositoryOrm: dropping non-column keys %s (phantom-column "
            "graceful no-op, REST parity)",
            dropped,
        )
    if temporal:
        known = _coerce_temporal(known, temporal)
    return known


class ProjectsRepositoryOrm(ProjectsRepository):
    """ORM-backed ProjectsRepository.

    Overrides every DB-touching method on the 10 project tables. The two
    auth-admin methods (enrich_members_with_email / get_user_email) inherit the
    legacy supabase path via MRO (they hit auth, not a table). See
    ``projects_repository.py`` for the per-method REST contracts."""

    # ------------------------------------------------------------------ #
    # Projects CRUD
    # ------------------------------------------------------------------ #

    async def get_user_projects(
        self, user_id: str, team_id: str | None = None
    ) -> List[Dict[str, Any]]:
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
            async with read_scope() as session:
                result = await session.execute(stmt)
                return [_row(r, _PROJECTS_N2A) for r in result.scalars().all()]
        except Exception as e:
            logger.error(f"Failed to get projects for user {user_id}: {e}")
            return []

    async def get_project_by_id(self, project_id: str) -> Optional[Dict[str, Any]]:
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
        try:
            values = _known_only(data, _PROJECTS_ATTRS, _PROJECTS_TEMPORAL)
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

    # ------------------------------------------------------------------ #
    # Files CRUD
    # ------------------------------------------------------------------ #

    async def get_project_files(
        self, project_id: str, include_trashed: bool = False
    ) -> List[Dict[str, Any]]:
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
        try:
            values = _known_only(data, _FILES_ATTRS, _FILES_TEMPORAL)
            async with write_scope() as session:
                result = await session.execute(
                    insert(ProjectFiles).values(**values).returning(ProjectFiles)
                )
                row = result.scalars().first()
                out = _row(row, _FILES_N2A) if row else {}
            logger.info(
                "Created file '%s' in project %s",
                data.get("filename"),
                data.get("project_id"),
            )
            return out
        except Exception as e:
            logger.error(f"Failed to create file: {e}")
            raise

    async def update_file(self, file_id: str, data: Dict[str, Any]) -> Dict[str, Any]:
        try:
            values = _known_only(data, _FILES_ATTRS, _FILES_TEMPORAL)
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

    # ------------------------------------------------------------------ #
    # File versions
    # ------------------------------------------------------------------ #

    async def get_file_versions(self, file_id: str) -> List[Dict[str, Any]]:
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
        try:
            values = _known_only(data, _VERSIONS_ATTRS, _VERSIONS_TEMPORAL)
            async with write_scope() as session:
                result = await session.execute(
                    insert(FileVersions).values(**values).returning(FileVersions)
                )
                row = result.scalars().first()
                out = _row(row, _VERSIONS_N2A) if row else {}
            logger.info(
                "Created version %s for file %s",
                data.get("version_number"),
                data.get("file_id"),
            )
            return out
        except Exception as e:
            logger.error(f"Failed to create version: {e}")
            raise

    # ------------------------------------------------------------------ #
    # Review comments — NOT OVERRIDDEN (inherited from the legacy REST parent).
    #
    # DEFERRED PRODUCT DECISION (not a mechanical migration concern): migration
    # 062 DROPPED the original (043) review_comments table and recreated it with
    # a different schema (resource_id / timecode; no file_id / timestamp_seconds
    # / drawing_data). The legacy comment methods (get_comments_for_file /
    # create_comment / get_comment_by_id / delete_comment) target the dropped 043
    # columns, so they are ALREADY 500-ing in production. We deliberately do NOT
    # override them: an inert mechanical migration must reproduce the legacy
    # behavior EXACTLY on flip — flag-ON routes comments through the inherited
    # REST parent and 500s identically to today (true parity, no repair).
    #
    # Beyond parity, a naive ORM remap would be WRONG: the comment surface is
    # reached via the project_files path (ProjectsService._verify_file_in_project
    # passes a project_files.id as file_id), so file_id is NOT a resources.id —
    # writing it as review_comments.resource_id (FK → resources.id) would FK-fail
    # or mis-associate. The comment surface is half-migrated to the resources
    # review system (see reviews_router / ReviewService) and needs a product
    # decision about ownership, not a mechanical port here.
    # ------------------------------------------------------------------ #

    # ------------------------------------------------------------------ #
    # Review status
    # ------------------------------------------------------------------ #

    async def update_review_status(
        self, file_id: str, status: Optional[str]
    ) -> Dict[str, Any]:
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
        try:
            values = _known_only(data, _FOLDERS_ATTRS, _FOLDERS_TEMPORAL)
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
        try:
            values = _known_only(data, _FOLDERS_ATTRS, _FOLDERS_TEMPORAL)
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
    # Tasks
    # ------------------------------------------------------------------ #

    async def get_tasks(self, project_id: str) -> List[Dict[str, Any]]:
        try:
            async with read_scope() as session:
                result = await session.execute(
                    select(ProjectTasks)
                    .where(ProjectTasks.project_id == int(project_id))
                    .order_by(ProjectTasks.sort_order)
                )
                return [_row(r, _TASKS_N2A) for r in result.scalars().all()]
        except Exception as e:
            logger.error(f"Failed to get tasks for project {project_id}: {e}")
            return []

    async def create_task(self, data: Dict[str, Any]) -> Dict[str, Any]:
        try:
            values = _known_only(data, _TASKS_ATTRS, _TASKS_TEMPORAL)
            async with write_scope() as session:
                result = await session.execute(
                    insert(ProjectTasks).values(**values).returning(ProjectTasks)
                )
                row = result.scalars().first()
                return _row(row, _TASKS_N2A) if row else {}
        except Exception as e:
            logger.error(f"Failed to create task: {e}")
            raise

    async def update_task(
        self, task_id: str, project_id: str, data: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        try:
            values = _known_only(data, _TASKS_ATTRS, _TASKS_TEMPORAL)
            if not values:
                async with read_scope() as session:
                    row = (
                        (
                            await session.execute(
                                select(ProjectTasks)
                                .where(ProjectTasks.id == int(task_id))
                                .where(ProjectTasks.project_id == int(project_id))
                                .limit(1)
                            )
                        )
                        .scalars()
                        .first()
                    )
                    return _row(row, _TASKS_N2A) if row else None
            async with write_scope() as session:
                result = await session.execute(
                    update(ProjectTasks)
                    .where(ProjectTasks.id == int(task_id))
                    .where(ProjectTasks.project_id == int(project_id))
                    .values(**values)
                    .returning(ProjectTasks)
                )
                row = result.scalars().first()
                return _row(row, _TASKS_N2A) if row else None
        except Exception as e:
            logger.error(f"Failed to update task {task_id}: {e}")
            raise

    async def delete_task(self, task_id: str, project_id: str) -> bool:
        try:
            async with write_scope() as session:
                await session.execute(
                    delete(ProjectTasks)
                    .where(ProjectTasks.id == int(task_id))
                    .where(ProjectTasks.project_id == int(project_id))
                )
            return True
        except Exception as e:
            logger.error(f"Failed to delete task {task_id}: {e}")
            raise

    # ------------------------------------------------------------------ #
    # Members  (composite PK user_id + project_id; NO id column.)
    #
    # get_members / create_member are genuine, non-drifted DB ops → overridden.
    #
    # update_member / delete_member are NOT overridden — inherited from the
    # legacy REST parent. DEFERRED PRODUCT DECISION (not a mechanical concern):
    # project_members has a composite PK (user_id + project_id) and NO ``id``
    # column, but the legacy update_member / delete_member filter by
    # ``id == member_id`` — a phantom column that never matched under REST, so
    # those endpoints are a SILENT NO-OP today. An inert mechanical migration
    # must reproduce that no-op EXACTLY on flip, so we inherit the legacy methods
    # rather than "fixing" the surface to use user_id (that would be a behavior
    # change, forbidden on a parity migration). The phantom-id member surface
    # needs a product decision (member_id → user_id) handled separately.
    # ------------------------------------------------------------------ #

    async def get_members(self, project_id: str) -> List[Dict[str, Any]]:
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

    # enrich_members_with_email / get_user_email are NOT overridden — they call
    # client.auth.admin (Supabase Auth, not a DB table) and inherit the legacy
    # path via MRO. The ORM get_members above str()s user_id (uuid → str), which
    # the legacy enrich_members_with_email needs for its ``user_map.get(
    # m["user_id"])`` lookup against str(u.id) keys.

    # ------------------------------------------------------------------ #
    # Collections
    # ------------------------------------------------------------------ #

    async def get_collections(self, project_id: str) -> List[Dict[str, Any]]:
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


__all__ = ["ProjectsRepositoryOrm"]
