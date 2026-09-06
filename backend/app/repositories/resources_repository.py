# app/repositories/resources_repository.py

"""SQLAlchemy 2.0 ORM implementation of ResourcesRepository (Task 5.2).

Post-rollout: prod runs 100% ORM, so the per-domain ``USE_ORM_RESOURCES``
flag and the standalone ``resources_repository_orm.py`` twin have been
retired — ``ResourcesRepository`` is now the single ORM-backed class and
``get_resources_repository()`` returns it unconditionally. Call sites are
zero-touch (same class name, same public signatures).

Data access for the resource library: resources, resource_items,
resource_versions, and folders. The 40 data-access methods on those four
tables run on the ORM session scopes from ``app.db.session``.

REST STRAGGLERS — NOW PORTED TO THE ORM (scope-enforce prerequisite)
====================================================================
Six method groups used to run the legacy supabase-py REST bodies in prod via
Python MRO (the old ``ResourcesRepositoryOrm(AsyncpgRepository,
ResourcesRepository)`` inherited them from the base). Those REST bodies were
tenant-scope BYPASSES: the app-layer choke point only governs the ORM session
layer, so ``self._get_client()`` (async supabase admin) reads/writes skipped
enforcement entirely. To let ``SCOPE_ENFORCE_RESOURCES`` flip on later they are
now rewritten on the ORM session scopes (reads via ``read_scope()`` +
``select(...)``, writes via ``write_scope()``), Strategy-C value parity with
the retired REST bodies preserved by the ``_rest_parity`` / ``_plain`` funnels:
  - ``find_by_hashes`` (batch hash lookup)
  - the resource_tags trio: ``add_resource_tag`` / ``remove_resource_tag`` /
    ``get_resource_tags``
  - the smart-folder group: ``get_smart_folders`` / ``create_smart_folder`` /
    ``execute_smart_rules`` (+ helpers ``_condition_to_sql_expr`` /
    ``_coerce_smart_value`` / ``_resolve_value`` / ``_filter_by_tags``)
  - the temp-sweeper helpers: ``list_resources_in_folder`` /
    ``soft_delete_resource``
With the port, ``self._get_client()`` has zero callers and is removed; the
async supabase admin import goes with it. ``list_accessible_for_user`` was
pure SQL via ``db_engine.fetch_all`` (never supabase-py) until the Phase A
raw-SQL-to-ORM migration (docs/decisions/2026-08-04-raw-sql-to-orm-full-
migration.md) ported it onto ``read_scope()`` + ``select(...)`` too.

⚠️ ``SCOPE_ENFORCE_RESOURCES`` is a SEPARATE flag/rollout concern (the
app-layer tenant-scope choke point) and is INDEPENDENT of the retired
``USE_ORM_RESOURCES``. Nothing here touches it — the ``system_request_scope``
/ ``scoped_sql`` / ``is_enforced`` machinery below is enforcement plumbing,
untouched by the ORM collapse.

THE P0 FIX
==========
The old asyncpg path ran every write via ``self.fetch_one("…RETURNING")`` on
a NON-committing ``engine.connect()``. On connection close the
INSERT/UPDATE/DELETE **silently rolled back** — the returned RETURNING row
looked written but the next read saw the OLD value (silent data loss). This
implementation routes every write through ``write_scope()`` (which does
``session.begin()`` and COMMITS), and runs each multi-statement cascade
inside ONE ``write_scope()`` so the cascade is atomic (all-or-nothing) AND
actually persists.

Fidelity contract (the swap must be invisible to all call sites):
  - dict at the boundary — never leak ORM objects. Same exact dict shapes
    as the legacy REST impl (column projections, nested ``resource`` from
    ``row_to_json``, ``parsed_media`` overlay, cascade count dicts).
  - ``_bigint()`` / ``_bigint_list()`` coercion on str-snowflake ids before
    binding to BIGINT columns (asyncpg int8 codec is strict).
  - ``_VERSION_BIGINT_COLS`` coercion preserved in ``create_version``.
  - datetimes bound as tz-aware ``datetime`` objects, never isoformat.
  - enum read-parity: ``resources`` has 3 ``Enum(AiTaskStatus)`` columns
    (transcript_status / summary_status / visual_analysis_status). The ORM
    returns enum MEMBERS; the legacy REST returned bare ``str``. Every read
    of a resources row is funnelled through ``_resources_row_to_dict`` which
    unwraps enums to ``.value`` (see ``_plain``).
  - VALUE-TYPE read-parity (strategy-C — AUTHZ-CRITICAL): the ORM read
    helpers (``_orm_obj_to_dict`` / RETURNING ``.mappings()``) return NATIVE
    Python types — ``uuid.UUID`` for uuid columns (creator_id / created_by /
    added_by / uploaded_by) and ``datetime`` for timestamptz columns
    (created_at / updated_at / trashed_at / transcode_at). The legacy REST
    (PostgREST) repo returned these as ``str``. Consumers compare WITHOUT
    coercion — e.g. ``resource.get("creator_id") != auth.user_id`` (ai_router)
    and ``resource["creator_id"] != user_id`` (resources_service), where the
    user id is a ``str`` — so a native ``UUID`` makes ``!=`` ALWAYS true and
    the owner is wrongly DENIED (silent authz break). Every dict the repo
    returns is funnelled through ``_rest_parity`` (uuid → str, datetime → ISO
    str), recursing into nested ``resource`` / ``parsed_media`` sub-dicts, so
    the ORM dict is a true drop-in for the REST baseline. BIGINT ids stay int
    (int on both sides).
"""

from __future__ import annotations

import json
import uuid
from contextlib import nullcontext
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Mapping, Optional

from loguru import logger
from sqlalchemy import delete as sa_delete
from sqlalchemy import distinct, func, insert, or_, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.db.pg_coerce import coerce_datetime_strings
from app.db.repository_base import AsyncpgRepository
from app.db.scope import UnscopedQueryError, is_enforced, system_request_scope
from app.db.session import read_scope, write_scope
from app.models import (
    Folders,
    GalleryItems,
    ParsedMedia,
    ResourceItems,
    Resources,
    ResourceTags,
    ResourceVersions,
    Tags,
    TeamMembers,
    Teams,
)
from app.repositories._orm_helpers import _name_to_attr, _orm_obj_to_dict, _plain

# Working-set caps for the batch sweepers in this repo. They replace
# unbounded SELECTs that PostgREST silently truncated at 1000. Each consuming
# sweeper re-runs, so a backlog larger than one batch drains over successive
# runs instead of being clipped.
EXPIRED_TRASH_BATCH = 5000
UNTRANSCODED_BATCH = 2000

# AI status fields live on the `resources` table (migration 067). A resource
# is considered "completed" for a step when the column equals this value.
_AI_STATUS_COMPLETED = "completed"
_AI_STATUS_FIELDS: Dict[str, str] = {
    "transcribed": "transcript_status",
    "summarized": "summary_status",
    "analyzed": "visual_analysis_status",
}

_RESOURCES_NAME_TO_ATTR: Dict[str, str] = _name_to_attr(Resources)
_RESOURCE_ITEMS_NAME_TO_ATTR: Dict[str, str] = _name_to_attr(ResourceItems)
_RESOURCE_VERSIONS_NAME_TO_ATTR: Dict[str, str] = _name_to_attr(ResourceVersions)
_FOLDERS_NAME_TO_ATTR: Dict[str, str] = _name_to_attr(Folders)
_RESOURCE_TAGS_NAME_TO_ATTR: Dict[str, str] = _name_to_attr(ResourceTags)
_TAGS_NAME_TO_ATTR: Dict[str, str] = _name_to_attr(Tags)

#: The only columns ``merge_slide_prompt`` will write besides the slide map.
#: mig 455: the origin has to land in the same flush as the text it labels.
#: Kept to one key on purpose — see that method's docstring.
_SLIDE_PROMPT_EXTRA_KEYS: frozenset[str] = frozenset({"prompt_origin"})

# The exact column projection the legacy PostgREST ``find_by_hashes`` selected.
# We read the full ORM entity (the proven-injectable read shape) then project
# down to these keys so the returned dict is byte-identical to the REST body
# (no extra columns leak into the ``existing`` payload the caller echoes back).
_FIND_BY_HASH_COLS = (
    "id",
    "filename",
    "file_type",
    "mime_type",
    "file_size_bytes",
    "thumbnail_path",
    "cover_image_path",
    "created_at",
    "file_hash",
)


# ── @-reference picker: ONE kind ladder for both the filter and the counts ──
#
# The tab badge and the tab's own contents have to agree, so both are derived
# from the same expression instead of two hand-written predicates that drift.
# They did drift: the badge used to be tallied from the page the router was
# about to return — already narrowed to one kind and already cut off at
# ``limit`` — so opening the Video tab zeroed the Image badge and "All" could
# never exceed 50.
#
# The ladder mirrors ``app/services/ai/_mime_kind.py::kind_from_mime`` rung for
# rung, catch-all ``doc`` included. That function is what labels every row in
# the response, so a disagreement here would badge a resource under a tab that
# then refuses to list it (pre-fix: application/zip and friends counted as doc
# rows in "All" but were invisible under the Doc tab, which matched only
# ``text/`` and ``application/json``).
_PICKER_KINDS = ("video", "image", "audio", "pdf", "doc")


def _picker_kind_expr():
    """SQL twin of ``kind_from_mime``: a resource row -> its canonical kind."""
    from sqlalchemy import case

    mime = func.lower(func.coalesce(Resources.mime_type, ""))
    return case(
        (mime.regexp_match("^video/"), "video"),
        (mime.regexp_match("^image/"), "image"),
        (mime.regexp_match("^audio/"), "audio"),
        (mime.regexp_match("^application/pdf$"), "pdf"),
        else_="doc",
    )


def _picker_visibility_filters(
    *, user_id: str, q: str, scope_team_id: str | None
) -> list:
    """WHERE conditions for "resources this caller may reference".

    Extracted so the row query and the counts aggregate share ONE definition
    of visible: this is an authorization predicate, and a copy-pasted second
    version is how one of the two silently stops matching the other.

    Assumes the caller has already joined ``resource_items`` to ``resources``.

    ``scope_team_id`` is a CALLER-SUPPLIED, unvalidated query-string value, so
    it is compared as TEXT (a non-numeric value must yield zero matches, not
    raise) — see ``list_accessible_for_user`` for the full note.
    """
    from sqlalchemy import String, cast

    scope_id_text = cast(ResourceItems.scope_id, String)

    if scope_team_id is not None:
        # Issue-scoped picker: narrow to the current team (only if the caller
        # is a member — no escalation) OR the caller's personal team.
        membership_ids = (
            select(cast(TeamMembers.team_id, String))
            .where(
                TeamMembers.user_id == user_id,
                cast(TeamMembers.team_id, String) == scope_team_id,
            )
            .union(
                select(cast(Teams.id, String)).where(
                    Teams.owner_id == user_id, Teams.kind == "personal"
                )
            )
        )
    else:
        membership_ids = select(cast(TeamMembers.team_id, String)).where(
            TeamMembers.user_id == user_id
        )

    conditions = [
        Resources.is_trashed.is_(False),
        scope_id_text.in_(membership_ids),
    ]
    if q:
        conditions.append(Resources.filename.ilike(f"%{q}%"))
    return conditions


def _to_rest_value(value: Any) -> Any:
    """Coerce ONE native ORM read value to its PostgREST/REST wire form.

    Strategy-C value-type parity (AUTHZ-CRITICAL): the ORM read helpers hand
    back native ``uuid.UUID`` / ``datetime`` objects where the legacy REST
    (PostgREST) repo returned ``str``. Generic ``isinstance`` sweep (NOT a
    column-name allowlist) so EVERY uuid/datetime field is covered without
    enumeration, recursing into nested dicts and lists of dicts (e.g. the
    embedded ``resource`` / ``parsed_media`` sub-dicts). BIGINT ids are ``int``
    on both sides → pass through untouched. Enums are already unwrapped to
    ``.value`` upstream by ``_plain`` (strings pass through here, so enum
    read-parity is preserved). ``None`` and every other scalar pass through."""
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, (datetime, date)):
        # tz-aware UTC datetime → ``...+00:00`` matches PostgREST. (PostgREST
        # trims trailing zeros in microseconds; that residual cosmetic diff is
        # accepted + pre-existing across the rollout — we do NOT replicate it.)
        return value.isoformat()
    if isinstance(value, dict):
        return _rest_parity(value)
    if isinstance(value, list):
        return [_to_rest_value(v) for v in value]
    return value


def _rest_parity(d: Dict[str, Any]) -> Dict[str, Any]:
    """Return a NEW dict (immutable — never mutate the input) with strategy-C
    value-type coercion applied to every value: uuid → str, datetime → ISO
    str, recursing into nested dicts / lists of dicts. See ``_to_rest_value``.

    Applied at EVERY dict-returning boundary of this repo so the ORM dict is a
    true drop-in for the REST baseline (fixes the 6 ``creator_id`` authz
    comparisons in ai_router / resources_service that break on a native UUID)."""
    return {k: _to_rest_value(v) for k, v in d.items()}


def _resources_row_to_dict(obj: Any) -> Dict[str, Any]:
    """SELECT *-shaped dict for a ``resources`` ORM row (enum + value safe)."""
    return _rest_parity(_orm_obj_to_dict(obj, _RESOURCES_NAME_TO_ATTR))


def _resource_item_row_to_dict(obj: Any) -> Dict[str, Any]:
    """SELECT *-shaped dict for a ``resource_items`` ORM row (value safe)."""
    return _rest_parity(_orm_obj_to_dict(obj, _RESOURCE_ITEMS_NAME_TO_ATTR))


def _version_row_to_dict(obj: Any) -> Dict[str, Any]:
    """SELECT *-shaped dict for a ``resource_versions`` ORM row (value safe)."""
    return _rest_parity(_orm_obj_to_dict(obj, _RESOURCE_VERSIONS_NAME_TO_ATTR))


def _folder_row_to_dict(obj: Any) -> Dict[str, Any]:
    """SELECT *-shaped dict for a ``folders`` ORM row (value safe)."""
    return _rest_parity(_orm_obj_to_dict(obj, _FOLDERS_NAME_TO_ATTR))


def _resource_tag_row_to_dict(obj: Any) -> Dict[str, Any]:
    """SELECT *-shaped dict for a ``resource_tags`` ORM row (value safe)."""
    return _rest_parity(_orm_obj_to_dict(obj, _RESOURCE_TAGS_NAME_TO_ATTR))


def _tag_row_to_dict(obj: Any) -> Dict[str, Any]:
    """SELECT *-shaped dict for a ``tags`` ORM row (value safe)."""
    return _rest_parity(_orm_obj_to_dict(obj, _TAGS_NAME_TO_ATTR))


def _mappings_dict(row: Any) -> Dict[str, Any]:
    """Plain dict from a RETURNING ``.mappings()`` row, enum- and value-safe.

    RETURNING rows still pass through the column type result processors, so
    an ``Enum`` column comes back as an enum MEMBER here too — funnel through
    ``_plain`` for the same bare-str parity as reads — and uuid/datetime
    columns come back native, so funnel through ``_rest_parity`` for the same
    REST wire shape (uuid → str, datetime → ISO str)."""
    return _rest_parity({k: _plain(v) for k, v in dict(row).items()})


def merge_slide_prompt_map(
    current: Optional[Dict[str, Any]],
    slide_name: str,
    entry: Dict[str, Any],
) -> Dict[str, Any]:
    """Fold one slide's fields into the whole ``slide_prompts`` map.

    Two levels of merge, both load-bearing:
      - map level: every OTHER slide's entry is carried over untouched, so
        captioning slide 7 can never erase slides 1-6. This is the same
        promise ``SlidePromptStrip`` makes on the frontend PATCH path — the
        column has two writers and both have to keep it.
      - entry level: only the keys in ``entry`` are replaced, so a caption
        run (which produces ``en``/``zh`` only) leaves a hand-written
        ``neg_en``/``neg_zh`` in place.

    Pure and side-effect free — the repository method wraps it, and the
    clobber cases are asserted against this function directly.
    """
    merged: Dict[str, Any] = dict(current or {})
    existing = merged.get(slide_name)
    base: Dict[str, Any] = dict(existing) if isinstance(existing, dict) else {}
    base.update(entry)
    merged[slide_name] = base
    return merged


class ResourcesRepository(AsyncpgRepository):
    """Resource library data access (async, ORM-backed).

    All data-access methods on resources / resource_items / resource_versions /
    folders / resource_tags run on the ORM session scopes — including the former
    REST stragglers (resource_tags trio, smart-folder group, ``find_by_hashes``,
    the two temp-sweeper helpers), ported off ``self._get_client()`` so the
    tenant-scope choke point governs them. ``_bigint`` / ``_bigint_list`` come
    from ``AsyncpgRepository``."""

    TABLE = "resources"

    TABLE_RESOURCES = "resources"
    TABLE_ITEMS = "resource_items"
    TABLE_VERSIONS = "resource_versions"
    TABLE_FOLDERS = "folders"
    TABLE_RESOURCE_TAGS = "resource_tags"

    def __init__(self):
        pass

    # ── Resources CRUD ──────────────────────────────────────────────

    async def create_resource(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """INSERT a resource as an ORM instance (the flip-safe write path).

        A2.5: replaced the Core ``insert(Resources).values().returning()`` —
        which the scope choke point FORBIDS under a user scope (``_forbid_
        scoped_bulk_dml``: a Core INSERT never reaches ``before_insert`` so it
        can't be owner-stamped) — with a ``session.add(instance)`` flush. The
        instance flush DOES flow through ``before_insert`` (``_stamp_user_on_
        insert``): under a USER scope it stamps/asserts ``creator_id ==
        scope.user_id``; under SYSTEM it leaves the owner as-given; with the
        flag OFF it is a plain legacy insert (no stamp / no raise). The post-
        flush ``refresh`` reloads the server-default columns (snowflake ``id``,
        ``created_at``, the 3 ai-status enums, …) so the returned dict matches
        the old RETURNING-row shape exactly — it is wrapped in
        ``system_request_scope`` because ``session.refresh`` (and lazy-load of
        the post-flush-expired columns by ``_resources_row_to_dict``) issues a
        ``from_statement`` PK reload that the choke point treats as
        non-injectable (deny-by-default RAISE under a USER scope). Reading back
        the row we JUST wrote — whose ownership ``before_insert`` already
        verified equals the active scope — to materialize server defaults is a
        safe, owner-agnostic internal read.

        ENFORCEMENT-GATED (inert guarantee): the ``system_request_scope`` wrap is
        applied ONLY when ``resources`` is enforced (``is_enforced``). Flag-off the
        refresh can't raise (no injection) so no wrap is needed — and skipping it
        avoids emitting a spurious ``orm-refresh-own-write`` audit log + DB row on
        every create, keeping the flag-off path byte-for-byte legacy."""
        try:
            async with write_scope() as session:
                obj = Resources(
                    **{_RESOURCES_NAME_TO_ATTR.get(k, k): v for k, v in data.items()}
                )
                session.add(obj)
                await session.flush()  # before_insert fires (stamp/assert)
                refresh_cm = (
                    system_request_scope(reason="orm-refresh-own-write")
                    if is_enforced("resources")
                    else nullcontext()
                )
                async with refresh_cm:
                    await session.refresh(obj)  # load server-default columns
                    created = _resources_row_to_dict(obj)
            logger.info(f"Created resource: {data.get('filename')}")
            return created
        except Exception as e:
            logger.error(f"Failed to create resource: {e}")
            raise

    async def get_resource_by_id(self, resource_id: str) -> Optional[Dict[str, Any]]:
        """Read one resource. ``None`` means **the row is not visible**; a
        missing ambient scope RAISES.

        ⚠️ ``UnscopedQueryError`` is re-raised, NOT swallowed into ``None``.
        The distinction is the whole point: ``None`` is a DATA answer ("no such
        row, or not yours"), while ``UnscopedQueryError`` is a CALLER BUG ("you
        forgot to open a ``request_scope`` / ``user_session`` at the entry
        boundary"). Collapsing the second into the first makes a programming
        error indistinguishable from a legitimate 404 — every caller then
        translates "I am broken" into "your file is gone", and the fault is
        invisible in the error funnel because nothing 5xx's.

        That is not hypothetical: it is exactly how the cover-frame extraction
        outage stayed hidden. ``distribution_router.extract_cover_frames`` and
        ``select_cover_frame`` never opened a scope, this method logged an ERROR
        and returned ``None``, ``cover_frames.load_source_video`` read that as
        "source resource not found" and raised 404, and the user was told "That
        video is no longer available — pick a different one" about a video that
        was perfectly fine. Five consecutive attempts, all 404, zero 5xx.

        Every other exception keeps the legacy log-and-return-``None`` behaviour
        — narrowing only the fail-closed guard, whose entire contract is to be
        loud (``app.db.scope``: "forgetting to open a session surfaces
        immediately instead of silently returning every user's rows").
        """
        try:
            async with read_scope() as session:
                result = await session.execute(
                    select(Resources)
                    .where(Resources.id == self._bigint(resource_id))
                    .limit(1)
                )
                row = result.scalars().first()
                return _resources_row_to_dict(row) if row else None
        except UnscopedQueryError:
            logger.error(
                f"Failed to get resource {resource_id}: no ambient scope. This is a "
                f"caller bug (missing request_scope/user_session at the entry "
                f"boundary), NOT a missing row — re-raising so it cannot be "
                f"mistranslated into a 404."
            )
            raise
        except Exception as e:
            logger.error(f"Failed to get resource {resource_id}: {e}")
            return None

    async def get_resource_by_id_for_caller(
        self, resource_id: str, user_id: str
    ) -> Optional[Dict[str, Any]]:
        """Like ``get_resource_by_id``, but visibility-checked: OWNER
        (``creator_id == user_id``) OR any teammate whose ``resource_items.
        scope_id`` membership covers this resource — mirrors the
        team-membership check in ``resource_fetch_tool.py::_fetch_dispatch``
        and ``search_resources``. None on BOTH "doesn't exist" and "exists
        but not visible to this caller" (no existence leak — callers should
        404 either way).

        ``ai_router.py::_resolve_resource_to_platform_id`` used to call the
        plain ``get_resource_by_id`` above, which runs on a bare
        ``read_scope()``. Under ``SCOPE_ENFORCE_RESOURCES=true`` (production's
        real value — see CLAUDE.md's 部署陷阱), that read sits inside the
        caller's ambient USER scope (opened by ``ScopedRequestDep``), so the
        choke point injects ``creator_id == caller`` — a team member
        triggering AI processing on a resource shared to their team but owned
        by someone else got 404'd before PR #1743's "run as owner" identity
        fix ever got a chance to apply.

        The ``creator_id`` half of the predicate is EXPLICIT (not merely
        "the join happens to also match the owner's own resource_items row")
        so an owner is never denied by a resource with a missing/orphaned
        resource_items row — the normal create path always writes one, but
        nothing here should depend on that invariant holding for every
        historical row.

        The wrap is gated by ``is_enforced("resources")`` the same as every
        other team-membership-visibility read in this file: with the flag
        off, ``nullcontext()`` keeps this byte-for-byte with an ungated read
        (the choke point itself is inert for ``resources`` while off). The
        owner-or-teammate PREDICATE, however, is unconditional — it does not
        loosen just because the flag happens to be off.
        """
        from sqlalchemy import String, cast

        scope_cm = (
            system_request_scope(
                reason="get-resource-by-id-for-caller: owner-or-team-member "
                "visibility check, not a blind cross-tenant read"
            )
            if is_enforced("resources")
            else nullcontext()
        )
        try:
            membership_exists = (
                select(ResourceItems.id)
                .where(ResourceItems.resource_id == Resources.id)
                .where(
                    cast(ResourceItems.scope_id, String).in_(
                        select(cast(TeamMembers.team_id, String)).where(
                            TeamMembers.user_id == user_id
                        )
                    )
                )
                .exists()
            )
            async with scope_cm:
                async with read_scope() as session:
                    result = await session.execute(
                        select(Resources)
                        .where(Resources.id == self._bigint(resource_id))
                        .where(or_(Resources.creator_id == user_id, membership_exists))
                        .limit(1)
                    )
                    row = result.scalars().first()
                    return _resources_row_to_dict(row) if row else None
        except UnscopedQueryError:
            # Same rule as get_resource_by_id: "no scope" is a caller bug, not
            # an invisible row. Unreachable while ``is_enforced`` holds (the
            # system_request_scope wrap above supplies a scope), which is
            # precisely why it must not be swallowed if that ever changes.
            logger.error(
                f"Failed to get resource {resource_id} for caller {user_id}: no "
                f"ambient scope — caller bug, re-raising."
            )
            raise
        except Exception as e:
            logger.error(
                f"Failed to get resource {resource_id} for caller {user_id}: {e}"
            )
            return None

    async def get_resource_by_media_id(self, media_id: str) -> Optional[Dict[str, Any]]:
        try:
            async with read_scope() as session:
                result = await session.execute(
                    select(Resources)
                    .where(Resources.media_id == self._bigint(media_id))
                    .limit(1)
                )
                row = result.scalars().first()
                return _resources_row_to_dict(row) if row else None
        except Exception as e:
            logger.error(f"Failed to get resource by media_id {media_id}: {e}")
            return None

    async def get_resource_by_platform_id(
        self, platform_id: str
    ) -> Optional[Dict[str, Any]]:
        """Two-step lookup parsed_media.platform_id → resources.media_id.

        Kept as two queries (rather than a JOIN) to match legacy shape —
        caller may already have the parsed_media row cached and collapsing
        into a JOIN would diverge the cache key."""
        try:
            async with read_scope() as session:
                # parsed_media.platform_id → id (text lookup). Use raw text()
                # so we don't import ParsedMedia here just for one scalar.
                media_id = await session.scalar(
                    text(
                        "SELECT id FROM parsed_media "
                        "WHERE platform_id = :pid LIMIT 1"
                    ),
                    {"pid": platform_id},
                )
            if not media_id:
                return None
            # parsed_media.id and resources.media_id are both BIGINT
            # (Snowflake, migration 051); pass the int through directly.
            return await self.get_resource_by_media_id(media_id)
        except Exception as e:
            logger.error(f"Failed to get resource by platform_id {platform_id}: {e}")
            return None

    async def get_resource_by_media_id_and_creator(
        self, media_id: str, creator_id: str
    ) -> Optional[Dict[str, Any]]:
        try:
            async with read_scope() as session:
                result = await session.execute(
                    select(Resources)
                    .where(Resources.media_id == self._bigint(media_id))
                    .where(Resources.creator_id == creator_id)
                    .limit(1)
                )
                row = result.scalars().first()
                return _resources_row_to_dict(row) if row else None
        except Exception as e:
            logger.error(
                f"Failed to get resource for media={media_id}, "
                f"creator={creator_id}: {e}"
            )
            return None

    async def get_completed_resource_by_url_and_creator(
        self, url: str, creator_id: str
    ) -> Optional[Dict[str, Any]]:
        """L2 dedup probe. JOIN with parsed_media on media_id, filter on
        original_url and the appropriate completion status (image vs video).

        Returns the same nested shape as legacy: ``{id, media_id,
        parsed_media: {id, platform_id, original_url, ...}}`` so call sites
        read the inner dict the same way.

        Phase C task 2: migrated off the ``scoped_sql`` raw-``text()`` backstop
        to a real ORM JOIN (Resources INNER JOIN ParsedMedia). ``creator_id``
        is kept as an EXPLICIT filter — the same predicate the pre-A3 code
        used — so this stays byte-for-byte correct regardless of
        ``SCOPE_ENFORCE_RESOURCES``: flag-off, this is the only filter (legacy
        behaviour, unconditionally correct); flag-on, the do_orm_execute choke
        point additionally injects ``creator_id == scope.user_id`` from the
        ambient scope, redundant-but-harmless because it's the exact same
        value (the acting user IS ``creator_id`` on every real call path). No
        explicit ``system_request_scope``/``user_session`` wrap here — the
        single caller (``media_fetch_helpers.py``) already opens
        ``request_scope(Scope(user_id=auth.user_id))`` around this call, so an
        ambient USER scope is always present when enforcement is on."""
        try:
            async with read_scope() as session:
                result = await session.execute(
                    select(
                        Resources.id.label("r_id"),
                        Resources.media_id.label("r_media_id"),
                        ParsedMedia.id.label("p_id"),
                        ParsedMedia.platform_id,
                        ParsedMedia.original_url,
                        ParsedMedia.video_download_status,
                        ParsedMedia.image_download_status,
                        ParsedMedia.media_type,
                    )
                    .join(ParsedMedia, Resources.media_id == ParsedMedia.id)
                    .where(Resources.creator_id == creator_id)
                    .where(ParsedMedia.original_url == url)
                    .limit(1)
                )
                row = result.mappings().first()
            if not row:
                return None
            row = dict(row)

            mt = row.get("media_type")
            is_image = str(mt) in ("2", "68", "image", "images")
            status = (
                row.get("image_download_status")
                if is_image
                else row.get("video_download_status")
            )
            # ORM Enum(DownloadStatus) columns read back as enum MEMBERS, not
            # bare str (unlike the retired text() path) — _plain unwraps them.
            if _plain(status) != "completed":
                return None

            # ids/strings/statuses only (no uuid/datetime here) — _rest_parity is
            # a no-op for the current shape, applied for uniformity + safety if
            # the projection ever grows a uuid/timestamptz column.
            return _rest_parity(
                {
                    "id": row["r_id"],
                    "media_id": row["r_media_id"],
                    "parsed_media": {
                        "id": row["p_id"],
                        "platform_id": row["platform_id"],
                        "original_url": row["original_url"],
                        "video_download_status": _plain(row["video_download_status"]),
                        "image_download_status": _plain(row["image_download_status"]),
                        "media_type": row["media_type"],
                    },
                }
            )
        except Exception as e:
            logger.debug(
                f"[ResourcesRepo] L2 dedup probe failed url={url[:40]} "
                f"creator={creator_id}: {e}"
            )
            return None

    async def get_owned_platform_ids(
        self, platform_ids: List[str], creator_id: str
    ) -> set:
        """Of the given vids (parsed_media.platform_id), return the subset this
        user has already downloaded. ONE batched query for the whole list —
        no N+1. Empty input short-circuits to ``set()`` without a query.

        "已下载" 的判据是两列**任一**有值
        =================================
        ``resources.file_path IS NOT NULL`` 单独用是错的：那一列对
        ``source_type='web'``（平台解析下载的素材）**按设计为空** —— 共享下载
        字段只存 ``parsed_media``（PR-B）。而这个方法查的**全是** web 素材（它
        按 ``media_id`` JOIN ``parsed_media``），所以旧谓词恰好在它唯一服务的那
        类数据上系统性地答错。

        生产实测：旧谓词判定"已下载" 998 行，正确谓词 1196 行 —— **198 条用户
        其实已经下载过的素材被报成"没下载"**。后果不是报错：``media_soda_router``
        把它当 ``downloaded`` 标志送给前端，前端"默认只勾选新的"，于是这 198 条
        被默认勾上，用户**重复下载自己已经有的东西**。

        ⚠️ 为什么**不**复用 ``resolve_resource_file_path``
        ================================================
        那个函数回答的是"给我一个能读的**文件**路径"，因此它（正确地）把图文
        相册的目录前缀挡成 ``None``。但这里问的是**存在性**："这东西下载过没
        有"。相册是**下载过的** —— 拿取路径函数当谓词，8 条相册会被判成"没下
        载"，用户照样重复下载。

        同一个目录守卫，在取路径场景是保护，在存在性场景是错误答案。这是"目录
        形状"陷阱的第二种表现，所以这里刻意**不为了复用而复用**：存在性用自己
        的 SQL 谓词，且必须留在 SQL 里（一次批量查询，逐行调 Python 解析会退化
        成 N+1）。

        Phase C task 2: migrated off the ``scoped_sql`` raw-``text()`` backstop
        to a real ORM JOIN, same rationale as ``get_completed_resource_by_url_
        and_creator`` above — ``creator_id`` stays an EXPLICIT filter (correct
        regardless of ``SCOPE_ENFORCE_RESOURCES``), no explicit scope wrap
        here because the single caller (``media_soda_router.py``) already
        declares ``_scope: ScopedRequestDep`` on the endpoint, which opens an
        ambient USER scope for the whole request."""
        if not platform_ids:
            return set()
        try:
            async with read_scope() as session:
                result = await session.execute(
                    select(ParsedMedia.platform_id)
                    .distinct()
                    .select_from(Resources)
                    .join(ParsedMedia, Resources.media_id == ParsedMedia.id)
                    .where(Resources.creator_id == creator_id)
                    .where(ParsedMedia.platform_id.in_(list(platform_ids)))
                    # 两列任一有值即"已下载"（理由见 docstring）。相册在这里
                    # 靠 download_path 被正确算作已下载 —— 它没有单个文件，但
                    # 它确实下载过。
                    .where(
                        or_(
                            Resources.file_path.isnot(None),
                            ParsedMedia.download_path.isnot(None),
                        )
                    )
                )
                return {r[0] for r in result.all()}
        except Exception as e:
            logger.error(
                f"Failed to resolve owned platform_ids for creator "
                f"{creator_id}: {e}"
            )
            return set()

    async def update_resource(
        self, resource_id: str, data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """UPDATE a resource by id via load-then-modify, COMMITTING via
        write_scope (the P0 fix).

        A2.5: replaced the Core ``update(Resources).where().values().
        returning()`` — FORBIDDEN under a user scope (``_forbid_scoped_bulk_
        dml``: a bulk UPDATE tree can't be safely tenant-filtered) — with the
        sanctioned load-then-modify pattern. ``session.get`` flows through the
        SELECT injection: under a USER scope the choke point injects
        ``creator_id == scope.user_id``, so a row owned by another user loads
        as None (correct fail-closed isolation) and we return ``{}`` —
        preserving the legacy "update of a nonexistent id → {}" contract. The
        dirty-instance flush is governed by the unit-of-work, no Core DML.

        The post-flush ``refresh`` (re-materializing server-managed columns
        like ``updated_at`` for the returned dict) is wrapped in
        ``system_request_scope`` for the same reason as ``create_resource``: a
        ``from_statement`` PK reload is non-injectable → deny-by-default RAISE
        under a USER scope. The row's ownership was already proven by the
        injected ``session.get`` above, so reading it back is owner-agnostic-
        safe.

        ENFORCEMENT-GATED (inert guarantee): the ``system_request_scope`` wrap is
        applied ONLY when ``resources`` is enforced (``is_enforced``). Flag-off the
        refresh can't raise (no injection) so no wrap is needed — and skipping it
        avoids a spurious ``orm-refresh-own-write`` audit log + DB row on every
        update, keeping the flag-off path byte-for-byte legacy."""
        try:
            async with write_scope() as session:
                obj = await session.get(Resources, self._bigint(resource_id))
                if obj is None:
                    return {}
                # ISO-string timestamps would make asyncpg reject the flush
                # (see app.db.pg_coerce) — parse them at the boundary.
                normalized = coerce_datetime_strings(Resources, data)
                for k, v in normalized.items():
                    setattr(obj, _RESOURCES_NAME_TO_ATTR.get(k, k), v)
                await session.flush()
                refresh_cm = (
                    system_request_scope(reason="orm-refresh-own-write")
                    if is_enforced("resources")
                    else nullcontext()
                )
                async with refresh_cm:
                    await session.refresh(obj)
                    updated = _resources_row_to_dict(obj)
            logger.info(f"Updated resource {resource_id}")
            return updated
        except Exception as e:
            logger.error(f"Failed to update resource {resource_id}: {e}")
            raise

    async def merge_slide_prompt(
        self,
        resource_id: str,
        slide_name: str,
        entry: Dict[str, Any],
        *,
        extra: Optional[Mapping[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Merge ONE slide's prompt fields into ``resources.slide_prompts``.

        Returns the slide's resulting entry (``{}`` when the row is
        missing / not owned, mirroring ``update_resource``).

        The merge is whole-map (``merge_slide_prompt_map``) so no other
        slide is ever clobbered, and it happens INSIDE the write_scope
        session that persists it — reading through a separate scope first
        would widen the read-modify-write window across two transactions,
        and two slides captioned back-to-back on the same album is a
        perfectly ordinary thing for a user to do.

        ``extra`` carries the columns that must land in the SAME flush as the
        text — today only ``prompt_origin``, whose mig 455 rule is that it
        follows the LAST writer of the positive text. Written as a second
        PATCH it would leave a window where the new text is persisted under
        the previous writer's origin. The whitelist is deliberately narrow:
        a general column bag here would become a second, undocumented way to
        PATCH a resource row — the whole-row write this method exists to
        avoid.
        """
        extra = dict(extra or {})
        unknown = set(extra) - _SLIDE_PROMPT_EXTRA_KEYS
        if unknown:
            raise ValueError(
                f"merge_slide_prompt extra accepts only "
                f"{sorted(_SLIDE_PROMPT_EXTRA_KEYS)}; got {sorted(unknown)}"
            )
        try:
            async with write_scope() as session:
                obj = await session.get(Resources, self._bigint(resource_id))
                if obj is None:
                    return {}
                merged = merge_slide_prompt_map(obj.slide_prompts, slide_name, entry)
                # Reassign (not in-place mutate) — a plain JSONB dict is not
                # a MutableDict, so the unit of work only sees the change if
                # the attribute itself is set to a new object.
                obj.slide_prompts = merged
                for k, v in extra.items():
                    setattr(obj, _RESOURCES_NAME_TO_ATTR.get(k, k), v)
                await session.flush()
            logger.info(
                f"Merged slide prompt for resource {resource_id} slide {slide_name!r}"
            )
            return merged[slide_name]
        except Exception as e:
            logger.error(
                f"Failed to merge slide prompt for {resource_id} "
                f"slide {slide_name!r}: {e}"
            )
            raise

    async def delete_resource(self, resource_id: str) -> bool:
        """DELETE a resource by id via load-then-delete, COMMITTING via
        write_scope.

        A2.5: replaced the Core ``delete(Resources).where()`` — FORBIDDEN
        under a user scope (``_forbid_scoped_bulk_dml``) — with a
        load-then-``session.delete(instance)``. ``session.get`` flows through
        the SELECT injection: under a USER scope it injects ``creator_id ==
        scope.user_id``, so a CROSS-USER id loads None → no-op → still returns
        ``True``. Returning True on not-found / not-owned keeps the boolean
        interface stable (idempotent delete) AND is the correct isolation
        behaviour: a user cannot observe (or delete) another user's row, and a
        cross-user delete is silently a no-op rather than an error."""
        try:
            async with write_scope() as session:
                obj = await session.get(Resources, self._bigint(resource_id))
                if obj is not None:
                    await session.delete(obj)
                    # Not load-bearing: write_scope commits (and thus flushes)
                    # at block exit. Kept only to surface any FK/constraint
                    # error inside this try (so it is logged + re-raised here)
                    # rather than at the outer commit. Contrast create/update,
                    # where flush IS required (before_insert / dirty-UPDATE
                    # must hit the DB before the subsequent refresh).
                    await session.flush()
            logger.info(f"Deleted resource {resource_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to delete resource {resource_id}: {e}")
            raise

    async def count_resources_by_media_id(self, media_id: str) -> int:
        """Count how many resources (across ALL users) reference a parsed_media.

        A2.5 — DATA-LOSS FIX + well-formed projection. This is a CROSS-USER GC
        reference count: callers (``resources_service.permanent_delete`` /
        ``cleanup_expired_trash``) use ``remaining == 0`` to decide whether to
        delete the SHARED physical files + parsed_media record. Under
        enforcement, if this count were user-scoped, user A deleting their
        resource would NOT see user B's resource for the same media_id → it
        would delete files B still needs. So we make it ALWAYS GLOBAL by
        wrapping the query in ``system_request_scope`` (owner-agnostic
        regardless of the caller's ambient scope — it is semantically "does ANY
        user still reference this media"). Nesting SYSTEM inside the already-
        SYSTEM ``cleanup_expired_trash`` path is a no-op set/reset; inside the
        USER ``permanent_delete`` path it temporarily sets SYSTEM for this read
        then resets to USER.

        Also projects a scoped column — ``func.count(Resources.id)`` instead of
        the old ``func.count()`` + ``select_from`` — so the statement is
        well-formed (a bare ``count(*)`` over a scoped table with no projected
        scoped column is deny-by-default RAISE; here SYSTEM scope means no
        injection either way, but the projected form is the canonical shape).

        A4 — RE-RAISE ON ERROR (data-loss hardening): this method must NEVER
        fabricate a ``0``. A transient DB error returning 0 is the DANGEROUS
        value — the GC callers (``permanent_delete`` / ``cleanup_expired_trash``)
        read ``remaining == 0`` to decide whether to delete the SHARED physical
        files + parsed_media record, so a fake 0 would delete files other users
        still reference. We log and RE-RAISE; the callers catch and SKIP the
        physical-file/media GC on uncertainty (never GC on a count failure).
        NOTE: no scheduled sweeper reclaims those skipped files — the only orphan
        sweeper (``scheduled_cleanup._sweep_orphan_upload_dirs``) walks ONLY the
        ``teams/{scope}/uploads/{resource_id}/`` tree by resource_id, NOT the
        ``global/resources/.../{media_id}/`` download tree that
        ``_delete_physical_files`` handles, nor the parsed_media row — so they
        leak until a later successful permanent_delete or manual cleanup.
        Accepted: leaking files on a rare transient count error beats deleting
        files another user still references.

        ENFORCEMENT-GATED (inert guarantee): the ``system_request_scope`` wrap that
        forces this count GLOBAL is applied ONLY when ``resources`` is enforced
        (``is_enforced``). Flag-off the choke point does not inject the tenant
        filter into this query anyway (it is already a cross-user count regardless
        of ambient scope), so the wrap is a no-op for correctness — skipping it
        avoids a spurious ``media-refcount-gc`` audit log + DB row on every GC
        refcount, keeping the flag-off path byte-for-byte legacy. Flag-on the wrap
        applies, keeping the count global under a USER scope (the data-loss
        regression guard)."""
        try:
            count_cm = (
                system_request_scope(reason="media-refcount-gc")
                if is_enforced("resources")
                else nullcontext()
            )
            async with count_cm:
                async with read_scope() as session:
                    count = await session.scalar(
                        select(func.count(Resources.id)).where(
                            Resources.media_id == self._bigint(media_id)
                        )
                    )
            return int(count or 0)
        except Exception as e:
            # NEVER return a fabricated 0 — a count failure must abort the
            # caller's shared-file GC, not silently green-light it.
            logger.error(f"Failed to count resources for media {media_id}: {e}")
            raise

    # ── Hash-based duplicate lookup ─────────────────────────────────

    async def find_by_hash(self, file_hash: str, creator_id: str) -> list[dict]:
        """Find non-trashed resources with the same file hash for a given
        creator. Column projection matches legacy exactly."""
        try:
            async with read_scope() as session:
                result = await session.execute(
                    select(
                        Resources.id,
                        Resources.filename,
                        Resources.file_type,
                        Resources.mime_type,
                        Resources.file_size_bytes,
                        Resources.thumbnail_path,
                        Resources.cover_image_path,
                        Resources.created_at,
                    )
                    .where(Resources.file_hash == file_hash)
                    .where(Resources.creator_id == creator_id)
                    .where(Resources.is_trashed.is_(False))
                )
                return [_rest_parity(dict(r)) for r in result.mappings().all()]
        except Exception as e:
            logger.error(f"Failed to find resources by hash: {e}")
            return []

    # ── Resource Items ──────────────────────────────────────────────

    async def find_resource_item(
        self,
        resource_id: str,
        scope_type: Optional[str],
        scope_id: str,
        folder_id: str | None = None,
    ) -> dict | None:
        # PR-E Phase 1: scope_type accepted but unused (scope_id is unique).
        try:
            async with read_scope() as session:
                stmt = (
                    select(ResourceItems)
                    .where(ResourceItems.resource_id == self._bigint(resource_id))
                    .where(ResourceItems.scope_id == self._bigint(scope_id))
                )
                if folder_id:
                    stmt = stmt.where(
                        ResourceItems.folder_id == self._bigint(folder_id)
                    )
                else:
                    stmt = stmt.where(ResourceItems.folder_id.is_(None))
                result = await session.execute(stmt.limit(1))
                row = result.scalars().first()
                return _resource_item_row_to_dict(row) if row else None
        except Exception as e:
            logger.error(f"Failed to find resource_item: {e}")
            return None

    async def create_resource_item(self, data: Dict[str, Any]) -> Dict[str, Any]:
        # Choke-point coercion: callers pass snowflake ids as str (API layer)
        # or int interchangeably, and asyncpg's strict int8 codec rejects str
        # even with a ::BIGINT cast — a str scope_id 500'd the resources
        # upload path (2026-07-05). Coercing here protects all 9 call sites.
        data = {
            **data,
            **{
                k: self._bigint(data[k])
                for k in ("scope_id", "resource_id", "folder_id", "library_id")
                if data.get(k) is not None
            },
        }
        try:
            async with write_scope() as session:
                result = await session.execute(
                    insert(ResourceItems)
                    .values(**data)
                    .returning(*ResourceItems.__table__.columns)
                )
                row = result.mappings().first()
                created = _mappings_dict(row) if row else {}
            logger.info(
                f"Created resource_item for resource {data.get('resource_id')} "
                f"in scope {data.get('scope_id')}"
            )
            return created
        except Exception as e:
            logger.error(f"Failed to create resource_item: {e}")
            raise

    async def _resource_ids_for_platforms(self, platforms: List[str]) -> List[str]:
        """Resource ids whose linked parsed_media.source_platform is in
        ``platforms``. Two-step lookup retained for parity. Returns list[str]
        (the get_resource_items path feeds it into a bigint-coerced ANY()).

        Phase C task 3: migrated off raw ``text()`` SQL to ORM ``select()``s.

        ⚠️ SECURITY AUDIT NOTE (flagged, not resolved, by this migration): the
        ``resources`` SELECT below carries NO ``creator_id`` (or any other
        tenant) filter — it returns every resource across every user matching
        ``media_id``, byte-for-byte the same as the pre-migration raw SQL
        (Phase C policy: a pure ORM port must never add/remove a filter). The
        only caller (``get_resource_items``) uses the result purely as an ID
        pre-filter that is later AND-intersected with a properly
        ``resource_items.scope_id``-scoped query, so this has not been
        observed to leak data in practice — but it IS a genuine cross-tenant
        read and should get a dedicated security audit in a follow-up task.
        Wrapped in ``system_request_scope`` (gated by ``is_enforced``) to
        preserve this unconditionally-cross-tenant behaviour rather than
        accidentally filtering to one owner or fail-closed raising once
        enforcement flips on.
        """
        cleaned = [p.strip() for p in platforms if p and p.strip()]
        if not cleaned:
            return []
        try:
            scope_cm = (
                system_request_scope(
                    reason="resource-ids-for-platforms: pre-filter helper "
                    "with no tenant column filter (see docstring) — "
                    "preserved as-is, flagged for a future security audit"
                )
                if is_enforced("resources")
                else nullcontext()
            )
            async with scope_cm:
                async with read_scope() as session:
                    media_rows = await session.execute(
                        select(ParsedMedia.id).where(
                            ParsedMedia.source_platform.in_(cleaned)
                        )
                    )
                    media_ids_int = [r[0] for r in media_rows.all()]
                    if not media_ids_int:
                        return []
                    resource_rows = await session.execute(
                        select(Resources.id).where(
                            Resources.media_id.in_(media_ids_int)
                        )
                    )
                    return [str(r[0]) for r in resource_rows.all()]
        except Exception as e:
            logger.error(f"Failed to resolve resource ids for platforms: {e}")
            return []

    async def _resource_ids_with_all_tags(self, tag_ids: List[str]) -> List[str]:
        """Resource ids that carry EVERY tag in ``tag_ids`` (AND).

        Single-query GROUP BY HAVING count = N. ``tag_id`` is a bigint column
        (migration 077+); coerce the str ids and bind as a bigint array."""
        if not tag_ids:
            return []
        try:
            async with read_scope() as session:
                result = await session.execute(
                    text(
                        "SELECT resource_id FROM resource_tags "
                        "WHERE tag_id = ANY(:tag_ids) "
                        "GROUP BY resource_id "
                        "HAVING count(DISTINCT tag_id) = :n"
                    ),
                    {
                        "tag_ids": self._bigint_list(tag_ids),
                        "n": len(set(tag_ids)),
                    },
                )
                return [str(r["resource_id"]) for r in result.mappings().all()]
        except Exception as e:
            logger.error(f"Failed to intersect resource tag ids: {e}")
            return []

    async def get_resource_item(
        self, resource_id: str, scope_type: Optional[str], scope_id: str
    ) -> Optional[Dict[str, Any]]:
        # PR-E Phase 1: scope_type accepted but unused (scope_id is unique).
        try:
            async with read_scope() as session:
                result = await session.execute(
                    select(ResourceItems)
                    .where(ResourceItems.resource_id == self._bigint(resource_id))
                    .where(ResourceItems.scope_id == self._bigint(scope_id))
                    .limit(1)
                )
                row = result.scalars().first()
                return _resource_item_row_to_dict(row) if row else None
        except Exception as e:
            logger.error(f"Failed to get resource_item: {e}")
            return None

    async def get_resource_item_in_folder(
        self,
        resource_id: str,
        scope_type: Optional[str],
        scope_id: str,
        folder_id: str | None,
    ) -> Optional[Dict[str, Any]]:
        # PR-E Phase 1: scope_type accepted but unused (scope_id is unique).
        try:
            async with read_scope() as session:
                stmt = (
                    select(ResourceItems)
                    .where(ResourceItems.resource_id == self._bigint(resource_id))
                    .where(ResourceItems.scope_id == self._bigint(scope_id))
                )
                if folder_id:
                    stmt = stmt.where(
                        ResourceItems.folder_id == self._bigint(folder_id)
                    )
                else:
                    stmt = stmt.where(ResourceItems.folder_id.is_(None))
                result = await session.execute(stmt.limit(1))
                row = result.scalars().first()
                return _resource_item_row_to_dict(row) if row else None
        except Exception as e:
            logger.error(f"Failed to get resource_item in folder: {e}")
            return None

    async def get_first_resource_item(
        self, resource_id: str
    ) -> Optional[Dict[str, Any]]:
        try:
            async with read_scope() as session:
                result = await session.execute(
                    select(ResourceItems)
                    .where(ResourceItems.resource_id == self._bigint(resource_id))
                    .limit(1)
                )
                row = result.scalars().first()
                return _resource_item_row_to_dict(row) if row else None
        except Exception as e:
            logger.error(f"Failed to get first resource_item: {e}")
            return None

    async def update_resource_item(
        self, item_id: str, data: Dict[str, Any]
    ) -> Dict[str, Any]:
        try:
            async with write_scope() as session:
                result = await session.execute(
                    update(ResourceItems)
                    .where(ResourceItems.id == self._bigint(item_id))
                    .values(**data)
                    .returning(*ResourceItems.__table__.columns)
                )
                row = result.mappings().first()
                return _mappings_dict(row) if row else {}
        except Exception as e:
            logger.error(f"Failed to update resource_item {item_id}: {e}")
            raise

    async def delete_resource_item(self, item_id: str) -> bool:
        """Delete a resource_item by ID. The DB trigger auto-trashes the
        parent resource if this was the last reference."""
        try:
            async with write_scope() as session:
                await session.execute(
                    sa_delete(ResourceItems).where(
                        ResourceItems.id == self._bigint(item_id)
                    )
                )
            logger.info(f"Deleted resource_item {item_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to delete resource_item {item_id}: {e}")
            raise

    async def count_resource_items(self, resource_id: str) -> int:
        try:
            async with read_scope() as session:
                count = await session.scalar(
                    select(func.count())
                    .select_from(ResourceItems)
                    .where(ResourceItems.resource_id == self._bigint(resource_id))
                )
            return int(count or 0)
        except Exception as e:
            logger.error(f"Failed to count items for resource {resource_id}: {e}")
            return 0

    # ── Gallery (first-class gallery entity, PR-A) ──────────────────

    async def validate_scope_image_ids(
        self, scope_id: str, image_ids: List[str]
    ) -> set:
        """Of ``image_ids``, return the subset that are image resources with a
        ``resource_items`` row in ``scope_id``. Used to validate a gallery's
        proposed children belong to the caller's scope and are actually images.

        Empty input short-circuits to ``set()`` without a query. Returns a set
        of the ids AS PASSED (str) so the caller can diff against its input.

        Phase C task 3: migrated off raw ``text()`` SQL to an ORM
        INNER JOIN. The authorization boundary here is
        ``resource_items.scope_id`` (team/project scope), NOT
        ``resources.creator_id`` — a resource_items row can be shared by
        every member of a team, so filtering by creator_id would wrongly
        reject a team member validating a sibling's upload. No creator_id
        filter is added (byte-for-byte equivalent to the pre-migration SQL).
        Wrapped in ``system_request_scope`` (gated by ``is_enforced``) since
        Resources IS a UserScoped model and this legitimately needs to see
        every owner's rows within the given scope_id.
        """
        if not image_ids:
            return set()
        try:
            id_ints = self._bigint_list(image_ids)
            scope_cm = (
                system_request_scope(
                    reason="validate-scope-image-ids: authorization boundary "
                    "is resource_items.scope_id (team/project), not "
                    "creator_id — no per-owner filter by design"
                )
                if is_enforced("resources")
                else nullcontext()
            )
            async with scope_cm:
                async with read_scope() as session:
                    result = await session.execute(
                        select(Resources.id)
                        .distinct()
                        .select_from(Resources)
                        .join(ResourceItems, ResourceItems.resource_id == Resources.id)
                        .where(Resources.id.in_(id_ints))
                        .where(ResourceItems.scope_id == self._bigint(scope_id))
                        .where(Resources.mime_type.like("image/%"))
                    )
                    found = {int(row[0]) for row in result.all()}
            # Map back to the caller's original str/int representation.
            return {orig for orig in image_ids if self._bigint(orig) in found}
        except Exception as e:
            logger.error(f"Failed to validate scope image ids: {e}")
            return set()

    async def set_gallery_items(
        self, gallery_id: str, image_ids: List[str]
    ) -> List[Dict[str, Any]]:
        """Replace a gallery's ordered children with ``image_ids`` (full reset).

        The delete + re-insert run inside ONE ``write_scope`` so the membership
        swap is atomic (all-or-nothing). ``position`` is the 0-based index of
        each id in the passed order. Returns the new junction rows."""
        gid = self._bigint(gallery_id)
        rows_out: List[Dict[str, Any]] = []
        try:
            async with write_scope() as session:
                await session.execute(
                    sa_delete(GalleryItems).where(GalleryItems.gallery_id == gid)
                )
                if image_ids:
                    values = [
                        {
                            "gallery_id": gid,
                            "image_id": self._bigint(image_id),
                            "position": idx,
                        }
                        for idx, image_id in enumerate(image_ids)
                    ]
                    result = await session.execute(
                        insert(GalleryItems)
                        .values(values)
                        .returning(*GalleryItems.__table__.columns)
                    )
                    rows_out = [_mappings_dict(r) for r in result.mappings().all()]
            logger.info(f"Set {len(image_ids)} gallery items for gallery {gallery_id}")
            return rows_out
        except Exception as e:
            logger.error(f"Failed to set gallery items for {gallery_id}: {e}")
            raise

    async def get_gallery_items(self, gallery_id: str) -> List[Dict[str, Any]]:
        """Ordered child images of a gallery: ``[{id, filename,
        thumbnail_path, position}, ...]`` sorted by ``position``."""
        try:
            async with read_scope() as session:
                result = await session.execute(
                    text(
                        "SELECT r.id, r.filename, r.thumbnail_path, gi.position "
                        "FROM gallery_items gi "
                        "INNER JOIN resources r ON r.id = gi.image_id "
                        "WHERE gi.gallery_id = :gid "
                        "ORDER BY gi.position ASC"
                    ),
                    {"gid": self._bigint(gallery_id)},
                )
                rows = [_rest_parity(dict(r)) for r in result.mappings().all()]
            return rows
        except Exception as e:
            logger.error(f"Failed to get gallery items for {gallery_id}: {e}")
            return []

    async def get_scope_gallery_membership(self, scope_id: str) -> Dict[str, Any]:
        """Gallery membership the frontend library list needs but cannot read
        itself — ``gallery_items`` is a service-role-only table (RLS lockdown,
        mig 375), so the direct supabase-js list query has no access to it.

        Returns, for one workspace ``scope_id``::

            {
              "child_image_ids": [str, ...],       # resources to hide
              "gallery_counts": {gallery_id: int}  # ▣ badge child counts
            }

        - ``child_image_ids``: resources that are a child of some gallery AND
          have a ``resource_items`` row in this scope — the rows the direct
          list must exclude so a gallery reads as a single tile (mirrors the
          backend list's ``NOT EXISTS (... gallery_items ...)`` predicate).
        - ``gallery_counts``: child count per gallery in this scope — the value
          the backend list's computed ``gallery_count`` column provides but the
          direct query cannot.

        Both sets are small (galleries are a fraction of a scope); the frontend
        caches this per scope and filters/annotates client-side.
        """
        try:
            sid = self._bigint(scope_id)
        except Exception:
            return {"child_image_ids": [], "gallery_counts": {}}
        try:
            async with read_scope() as session:
                child_result = await session.execute(
                    text(
                        "SELECT DISTINCT gi.image_id AS id "
                        "FROM gallery_items gi "
                        "INNER JOIN resource_items i "
                        "  ON i.resource_id = gi.image_id "
                        "WHERE i.scope_id = :sid"
                    ),
                    {"sid": sid},
                )
                child_ids = [str(r["id"]) for r in child_result.mappings().all()]

                count_result = await session.execute(
                    text(
                        "SELECT gi.gallery_id AS gallery_id, "
                        "       COUNT(*) AS n "
                        "FROM gallery_items gi "
                        "INNER JOIN resource_items i "
                        "  ON i.resource_id = gi.gallery_id "
                        "WHERE i.scope_id = :sid "
                        "GROUP BY gi.gallery_id"
                    ),
                    {"sid": sid},
                )
                counts = {
                    str(r["gallery_id"]): int(r["n"])
                    for r in count_result.mappings().all()
                }
            return {"child_image_ids": child_ids, "gallery_counts": counts}
        except Exception as e:
            logger.error(f"Failed to get scope gallery membership for {scope_id}: {e}")
            return {"child_image_ids": [], "gallery_counts": {}}

    # ── Listing with filters (the 22-arg behemoth) ──────────────────

    @staticmethod
    def _build_mime_sql(types: Optional[List[str]]) -> Optional[str]:
        """Translate type categories into a SQL ``OR`` expression. Returns
        None when no filter is needed. Values are inlined as SQL string
        literals (a fixed set of well-known LIKE patterns — no injection
        surface)."""
        if not types:
            return None
        categories = {t.strip() for t in types if t and t.strip()}
        if not categories:
            return None

        document_clause = (
            "(r.mime_type = 'application/pdf' "
            "OR r.mime_type LIKE 'application/msword%' "
            "OR r.mime_type LIKE 'application/vnd.%' "
            "OR r.mime_type LIKE 'text/%')"
        )
        known_clause = (
            "(r.mime_type LIKE 'video/%' "
            "OR r.mime_type LIKE 'image/%' "
            "OR r.mime_type LIKE 'audio/%' "
            "OR r.mime_type = 'application/pdf' "
            "OR r.mime_type LIKE 'application/msword%' "
            "OR r.mime_type LIKE 'application/vnd.%' "
            "OR r.mime_type = 'application/x-mediahub-gallery' "
            "OR r.mime_type LIKE 'text/%')"
        )

        clauses: List[str] = []
        for category in categories:
            if category == "video":
                clauses.append("r.mime_type LIKE 'video/%'")
            elif category == "image":
                clauses.append("r.mime_type LIKE 'image/%'")
            elif category == "audio":
                clauses.append("r.mime_type LIKE 'audio/%'")
            elif category == "document":
                clauses.append(document_clause)
            elif category == "gallery":
                clauses.append("r.mime_type = 'application/x-mediahub-gallery'")
            elif category == "other":
                clauses.append(f"NOT {known_clause}")
            # Silently ignore unknown categories (matches legacy behaviour).

        if not clauses:
            return None
        return "(" + " OR ".join(clauses) + ")"

    async def get_resource_items(
        self,
        scope_id: str,
        scope_type: Optional[str] = None,
        folder_id: Optional[str] = None,
        all_folders: bool = False,
        limit: Optional[int] = None,
        include_trashed: bool = False,
        tag_ids: Optional[List[str]] = None,
        min_rating: Optional[int] = None,
        types: Optional[List[str]] = None,
        source_types: Optional[List[str]] = None,
        platforms: Optional[List[str]] = None,
        ai_transcribed: Optional[bool] = None,
        ai_summarized: Optional[bool] = None,
        ai_analyzed: Optional[bool] = None,
        ai_has_prompt: Optional[bool] = None,
        created_after: Optional[date] = None,
        created_before: Optional[date] = None,
        duration_min: Optional[int] = None,
        duration_max: Optional[int] = None,
        aspect_ratios: Optional[List[str]] = None,
        min_likes: Optional[int] = None,
        min_comments: Optional[int] = None,
        min_favorites: Optional[int] = None,
        min_shares: Optional[int] = None,
        social_combine: str = "and",
        has_comments: Optional[bool] = None,
        include_gallery_children: bool = False,
    ) -> List[Dict[str, Any]]:
        """List resource_items joined to their resources.

        Same dynamic-WHERE SQL the asyncpg impl built; the embedded
        ``resource`` is produced by ``row_to_json(r.*)`` and decoded to a
        dict. Preserves all per-parameter semantics including the no-ops
        (``aspect_ratios`` + social metrics) the frontend chip applies
        client-side. Bound named params (:name) — no asyncpg ``$N``."""
        try:
            # AND-semantic tag filter — pre-resolve via helper.
            matched_resource_ids: Optional[List[str]] = None
            if tag_ids:
                matched_resource_ids = await self._resource_ids_with_all_tags(tag_ids)
                if not matched_resource_ids:
                    return []

            # Platform pre-resolution + intersection.
            if platforms:
                platform_resource_ids = await self._resource_ids_for_platforms(
                    platforms
                )
                if not platform_resource_ids:
                    return []
                if matched_resource_ids is None:
                    matched_resource_ids = platform_resource_ids
                else:
                    platform_set = set(platform_resource_ids)
                    matched_resource_ids = [
                        rid for rid in matched_resource_ids if rid in platform_set
                    ]
                    if not matched_resource_ids:
                        return []

            # Build dynamic WHERE with named bind params.
            where: List[str] = ["i.scope_id = :scope_id"]
            params: Dict[str, Any] = {"scope_id": self._bigint(scope_id)}

            if folder_id:
                where.append("i.folder_id = :folder_id")
                params["folder_id"] = self._bigint(folder_id)
            elif not all_folders:
                # Default = root-level items only (the folder-browsing UI).
                # all_folders=True skips the predicate so scope-wide pickers
                # (e.g. Distribution Publish) also see items filed in folders.
                where.append("i.folder_id IS NULL")

            if not include_trashed:
                where.append("r.is_trashed = false")

            # Gallery children are hidden from the normal library listing so a
            # gallery reads as a single tile. A child is any resource that
            # appears as a gallery_items.image_id. include_gallery_children=True
            # opts out (e.g. a picker that wants the raw images too).
            if not include_gallery_children:
                where.append(
                    "NOT EXISTS (SELECT 1 FROM gallery_items gi "
                    "WHERE gi.image_id = i.resource_id)"
                )

            if matched_resource_ids is not None:
                where.append("i.resource_id = ANY(:matched_ids)")
                params["matched_ids"] = self._bigint_list(matched_resource_ids)

            if min_rating is not None:
                where.append("r.rating >= :min_rating")
                params["min_rating"] = int(min_rating)

            mime_sql = self._build_mime_sql(types)
            if mime_sql:
                where.append(mime_sql)

            # Provenance filter — restrict to specific resource.source_type
            # values (web / upload / generated / derived). Used by the
            # Distribution Publish picker to exclude platform-downloaded
            # ("web") material and list only the user's own content.
            if source_types:
                where.append("r.source_type = ANY(:source_types)")
                params["source_types"] = list(source_types)

            # AI status filters: each requires == "completed".
            for key, flag, column in (
                ("ai_transcribed", ai_transcribed, _AI_STATUS_FIELDS["transcribed"]),
                ("ai_summarized", ai_summarized, _AI_STATUS_FIELDS["summarized"]),
                ("ai_analyzed", ai_analyzed, _AI_STATUS_FIELDS["analyzed"]),
            ):
                if flag is True:
                    where.append(f'r."{column}" = :{key}')
                    params[key] = _AI_STATUS_COMPLETED

            # "Has prompt" filter: any of the four prompt fields non-empty.
            # Not a status column (no _AI_STATUS_FIELDS entry) — "non-empty"
            # semantics differ per column type. Text columns (gen_prompt /
            # gen_prompt_negative / gen_prompt_json — the latter is TEXT
            # despite storing JSON, migration 392) need IS NOT NULL AND <> ''.
            # slide_prompts is JSONB and must also exclude the JSON literals
            # 'null' and '{}' (an empty object is not "has a prompt"). No
            # bind params needed — every literal here is a fixed constant,
            # never user input.
            if ai_has_prompt is True:
                where.append(
                    "("
                    "(r.gen_prompt IS NOT NULL AND r.gen_prompt <> '') OR "
                    "(r.gen_prompt_negative IS NOT NULL AND r.gen_prompt_negative <> '') OR "
                    "(r.gen_prompt_json IS NOT NULL AND r.gen_prompt_json <> '') OR "
                    "(r.slide_prompts IS NOT NULL "
                    "AND r.slide_prompts <> 'null'::jsonb "
                    "AND r.slide_prompts <> '{}'::jsonb)"
                    ")"
                )

            if created_after is not None:
                where.append("r.created_at >= :created_after")
                params["created_after"] = datetime.combine(
                    created_after, datetime.min.time(), tzinfo=timezone.utc
                )
            if created_before is not None:
                where.append("r.created_at <= :created_before")
                params["created_before"] = datetime.combine(
                    created_before, datetime.max.time(), tzinfo=timezone.utc
                )

            if duration_min is not None:
                where.append("r.duration_seconds >= :duration_min")
                params["duration_min"] = int(duration_min)
            if duration_max is not None:
                where.append("r.duration_seconds <= :duration_max")
                params["duration_max"] = int(duration_max)

            # Tail no-ops kept for parity (filtered client-side).
            _ = (
                aspect_ratios,
                min_likes,
                min_comments,
                min_favorites,
                min_shares,
                social_combine,
                has_comments,
            )

            # NOTE: resource_items has NO ``updated_at`` column in the current
            # schema (PR-E era). The legacy ``select("*, resource:...")``
            # returned only the columns that exist, so callers never depend on
            # an item-level ``updated_at``. We select the real item columns +
            # the embedded ``resource`` (row_to_json, decoded to a dict) and
            # alias i.created_at so it survives the resource overlay.
            limit_sql = ""
            if limit is not None and limit > 0:
                limit_sql = " LIMIT :limit"
                params["limit"] = int(limit)
            sql = (
                "SELECT i.id, i.resource_id, i.scope_id, "
                "       i.folder_id, i.added_by, i.library_id, "
                "       i.created_at AS i_created_at, "
                "       row_to_json(r.*) AS resource, "
                "       (SELECT count(*) FROM gallery_items gc "
                "        WHERE gc.gallery_id = r.id) AS gallery_count "
                "FROM resource_items i "
                "INNER JOIN resources r ON i.resource_id = r.id "
                f"WHERE {' AND '.join(where)} "
                f"ORDER BY i.created_at DESC{limit_sql}"
            )

            async with read_scope() as session:
                result = await session.execute(text(sql), params)
                rows = [dict(r) for r in result.mappings().all()]
            for row in rows:
                resource = row.get("resource")
                if isinstance(resource, str):
                    resource = json.loads(resource)
                    row["resource"] = resource
                # gallery_count rides along inside the resource object so the
                # frontend reads it as ``resource.gallery_count`` (0 for a
                # non-gallery row — gallery_items only has rows for galleries).
                gallery_count = row.pop("gallery_count", 0)
                if isinstance(resource, dict):
                    resource["gallery_count"] = int(gallery_count or 0)
                row["created_at"] = row.pop("i_created_at")
            return [_rest_parity(row) for row in rows]
        except Exception as e:
            logger.error(f"Failed to get resource items: {e}")
            return []

    # ── Trash listing ───────────────────────────────────────────────

    async def get_expired_trashed_resources(
        self, older_than_days: int = 30, limit: int = EXPIRED_TRASH_BATCH
    ) -> List[Dict[str, Any]]:
        """Trashed resources older than N days, for permanent cleanup.

        Bounded + ordered to match the REST twin: at most ``limit`` rows,
        oldest-trashed first. Was an unbounded SELECT (whole expired set into
        RAM at scale); the daily sweeper re-runs to drain a larger backlog.

        C2: ``media_id`` and ``thumbnail_path`` MUST be in this projection.
        This is the only unattended cleanup path (``scheduled_cleanup.py`` ->
        ``resources_service.cleanup_expired_trash``), and that service reads
        both ``resource.get("media_id")`` (to decide whether the shared
        parsed_media row + physical file can be GC'd) and
        ``resource.get("thumbnail_path")`` (``_delete_physical_files``'s
        thumbnail cleanup branch). Before this fix the projection only
        carried id/file_path/cover_image_path, so ``media_id`` was silently
        None for every row on this path — the scheduled sweeper matched
        every kept-alive row as if it had no parsed_media at all and never
        ran the shared-object GC branch, and thumbnail_path was dead code
        here specifically.
        """
        # ⛔ 这个投影喂给 ``resources_service._delete_physical_files``，所以
        # **永远不要**在消费端给它接 PR-B 阶梯（``resolve_resource_file_path``）。
        # ``file_path`` 为空在删除语义下表示"这一行没有自己的文件"，不是"去
        # parsed_media 找一个来删" —— 那份是多用户共享的，顺着阶梯删会造成跨租户
        # 数据丢失。``media_id`` 在这个投影里，是给共享对象的**引用检查**用的
        # （``exclude`` 名单），不是给"回落取路径"用的。完整理由见
        # ``resources_service._delete_physical_files`` 里的同款标注。
        try:
            cutoff = datetime.now(timezone.utc) - timedelta(days=older_than_days)
            async with read_scope() as session:
                result = await session.execute(
                    select(
                        Resources.id,
                        Resources.file_path,
                        Resources.cover_image_path,
                        Resources.media_id,
                        Resources.thumbnail_path,
                    )
                    .where(Resources.is_trashed.is_(True))
                    .where(Resources.trashed_at < cutoff)
                    .order_by(Resources.trashed_at.asc())
                    .limit(limit)
                )
                return [_rest_parity(dict(r)) for r in result.mappings().all()]
        except Exception as e:
            logger.error(f"Failed to get expired trashed resources: {e}")
            return []

    async def get_trashed_resources(
        self, scope_type: Optional[str], scope_id: str
    ) -> List[Dict[str, Any]]:
        """Trashed resources in a scope. JOIN with resources, re-shape flat
        row → ``{item_columns..., resource: {...}}`` to match the
        embedded-PostgREST shape callers depend on. ``row_to_json(r.*)`` is
        decoded explicitly (the driver returns it as a str)."""
        try:
            async with read_scope() as session:
                result = await session.execute(
                    text(
                        "SELECT i.id, i.resource_id, i.scope_id, "
                        "       i.folder_id, i.added_by, i.library_id, "
                        "       i.created_at AS i_created_at, "
                        "       row_to_json(r.*) AS resource "
                        "FROM resource_items i "
                        "INNER JOIN resources r ON i.resource_id = r.id "
                        "WHERE i.scope_id = :scope_id "
                        "  AND r.is_trashed = true "
                        "ORDER BY i.created_at DESC"
                    ),
                    {"scope_id": self._bigint(scope_id)},
                )
                rows = [dict(r) for r in result.mappings().all()]
            for row in rows:
                resource = row.get("resource")
                if isinstance(resource, str):
                    row["resource"] = json.loads(resource)
                row["created_at"] = row.pop("i_created_at")
            return [_rest_parity(row) for row in rows]
        except Exception as e:
            logger.error(f"Failed to get trashed resources: {e}")
            return []

    # ── Resource Versions ───────────────────────────────────────────

    # resource_versions BIGINT columns that snowflake ids travel into as
    # strings. asyncpg refuses to cast str → int8 so coerce at the boundary.
    _VERSION_BIGINT_COLS = ("resource_id", "file_size_bytes")

    async def create_version(self, data: Dict[str, Any]) -> Dict[str, Any]:
        try:
            coerced = {
                k: (self._bigint(v) if k in self._VERSION_BIGINT_COLS else v)
                for k, v in data.items()
            }
            async with write_scope() as session:
                result = await session.execute(
                    insert(ResourceVersions)
                    .values(**coerced)
                    .returning(*ResourceVersions.__table__.columns)
                )
                row = result.mappings().first()
                created = _mappings_dict(row) if row else {}
            logger.info(
                f"Created version {coerced.get('version_number')} "
                f"for resource {coerced.get('resource_id')}"
            )
            return created
        except Exception as e:
            logger.error(f"Failed to create version: {e}")
            raise

    async def get_versions(self, resource_id: str) -> List[Dict[str, Any]]:
        try:
            async with read_scope() as session:
                result = await session.execute(
                    select(ResourceVersions)
                    .where(ResourceVersions.resource_id == self._bigint(resource_id))
                    .order_by(ResourceVersions.version_number.desc())
                )
                return [_version_row_to_dict(r) for r in result.scalars().all()]
        except Exception as e:
            logger.error(f"Failed to get versions for resource {resource_id}: {e}")
            return []

    async def get_version_by_id(self, version_id: str) -> Optional[Dict[str, Any]]:
        try:
            async with read_scope() as session:
                result = await session.execute(
                    select(ResourceVersions)
                    .where(ResourceVersions.id == self._bigint(version_id))
                    .limit(1)
                )
                row = result.scalars().first()
                return _version_row_to_dict(row) if row else None
        except Exception as e:
            logger.error(f"Failed to get version {version_id}: {e}")
            return None

    async def get_version_by_number(
        self, resource_id: str, version_number: int
    ) -> Optional[Dict[str, Any]]:
        try:
            async with read_scope() as session:
                result = await session.execute(
                    select(ResourceVersions)
                    .where(ResourceVersions.resource_id == self._bigint(resource_id))
                    .where(ResourceVersions.version_number == int(version_number))
                    .limit(1)
                )
                row = result.scalars().first()
                return _version_row_to_dict(row) if row else None
        except Exception as e:
            logger.error(
                f"Failed to get version {version_number} for {resource_id}: {e}"
            )
            return None

    async def delete_version(self, version_id: str) -> bool:
        try:
            async with write_scope() as session:
                await session.execute(
                    sa_delete(ResourceVersions).where(
                        ResourceVersions.id == self._bigint(version_id)
                    )
                )
            logger.info(f"Deleted version {version_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to delete version {version_id}: {e}")
            raise

    async def update_version(
        self, version_id: str, data: Dict[str, Any]
    ) -> Dict[str, Any]:
        try:
            # ISO-string timestamps would make asyncpg roll back the whole
            # UPDATE (see app.db.pg_coerce) — parse them at the boundary.
            normalized = coerce_datetime_strings(ResourceVersions, data)
            async with write_scope() as session:
                result = await session.execute(
                    update(ResourceVersions)
                    .where(ResourceVersions.id == self._bigint(version_id))
                    .values(**normalized)
                    .returning(*ResourceVersions.__table__.columns)
                )
                row = result.mappings().first()
                return _mappings_dict(row) if row else {}
        except Exception as e:
            logger.error(f"Failed to update version {version_id}: {e}")
            raise

    async def get_untranscoded_video_versions(
        self, limit: int = UNTRANSCODED_BATCH
    ) -> List[Dict[str, Any]]:
        """Video versions never transcoded — NULL ``transcode_status`` AND
        non-NULL ``file_path``. Projection matches legacy.

        Bounded + ordered to match the REST twin: at most ``limit`` rows by id.
        The caller marks each ``pending`` so it leaves this set, making the
        batch re-runnable to drain past one call.
        """
        try:
            async with read_scope() as session:
                result = await session.execute(
                    select(
                        ResourceVersions.id,
                        ResourceVersions.resource_id,
                        ResourceVersions.mime_type,
                        ResourceVersions.file_path,
                    )
                    .where(ResourceVersions.mime_type.like("video/%"))
                    .where(ResourceVersions.transcode_status.is_(None))
                    .where(ResourceVersions.file_path.isnot(None))
                    .order_by(ResourceVersions.id.asc())
                    .limit(limit)
                )
                return [_rest_parity(dict(r)) for r in result.mappings().all()]
        except Exception as e:
            logger.error(f"Failed to get untranscoded video versions: {e}")
            return []

    async def get_next_version_number(self, resource_id: str) -> int:
        """Next monotonic version_number for a resource. Returns 1 when the
        resource has no versions yet."""
        try:
            async with read_scope() as session:
                current = await session.scalar(
                    select(
                        func.coalesce(func.max(ResourceVersions.version_number), 0)
                    ).where(ResourceVersions.resource_id == self._bigint(resource_id))
                )
            return int(current or 0) + 1
        except Exception as e:
            logger.error(f"Failed to get next version for resource {resource_id}: {e}")
            return 1

    # ── Folders ─────────────────────────────────────────────────────

    async def create_folder(self, data: Dict[str, Any]) -> Dict[str, Any]:
        # Same choke-point coercion as create_resource_item (#1054): callers
        # hand snowflake ids around as strings, but asyncpg's strict int8
        # codec rejects str for BIGINT columns.
        data = {
            **data,
            **{
                k: self._bigint(data[k])
                for k in ("scope_id", "parent_id")
                if data.get(k) is not None
            },
        }
        try:
            async with write_scope() as session:
                result = await session.execute(
                    insert(Folders).values(**data).returning(*Folders.__table__.columns)
                )
                row = result.mappings().first()
                created = _mappings_dict(row) if row else {}
            logger.info(f"Created folder: {data.get('name')}")
            return created
        except Exception as e:
            logger.error(f"Failed to create folder: {e}")
            raise

    async def get_trashed_folders(
        self, scope_type: Optional[str], scope_id: str
    ) -> List[Dict[str, Any]]:
        # PR-E Phase 1: scope_type accepted but unused (scope_id is unique).
        try:
            async with read_scope() as session:
                result = await session.execute(
                    select(Folders)
                    .where(Folders.scope_id == self._bigint(scope_id))
                    .where(Folders.is_trashed.is_(True))
                    .order_by(Folders.trashed_at.desc())
                )
                return [_folder_row_to_dict(r) for r in result.scalars().all()]
        except Exception as e:
            logger.error(f"Failed to get trashed folders: {e}")
            return []

    async def get_folders(
        self,
        scope_type: Optional[str],
        scope_id: str,
        include_trashed: bool = False,
    ) -> List[Dict[str, Any]]:
        # PR-E Phase 1: scope_type accepted but unused (scope_id is unique).
        try:
            async with read_scope() as session:
                stmt = select(Folders).where(Folders.scope_id == self._bigint(scope_id))
                if not include_trashed:
                    stmt = stmt.where(Folders.is_trashed.is_(False))
                stmt = stmt.order_by(Folders.sort_order.asc())
                result = await session.execute(stmt)
                return [_folder_row_to_dict(r) for r in result.scalars().all()]
        except Exception as e:
            logger.error(f"Failed to get folders: {e}")
            return []

    async def get_folder_by_id(self, folder_id: str) -> Optional[Dict[str, Any]]:
        try:
            async with read_scope() as session:
                result = await session.execute(
                    select(Folders)
                    .where(Folders.id == self._bigint(folder_id))
                    .limit(1)
                )
                row = result.scalars().first()
                return _folder_row_to_dict(row) if row else None
        except Exception as e:
            logger.error(f"Failed to get folder {folder_id}: {e}")
            return None

    async def update_folder(
        self, folder_id: str, data: Dict[str, Any]
    ) -> Dict[str, Any]:
        try:
            async with write_scope() as session:
                result = await session.execute(
                    update(Folders)
                    .where(Folders.id == self._bigint(folder_id))
                    .values(**data)
                    .returning(*Folders.__table__.columns)
                )
                row = result.mappings().first()
                updated = _mappings_dict(row) if row else {}
            logger.info(f"Updated folder {folder_id}")
            return updated
        except Exception as e:
            logger.error(f"Failed to update folder {folder_id}: {e}")
            raise

    async def delete_folder(self, folder_id: str) -> bool:
        try:
            async with write_scope() as session:
                await session.execute(
                    sa_delete(Folders).where(Folders.id == self._bigint(folder_id))
                )
            logger.info(f"Deleted folder {folder_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to delete folder {folder_id}: {e}")
            raise

    async def get_descendant_folder_ids(
        self, folder_id: str, *, include_trashed: bool = False
    ) -> List[str]:
        """Recursive descent into children (excluding ``folder_id`` itself).
        Single CTE.

        By default only NON-trashed descendants (the folder-tree UI contract).
        Pass ``include_trashed=True`` for a permanent purge
        (``permanent_delete_folder``), which must reach EVERY descendant — the
        trashed sub-folders and the subtrees hanging beneath them — so the CTE
        recurses through trashed folders instead of stopping at them.

        Returns list[str] for legacy contract (some unmigrated paths pass it
        back to PostgREST); internal callers like ``count_folder_contents``
        re-coerce via ``_bigint_list``."""
        # Helper-controlled literal fragments (no user input) — safe to splice.
        base_filter = "" if include_trashed else " AND is_trashed = false"
        rec_filter = "" if include_trashed else " WHERE f.is_trashed = false"
        try:
            async with read_scope() as session:
                result = await session.execute(
                    text(
                        "WITH RECURSIVE descendants AS ("
                        "  SELECT id FROM folders "
                        "    WHERE parent_id = :fid" + base_filter + " "
                        "  UNION ALL "
                        "  SELECT f.id FROM folders f "
                        "    INNER JOIN descendants d ON f.parent_id = d.id"
                        + rec_filter
                        + ") SELECT id FROM descendants"
                    ),
                    {"fid": self._bigint(folder_id)},
                )
                return [str(r["id"]) for r in result.mappings().all()]
        except Exception as e:
            logger.error(f"Failed to get descendant folders for {folder_id}: {e}")
            return []

    async def count_folder_contents(self, folder_ids: List[str]) -> Dict[str, int]:
        """Resource + sub-folder count for a list of folder ids. Sub-folder
        count mirrors the legacy quirk: ``len(ids) - 1`` (the list is
        ``[root] + descendants``; the -1 strips the root)."""
        try:
            async with read_scope() as session:
                resource_count = await session.scalar(
                    text(
                        "SELECT count(*) FROM resource_items "
                        "WHERE folder_id = ANY(:fids)"
                    ),
                    {"fids": self._bigint_list(folder_ids)},
                )
            subfolder_count = len(folder_ids) - 1 if len(folder_ids) > 1 else 0
            return {
                "resource_count": int(resource_count or 0),
                "subfolder_count": subfolder_count,
            }
        except Exception as e:
            logger.error(f"Failed to count folder contents: {e}")
            return {"resource_count": 0, "subfolder_count": 0}

    async def restore_folder_cascade(self, folder_id: str) -> Dict[str, int]:
        """Restore a folder + all trashed descendants + their resources, in
        ONE transaction (write_scope commits once). The multi-statement
        cascade is atomic — any raise rolls back all of it.

        Phase C task 3: the ``resources`` UPDATE below is migrated off raw
        ``text()`` SQL to ORM. Bulk Core UPDATE on ``Resources`` (a
        UserScoped model) is forbidden under a real user ``Scope``
        (``app/db/scope.py::_forbid_scoped_bulk_dml``) — must run as SYSTEM.
        No per-owner filter here BY DESIGN: folder membership (via
        ``resource_items.folder_id``), not ``creator_id``, is the
        authorization boundary for a folder cascade — a folder can hold
        resources from multiple contributors and restoring must not silently
        skip a co-contributor's resource. The ``folders`` UPDATE and the
        WITH RECURSIVE subtree lookup above stay raw ``text()`` (out of this
        task's scope — ``folders`` carries no scope mixin)."""
        try:
            async with write_scope() as session:
                folder_ids_rows = await session.execute(
                    text(
                        "WITH RECURSIVE subtree AS ("
                        "  SELECT id FROM folders "
                        "    WHERE id = :fid AND is_trashed = true "
                        "  UNION ALL "
                        "  SELECT f.id FROM folders f "
                        "    INNER JOIN subtree s ON f.parent_id = s.id "
                        "    WHERE f.is_trashed = true"
                        ") SELECT id FROM subtree"
                    ),
                    {"fid": self._bigint(folder_id)},
                )
                all_ids = [r["id"] for r in folder_ids_rows.mappings().all()]
                if not all_ids:
                    return {"restored_folders": 0, "restored_resources": 0}

                scope_cm = (
                    system_request_scope(
                        reason="restore-folder-cascade: bulk resources "
                        "UPDATE, no per-owner filter — folder membership is "
                        "the authorization boundary, not creator_id"
                    )
                    if is_enforced("resources")
                    else nullcontext()
                )
                async with scope_cm:
                    restored_resources_rows = await session.execute(
                        update(Resources)
                        .where(Resources.is_trashed.is_(True))
                        .where(
                            Resources.id.in_(
                                select(ResourceItems.resource_id).where(
                                    ResourceItems.folder_id.in_(all_ids)
                                )
                            )
                        )
                        .values(is_trashed=False, trashed_at=None)
                        .returning(Resources.id)
                    )
                    restored_resources = len(restored_resources_rows.all())

                restored_folders_rows = await session.execute(
                    text(
                        "UPDATE folders SET is_trashed = false, "
                        "       trashed_at = NULL "
                        "WHERE id = ANY(:ids) RETURNING id"
                    ),
                    {"ids": all_ids},
                )
                restored_folders = len(restored_folders_rows.mappings().all())

            logger.info(
                f"Cascade-restored folder {folder_id}: "
                f"{restored_folders} folders, "
                f"{restored_resources} resources"
            )
            return {
                "restored_folders": restored_folders,
                "restored_resources": restored_resources,
            }
        except Exception as e:
            logger.error(f"Failed to cascade-restore folder {folder_id}: {e}")
            raise

    async def trash_folder_cascade(self, folder_id: str) -> Dict[str, int]:
        """Trash a folder + all non-trashed descendants + their resources, in
        ONE transaction (write_scope commits once; atomic). The resource
        UPDATE snapshots the original location (last_folder_id / last_library_id
        / last_scope_id) per legacy contract — needed for restore.

        PR-E 4c dropped ``resource_items.scope_type``; the legacy supabase-py
        impl no longer reads/snapshots it (restore uses last_scope_id + folder/
        library). We match that — the retired asyncpg impl still referenced
        ``i.scope_type`` and would 42703 against the current schema.

        Phase C task 3: the ``resources`` UPDATE below is migrated off raw
        ``text()`` SQL (an ``UPDATE ... FROM``) to ORM — referencing
        ``ResourceItems`` columns in ``.where()``/``.values()`` makes
        SQLAlchemy render the same native Postgres ``UPDATE ... FROM``
        shape. Same bulk-DML-on-scoped-model rationale as
        ``restore_folder_cascade``: forbidden under a real user Scope, must
        run as SYSTEM; no per-owner filter by design (folder membership,
        not creator_id, is the authorization boundary)."""
        try:
            async with write_scope() as session:
                folder_ids_rows = await session.execute(
                    text(
                        "WITH RECURSIVE subtree AS ("
                        "  SELECT id FROM folders WHERE id = :fid "
                        "  UNION ALL "
                        "  SELECT f.id FROM folders f "
                        "    INNER JOIN subtree s ON f.parent_id = s.id "
                        "    WHERE f.is_trashed = false"
                        ") SELECT id FROM subtree"
                    ),
                    {"fid": self._bigint(folder_id)},
                )
                all_ids = [r["id"] for r in folder_ids_rows.mappings().all()]
                if not all_ids:
                    return {"trashed_folders": 0, "trashed_resources": 0}

                scope_cm = (
                    system_request_scope(
                        reason="trash-folder-cascade: bulk resources "
                        "UPDATE, no per-owner filter — folder membership is "
                        "the authorization boundary, not creator_id"
                    )
                    if is_enforced("resources")
                    else nullcontext()
                )
                async with scope_cm:
                    trashed_resources_rows = await session.execute(
                        update(Resources)
                        .where(Resources.id == ResourceItems.resource_id)
                        .where(ResourceItems.folder_id.in_(all_ids))
                        .where(Resources.is_trashed.is_(False))
                        .values(
                            is_trashed=True,
                            trashed_at=func.now(),
                            last_folder_id=ResourceItems.folder_id,
                            last_library_id=ResourceItems.library_id,
                            last_scope_id=ResourceItems.scope_id,
                        )
                        .returning(Resources.id)
                    )
                    trashed_resources = len(trashed_resources_rows.all())

                trashed_folders_rows = await session.execute(
                    text(
                        "UPDATE folders SET "
                        "  is_trashed = true, trashed_at = now() "
                        "WHERE id = ANY(:ids) RETURNING id"
                    ),
                    {"ids": all_ids},
                )
                trashed_folders = len(trashed_folders_rows.mappings().all())

            logger.info(
                f"Cascade-trashed folder {folder_id}: "
                f"{trashed_folders} folders, "
                f"{trashed_resources} resources"
            )
            return {
                "trashed_folders": trashed_folders,
                "trashed_resources": trashed_resources,
            }
        except Exception as e:
            logger.error(f"Failed to cascade-trash folder {folder_id}: {e}")
            raise

    # ══════════════════════════════════════════════════════════════════
    # PORTED REST STRAGGLERS — now on the ORM session scopes
    # ══════════════════════════════════════════════════════════════════
    #
    # Everything below used to run the legacy supabase-py REST bodies in prod
    # via MRO (inherited by the old ORM subclass), bypassing the tenant-scope
    # choke point. Ported to ``read_scope()`` / ``write_scope()`` so the choke
    # point governs them (the ``SCOPE_ENFORCE_RESOURCES`` prerequisite).
    # Strategy-C value parity with the retired REST bodies is preserved via the
    # ``_rest_parity`` / ``_plain`` funnels. See the module docstring.

    # ── Hash-based batch duplicate lookup ────────────────────────────

    async def find_by_hashes(
        self, file_hashes: list[str], creator_id: str
    ) -> dict[str, dict]:
        """Find non-trashed resources matching any of the given hashes for a creator.

        Issues a single ``file_hash IN (...)`` query and returns a mapping of
        ``{file_hash: first_matching_row}``.  Never raises — returns ``{}`` on
        any error.  Caller is responsible for keeping ``file_hashes`` ≤ 100.

        Reads the full ORM entity (the proven-injectable read shape — the choke
        point can attach the tenant predicate) then projects down to the exact
        column set the legacy PostgREST body selected (``_FIND_BY_HASH_COLS``)
        so the ``existing`` payload the caller echoes back stays byte-identical
        (id → int, created_at → ISO str via ``_rest_parity``).
        """
        if not file_hashes:
            return {}
        try:
            async with read_scope() as session:
                result = await session.execute(
                    select(Resources)
                    .where(Resources.file_hash.in_(file_hashes))
                    .where(Resources.creator_id == creator_id)
                    .where(Resources.is_trashed.is_(False))
                )
                rows = [
                    {k: full[k] for k in _FIND_BY_HASH_COLS}
                    for full in (
                        _resources_row_to_dict(obj) for obj in result.scalars().all()
                    )
                ]
            # Build hash→first-row map (first row per hash wins).
            mapping: dict[str, dict] = {}
            for row in rows:
                h = row.get("file_hash")
                if h and h not in mapping:
                    mapping[h] = row
            return mapping
        except Exception as e:
            logger.error("Failed to find resources by hashes: {}", e)
            return {}

    # ── Resource Tags ────────────────────────────────────────────────

    async def add_resource_tag(
        self, resource_id: str, tag_id: str, tagged_by: str
    ) -> Dict[str, Any]:
        """INSERT a resource_tags junction row, COMMITTING via write_scope.

        ``resource_tags`` is NOT a scope-mixin model, so a Core ``insert().
        returning()`` is permitted by the choke point (``_forbid_scoped_bulk_
        dml`` no-ops when no scoped model is touched) — same shape as
        ``create_folder``. RETURNING the full row reproduces the legacy REST
        ``result.data[0]`` shape (server defaults ``created_at`` / ``source``
        included; ``tagged_by`` uuid → str via ``_mappings_dict``)."""
        try:
            async with write_scope() as session:
                # Idempotent attach: the (resource_id, tag_id) PK means a repeat
                # attach (double-fired toggle, stale client state) raised a
                # duplicate-key error → 500 (prod 2026-07-19). ON CONFLICT DO
                # NOTHING + re-select returns the existing row instead — "make
                # sure this tag is on this resource" is naturally idempotent.
                result = await session.execute(
                    pg_insert(ResourceTags)
                    .values(
                        resource_id=self._bigint(resource_id),
                        tag_id=self._bigint(tag_id),
                        tagged_by=tagged_by,
                    )
                    .on_conflict_do_nothing(index_elements=["resource_id", "tag_id"])
                    .returning(*ResourceTags.__table__.columns)
                )
                row = result.mappings().first()
                if row is None:
                    row = (
                        (
                            await session.execute(
                                select(ResourceTags.__table__)
                                .where(
                                    ResourceTags.resource_id
                                    == self._bigint(resource_id)
                                )
                                .where(ResourceTags.tag_id == self._bigint(tag_id))
                                .limit(1)
                            )
                        )
                        .mappings()
                        .first()
                    )
                created = _mappings_dict(row) if row else {}
            logger.info(f"Tagged resource {resource_id} with tag {tag_id}")
            return created
        except Exception as e:
            logger.error(f"Failed to tag resource {resource_id}: {e}")
            raise

    async def remove_resource_tag(self, resource_id: str, tag_id: str) -> bool:
        """DELETE a resource_tags junction row via load-then-delete.

        Composite PK (resource_id, tag_id) → ``session.get`` by PK dict, then
        ``session.delete(instance)`` (the sanctioned governed write path). A
        missing row is a no-op that still returns ``True`` — the legacy REST
        ``.delete().eq().eq()`` also succeeded with zero matched rows, keeping
        the idempotent boolean contract."""
        try:
            async with write_scope() as session:
                obj = await session.get(
                    ResourceTags,
                    {
                        "resource_id": self._bigint(resource_id),
                        "tag_id": self._bigint(tag_id),
                    },
                )
                if obj is not None:
                    await session.delete(obj)
                    await session.flush()
            logger.info(f"Removed tag {tag_id} from resource {resource_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to remove tag from resource {resource_id}: {e}")
            raise

    async def get_resource_tags(self, resource_id: str) -> List[Dict[str, Any]]:
        """All resource_tags rows for a resource, each with its embedded ``tag``.

        Reproduces the legacy PostgREST ``select("*, tag:tags(*)")`` embed: a
        list of resource_tags dicts, each carrying a nested ``tag`` key = the
        full tags row. The ``tag_id`` FK is a non-null composite PK with ON
        DELETE CASCADE, so no orphan junction rows can exist — an INNER JOIN is
        equivalent to the REST left-embed here (the ``tag`` is always present).
        """
        try:
            async with read_scope() as session:
                result = await session.execute(
                    select(ResourceTags, Tags)
                    .join(Tags, ResourceTags.tag_id == Tags.id)
                    .where(ResourceTags.resource_id == self._bigint(resource_id))
                )
                out: List[Dict[str, Any]] = []
                for rt_obj, tag_obj in result.all():
                    row = _resource_tag_row_to_dict(rt_obj)
                    row["tag"] = _tag_row_to_dict(tag_obj)
                    out.append(row)
                return out
        except Exception as e:
            logger.error(f"Failed to get tags for resource {resource_id}: {e}")
            return []

    # ── Smart Folders (legacy REST) ─────────────────────────────────

    async def get_smart_folders(
        self, scope_type: Optional[str], scope_id: str
    ) -> List[Dict[str, Any]]:
        """Get all smart folders for a scope.

        PR-E Phase 1: scope_type accepted but unused (scope_id is unique).
        Smart folders ARE ``folders`` rows with ``is_smart = true``; ordered by
        ``sort_order`` ascending to match the legacy REST body.
        """
        try:
            async with read_scope() as session:
                result = await session.execute(
                    select(Folders)
                    .where(Folders.scope_id == self._bigint(scope_id))
                    .where(Folders.is_smart.is_(True))
                    .where(Folders.is_trashed.is_(False))
                    .order_by(Folders.sort_order.asc())
                )
                return [_folder_row_to_dict(r) for r in result.scalars().all()]
        except Exception as e:
            logger.error(f"Failed to get smart folders: {e}")
            return []

    async def create_smart_folder(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Create a folder with is_smart=true, COMMITTING via write_scope.

        ``folders`` is NOT a scope-mixin model, so a Core ``insert().returning()``
        is permitted (same shape as ``create_folder``) — RETURNING the full row
        reproduces the legacy REST ``result.data[0]`` shape with every server
        default (snowflake ``id`` / ``created_at`` / ``sort_order`` / ``is_system``
        / ``visibility`` / ``is_trashed``) materialized."""
        try:
            async with write_scope() as session:
                result = await session.execute(
                    insert(Folders).values(**data).returning(*Folders.__table__.columns)
                )
                row = result.mappings().first()
                created = _mappings_dict(row) if row else {}
            logger.info(f"Created smart folder: {data.get('name')}")
            return created
        except Exception as e:
            logger.error(f"Failed to create smart folder: {e}")
            raise

    async def execute_smart_rules(
        self, scope_type: Optional[str], scope_id: str, rules: Dict[str, Any]
    ) -> List[Dict[str, Any]]:
        """Execute smart folder rules against resource_items + resources (ORM).

        Ports the legacy PostgREST query builder to SQLAlchemy:
        - resource-table fields → predicates on the ``Resources`` model, spliced
          into the WHERE (AND mode) or OR-combined (OR mode);
        - tags → post-filtered against ``resource_tags`` (``_filter_by_tags``);
        - relative dates → resolved to absolute values by ``_resolve_value``;
        - match / exclude → the exclude branch subtracts the matched set from
          all (non-trashed, in-scope) items.

        The base shape is an INNER JOIN ``resource_items ⋈ resources`` — the
        exact equivalent of the REST ``resource:resources!inner(*)`` embed and a
        choke-point-injectable read shape (the tenant predicate on ``resources``
        reaches a filtering position). Ordering is on ``resource_items.created_at``
        DESC, matching the REST root-table ``.order("created_at")``. Each row is
        flattened to a ``resource_items`` dict with the full ``resources`` row
        under a nested ``resource`` key (Strategy-C parity via the row helpers).
        """
        try:
            conditions = rules.get("conditions", [])
            operator = rules.get("operator", "AND")
            match = rules.get("match", True)

            # Separate tag conditions from resource conditions.
            tag_conditions = [c for c in conditions if c["field"] == "tags"]
            resource_conditions = [c for c in conditions if c["field"] != "tags"]

            async with read_scope() as session:
                base = (
                    select(ResourceItems, Resources)
                    .join(Resources, ResourceItems.resource_id == Resources.id)
                    .where(ResourceItems.scope_id == self._bigint(scope_id))
                    .where(Resources.is_trashed.is_(False))
                )

                stmt = base
                if operator == "AND":
                    for cond in resource_conditions:
                        expr = self._condition_to_sql_expr(cond)
                        if expr is not None:
                            stmt = stmt.where(expr)
                else:
                    or_exprs = [
                        expr
                        for cond in resource_conditions
                        if (expr := self._condition_to_sql_expr(cond)) is not None
                    ]
                    if or_exprs:
                        stmt = stmt.where(or_(*or_exprs))

                stmt = stmt.order_by(ResourceItems.created_at.desc())
                result = await session.execute(stmt)
                items = [
                    self._smart_item_dict(item_obj, res_obj)
                    for item_obj, res_obj in result.all()
                ]

                # Post-filter for tag conditions (tags live in resource_tags).
                if tag_conditions:
                    items = await self._filter_by_tags(
                        items, tag_conditions, operator, session
                    )

                # Match / exclude logic.
                if not match:
                    all_result = await session.execute(
                        base.order_by(ResourceItems.created_at.desc())
                    )
                    all_items = [
                        self._smart_item_dict(item_obj, res_obj)
                        for item_obj, res_obj in all_result.all()
                    ]
                    matched_ids = {item["id"] for item in items}
                    items = [
                        item for item in all_items if item["id"] not in matched_ids
                    ]

            return items
        except Exception as e:
            logger.error(f"Failed to execute smart rules: {e}")
            return []

    @staticmethod
    def _smart_item_dict(item_obj: Any, resource_obj: Any) -> Dict[str, Any]:
        """Flatten a ``(resource_items, resources)`` join row to the legacy embed
        shape: the resource_items dict with the full resources row nested under a
        ``resource`` key (both Strategy-C value-parity coerced)."""
        row = _resource_item_row_to_dict(item_obj)
        row["resource"] = _resources_row_to_dict(resource_obj)
        return row

    @staticmethod
    def _coerce_smart_value(col: Any, value: Any) -> Any:
        """Coerce a resolved rule value to the target column's Python type.

        PostgREST relied on Postgres casting an ``unknown`` string literal to the
        column type; asyncpg's codec is strict and needs the typed Python value.
        So an int column gets ``int(value)`` and a timestamptz column gets
        ``datetime.fromisoformat(value)`` (the ISO string ``_resolve_value``
        produces for relative dates parses straight back to the identical tz-aware
        instant). Text columns and non-str values pass through unchanged. A value
        that fails to parse is left as-is so the downstream query raises exactly
        as the REST body's malformed filter would (→ swallowed to ``[]``).

        The int / datetime coercion set is closed over the field types actually
        reachable via the smart-rule schema (filename / file_type / file_size_bytes
        / created_at / duration_seconds / resolution / source_type / mime_type):
        no bool / UUID / date-only Resources column is reachable, so only int and
        timestamptz need special-casing; every other type is text-like and passes
        through. Extend this if a new coercible field is ever added to the schema."""
        if not isinstance(value, str):
            return value
        try:
            pytype = col.type.python_type
        except Exception:  # noqa: BLE001 - unknown/computed type → no coercion
            return value
        if pytype is int:
            try:
                return int(value)
            except ValueError:
                return value
        if pytype is datetime:
            try:
                return datetime.fromisoformat(value)
            except ValueError:
                return value
        return value

    def _condition_to_sql_expr(self, cond: Dict[str, Any]) -> Optional[Any]:
        """Translate a single resource-field condition to a SQLAlchemy predicate
        on the ``Resources`` model.

        Supported operators (1:1 with the retired ``_apply_condition`` /
        ``_condition_to_postgrest`` PostgREST translations):
          eq → ``col == v`` · contains → ``col ILIKE %v%`` · starts_with →
          ``col ILIKE v%`` · gt/lt/gte/lte → ``col > / < / >= / <= v`` · in →
          ``col IN (v.split(","))``. Any other op (e.g. ``not_contains``, which is
          a tags-only operator) → ``None`` = no predicate, exactly as the REST
          helpers returned the query unchanged / ``None``.

        The value goes through ``_resolve_value`` (relative-date magic) then
        ``_coerce_smart_value`` for the comparison operators (ILIKE stays a text
        match on the raw value). An unknown ``field`` raises ``AttributeError``
        here, caught by ``execute_smart_rules`` → ``[]`` — the same terminal
        outcome as PostgREST 400-ing on an unknown column."""
        field = cond["field"]
        op = cond["op"]
        value = self._resolve_value(cond["value"])
        col = getattr(Resources, _RESOURCES_NAME_TO_ATTR.get(field, field))

        if op == "eq":
            return col == self._coerce_smart_value(col, value)
        elif op == "contains":
            return col.ilike(f"%{value}%")
        elif op == "starts_with":
            return col.ilike(f"{value}%")
        elif op == "gt":
            return col > self._coerce_smart_value(col, value)
        elif op == "lt":
            return col < self._coerce_smart_value(col, value)
        elif op == "gte":
            return col >= self._coerce_smart_value(col, value)
        elif op == "lte":
            return col <= self._coerce_smart_value(col, value)
        elif op == "in":
            return col.in_([self._coerce_smart_value(col, v) for v in value.split(",")])
        return None

    def _resolve_value(self, value: str) -> str:
        """Resolve relative dates like 'relative:-7d' to absolute ISO dates."""
        if isinstance(value, str) and value.startswith("relative:"):
            offset_str = value.split(":")[1]
            # Parse -7d, -30d, -1h, etc.
            unit = offset_str[-1]
            amount = int(offset_str[:-1])
            now = datetime.now(timezone.utc)
            if unit == "d":
                target = now + timedelta(days=amount)
            elif unit == "h":
                target = now + timedelta(hours=amount)
            elif unit == "m":
                target = now + timedelta(minutes=amount)
            else:
                target = now + timedelta(days=amount)
            return target.isoformat()
        return value

    async def _filter_by_tags(
        self,
        items: List[Dict[str, Any]],
        tag_conditions: List[Dict[str, Any]],
        operator: str,
        session: Any,
    ) -> List[Dict[str, Any]]:
        """Post-filter items by tag conditions using the resource_tags table.

        Runs inside the caller's ``execute_smart_rules`` read session. Reads the
        tag NAMES for the candidate resources via an INNER JOIN
        ``resource_tags ⋈ tags`` (neither table is scope-governed), builds a
        ``resource_id → {lowercased tag names}`` map, and keeps each item whose
        resource satisfies the tag conditions — ``contains`` / ``not_contains``
        combined by the same AND/OR ``operator`` as the resource conditions
        (set-membership semantics identical to the retired REST body)."""
        if not items:
            return items

        # Candidate resource ids from the items (bigint ints from the row dicts).
        resource_ids = list(
            {item.get("resource_id") for item in items if item.get("resource_id")}
        )
        if not resource_ids:
            return []

        # Fetch all tag names for these resources.
        result = await session.execute(
            select(ResourceTags.resource_id, Tags.name)
            .join(Tags, ResourceTags.tag_id == Tags.id)
            .where(ResourceTags.resource_id.in_(resource_ids))
        )
        tag_data = result.mappings().all()

        # Build resource_id -> set of tag names.
        resource_tags: Dict[Any, set] = {}
        for row in tag_data:
            rid = row["resource_id"]
            tag_name = row["name"] or ""
            if rid not in resource_tags:
                resource_tags[rid] = set()
            resource_tags[rid].add(tag_name.lower())

        # Apply tag conditions
        def matches_tags(resource_id: str) -> bool:
            tags = resource_tags.get(resource_id, set())
            results = []
            for cond in tag_conditions:
                tag_value = cond["value"].lower()
                if cond["op"] == "contains":
                    results.append(tag_value in tags)
                elif cond["op"] == "not_contains":
                    results.append(tag_value not in tags)
                else:
                    results.append(False)
            if operator == "AND":
                return all(results)
            return any(results)

        return [item for item in items if matches_tags(item.get("resource_id", ""))]

    # ── @-reference picker ──────────────────────────────────────────

    async def list_accessible_for_user(
        self,
        *,
        user_id: str,
        q: str = "",
        kinds: list[str] | None = None,
        sources: list[str] | None = None,
        limit: int = 20,
        cursor: str | None = None,
        scope_team_id: str | None = None,
    ) -> list[dict]:
        """Resources the user can read: own personal + team-shared.

        Returns list of dicts with keys: id, name, mime, size, updated_at,
        scope_type, scope_id, thumbnail_path, cover_image_path, media_id,
        transcript_status, summary_status. Used by the @-reference picker;
        the last five are router-internal inputs (thumbnail decision + status
        badges), not response fields — see resources_search_router.

        Args:
            user_id: caller's auth id; used for both personal-owned and
                team-membership filtering.
            q: substring to ILIKE-match against ``filename`` (empty = no filter).
            kinds: optional list of canonical kinds — ``video``/``image``/
                ``doc``/``audio``/``pdf``. Unknown values are silently ignored
                (treated as wildcard), matching the picker UX intent.
            sources: optional list of ``resources.source_type`` values —
                ``upload``/``web``/``generated``/``derived`` (the four the DB
                CHECK admits since migration 363). None or empty = no filter.
                The ROUTER does the allowlisting; anything reaching here is
                used verbatim, so a caller passing an unknown value gets zero
                rows rather than a silent wildcard.
            limit: page size; capped at 50 (min 1).
            cursor: reserved for Phase 2 pagination — currently unused.
            scope_team_id: when provided, restricts results to this team
                (membership verified) plus the caller's own personal team.
                When None, returns resources across all the caller's teams.

        Phase A raw-SQL-to-ORM migration (docs/decisions/2026-08-04-raw-sql-
        to-orm-full-migration.md): was pure SQL via ``db_engine.fetch_all``.
        ``scope_team_id`` is a CALLER-SUPPLIED, unvalidated query-string value
        (``resources_search_router.search_resources`` has no int() coercion),
        so — matching the legacy ``team_id::text = :scope_team_id`` — it is
        compared as TEXT in ``_picker_visibility_filters`` (which this method
        shares with ``count_accessible_by_kind_for_user``): a non-numeric
        value must yield zero matches, not raise. Resources carries
        UserScoped(creator_id), but
        this access check is governed by TEAM membership, not creator_id (a
        resource shared to the caller's team must stay visible even if they
        didn't create it) — wrapped in an ``is_enforced``-gated
        ``system_request_scope`` (see this file's established
        ``count_resources_by_media_id`` pattern). ``SCOPE_ENFORCE_RESOURCES``
        DEFAULTS to false in code, but production sets it TRUE via
        ``secrets/backend.env`` (outside this repo tree — CLAUDE.md's
        部署陷阱 on env overriding config.yml). So this wrap is LOAD-BEARING
        in production, not a no-op: without it, the do_orm_execute choke
        point sees Resources touched with no ambient scope and fail-closed
        raises ``UnscopedQueryError`` on the very first call — the picker
        500s instead of silently mis-scoping to creator_id-only. The
        ``is_enforced`` gate exists only to stay byte-for-byte legacy where
        the flag genuinely is off (e.g. this repo's local/test default).
        """
        from sqlalchemy import String, case, cast

        capped_limit = min(max(int(limit), 1), 50)
        # Unknown kinds are dropped rather than matched: an all-unknown list
        # collapses to no filter (the documented wildcard), which is what the
        # old ``kinds_re = "."`` fallback did.
        kinds_list = [k for k in (kinds or []) if k in _PICKER_KINDS]

        id_text = cast(Resources.id, String)
        scope_id_text = cast(ResourceItems.scope_id, String)

        # PR-E 4c: scope_type column is being dropped; derive the personal/team
        # label from teams.kind (aliased as scope_type so the caller's response
        # shape is unchanged). After Spec 1 PR-C, ri.scope_id is always a
        # teams.id snowflake; personal scope is a single-member team containing
        # the user — which is why the membership predicate in
        # ``_picker_visibility_filters`` can be expressed purely over teams.
        stmt = (
            select(
                id_text.label("id"),
                Resources.filename.label("name"),
                Resources.mime_type.label("mime"),
                Resources.file_size_bytes.label("size"),
                Resources.updated_at,
                # Cover signals: the router turns these into a thumbnail_url
                # and does NOT emit them (the same ladder serve_resource_cover
                # walks — thumbnail_path > cover_image_path > parsed_media via
                # media_id > the original file for image/*). media_id in
                # particular must not reach the client: it is a Snowflake
                # BIGINT that JS silently rounds.
                Resources.thumbnail_path,
                Resources.cover_image_path,
                Resources.media_id,
                # AI processing state, so the picker can badge an unprocessed
                # video before the user attaches it (spec 2026-08-17 §1-F2).
                Resources.transcript_status,
                Resources.summary_status,
                scope_id_text.label("scope_id"),
                case((Teams.kind == "personal", "personal"), else_="team").label(
                    "scope_type"
                ),
            )
            .join(ResourceItems, ResourceItems.resource_id == Resources.id)
            .outerjoin(Teams, Teams.id == ResourceItems.scope_id)
            .where(
                *_picker_visibility_filters(
                    user_id=user_id, q=q, scope_team_id=scope_team_id
                )
            )
        )

        if kinds_list:
            stmt = stmt.where(_picker_kind_expr().in_(kinds_list))

        # The source chips ("Uploaded" / "Downloaded" / "Generated") on the
        # canvas Files shelf. Applied here AND in the counts aggregate below,
        # so a badge cannot describe a wider set than the grid shows.
        if sources:
            stmt = stmt.where(Resources.source_type.in_(sources))

        stmt = stmt.order_by(Resources.updated_at.desc()).limit(capped_limit)

        scope_cm = (
            system_request_scope(reason="resources-search-team-membership-access")
            if is_enforced("resources")
            else nullcontext()
        )
        async with scope_cm:
            async with read_scope() as session:
                rows = (await session.execute(stmt)).mappings().all()
        return [dict(r) for r in rows] or []

    async def count_accessible_by_kind_for_user(
        self,
        *,
        user_id: str,
        q: str = "",
        sources: list[str] | None = None,
        scope_team_id: str | None = None,
    ) -> dict[str, int]:
        """Per-kind totals for the @-reference picker's tab badges.

        Deliberately takes NO ``kinds`` and NO ``limit``. A tab badge answers
        "how many of these can I reach with the query I have typed", which is
        a property of ``q`` + scope alone. Deriving it from the returned page
        instead — what the router used to do — makes the badge report the
        page: opening the Video tab dropped the Image badge to 0, and the
        "All" badge could never say more than 50.

        Returns every key the response contract promises (``all`` plus the
        five kinds), zero-filled, so a kind with no rows still renders a "0"
        rather than disappearing.

        ``count(DISTINCT resources.id)``, not ``count(*)``: a resource with
        two ``resource_items`` rows (production has one such row today) is
        still ONE thing the user can reference.

        Args:
            user_id: caller's auth id (personal-owned + team-membership).
            q: substring to ILIKE-match against ``filename``.
            sources: optional ``resources.source_type`` allowlist — same values
                and same "None = no filter" contract as
                ``list_accessible_for_user``. Unlike ``kinds`` this one IS
                honoured here: it is a filter across every kind, so a badge
                that ignored it would count rows the grid is not showing.
            scope_team_id: as in ``list_accessible_for_user``.
        """
        kind_expr = _picker_kind_expr()
        stmt = (
            select(
                kind_expr.label("kind"),
                func.count(distinct(Resources.id)).label("n"),
            )
            .select_from(Resources)
            .join(ResourceItems, ResourceItems.resource_id == Resources.id)
            .where(
                *_picker_visibility_filters(
                    user_id=user_id, q=q, scope_team_id=scope_team_id
                )
            )
            .group_by(kind_expr)
        )

        if sources:
            stmt = stmt.where(Resources.source_type.in_(sources))

        scope_cm = (
            system_request_scope(reason="resources-search-team-membership-access")
            if is_enforced("resources")
            else nullcontext()
        )
        async with scope_cm:
            async with read_scope() as session:
                rows = (await session.execute(stmt)).mappings().all()

        counts: dict[str, int] = {"all": 0}
        counts.update({k: 0 for k in _PICKER_KINDS})
        for row in rows:
            n = int(row["n"])
            counts[str(row["kind"])] = n
            counts["all"] += n
        return counts

    # ── Temp-folder sweeper helpers ─────────────────────────────────

    async def list_resources_in_folder(
        self, folder_id: str, *, include_trashed: bool = False
    ) -> list[dict]:
        """Return resources whose resource_items row references this folder.

        ``folder_id`` lives on ``resource_items``, not ``resources``, so we
        INNER JOIN the two (the equivalent of the legacy ``resource:resources!
        inner(...)`` embed). The result is flattened to
        ``{"id": resource_id, "created_at": resources.created_at}`` so the temp
        sweeper can compute expiry without knowing the schema detail.

        ``created_at`` is returned as an ISO **string** (via ``_to_rest_value``)
        — the sweeper's ``_is_expired`` calls ``.replace(...)`` on it, so a
        native ``datetime`` would break it. Non-trashed rows only unless
        ``include_trashed``. Re-raises on error (the legacy contract — NOT a
        swallow-to-[])."""
        try:
            async with read_scope() as session:
                result = await session.execute(
                    select(
                        ResourceItems.resource_id,
                        Resources.created_at,
                        Resources.is_trashed,
                    )
                    .join(Resources, ResourceItems.resource_id == Resources.id)
                    .where(ResourceItems.folder_id == self._bigint(folder_id))
                )
                rows = result.mappings().all()
            out = []
            for row in rows:
                if not include_trashed and row["is_trashed"]:
                    continue
                out.append(
                    {
                        "id": row["resource_id"],
                        "created_at": _to_rest_value(row["created_at"]),
                    }
                )
            return out
        except Exception as e:
            logger.error(f"Failed to list resources in folder {folder_id}: {e}")
            raise

    async def soft_delete_resource(self, resource_id: str) -> None:
        """Mark a resource as trashed without removing the file on disk.

        ``resources`` IS a scope-mixin model, so the write goes through the
        sanctioned load-then-modify path (``session.get`` + attribute update),
        NOT a Core UPDATE (forbidden under a user scope). ``trashed_at`` is set
        as a tz-aware ``datetime`` (bound native, never isoformat). A missing id
        is a no-op — the legacy REST ``.update().eq("id", ...)`` also matched
        zero rows silently. The sweeper calls this under SYSTEM scope, so
        ``session.get`` loads any owner's row. File cleanup is handled by the
        existing trash-purge pipeline (``cleanup_trashed_resources_workflow``),
        not by this method. Re-raises on error (legacy contract)."""
        try:
            async with write_scope() as session:
                obj = await session.get(Resources, self._bigint(resource_id))
                if obj is not None:
                    obj.is_trashed = True
                    obj.trashed_at = datetime.now(timezone.utc)
                    await session.flush()
            logger.info(f"[temp_sweeper] soft-deleted resource {resource_id}")
        except Exception as e:
            logger.error(f"Failed to soft-delete resource {resource_id}: {e}")
            raise


# ─── Repository factory (post-rollout, ORM-only) ───────────────────────
#
# The per-domain ``USE_ORM_RESOURCES`` flag has been retired — prod runs 100%
# ORM. ``get_resources_repository()`` unconditionally returns the (now
# ORM-backed) ``ResourcesRepository``. Call sites that construct
# ``ResourcesRepository()`` directly get the same ORM implementation.


def get_resources_repository() -> ResourcesRepository:
    """Return the resources repository (ORM-backed, post-rollout)."""
    return ResourcesRepository()
