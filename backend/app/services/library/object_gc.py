# app/services/library/object_gc.py

"""Reference-safe object deletion for the content-addressed library store.

Spec: docs/superpowers/specs/2026-08-03-reference-safe-object-deletion-design.md

Content addressing + the global download cache mean the SAME ``sb://`` key
is routinely referenced by more than one row (991 groups of
``parsed_media.download_path`` <-> ``resources.file_path``, 967 groups of
``resource_versions.file_path`` <-> ``resources.file_path``, measured against
production 2026-08-03). Two existing call sites got this wrong in opposite
directions:

  * ``resources_service._delete_physical_files`` / ``delete_version`` never
    deleted the object at all for ``sb://`` rows (they joined the raw sb://
    string onto ``DOWNLOAD_PATH`` as if it were a filesystem path) — objects
    leaked forever.
  * ``media_router._delete_stored_file`` deleted unconditionally — removing a
    ``parsed_media`` row could destroy an object a ``resources`` row still
    served, an instant, silent data-loss bug.

``delete_object_if_unreferenced`` is the one place that gets both directions
right — but "prefix-shaped keys are never shared" is only true for TWO of
the three prefix namespaces. ``hls/{rid}/{vid}/`` and ``derived/{rid}/`` are
named purely from IDs the caller owns and are genuinely exclusive, so those
are removed outright with no reference query. ``album`` prefixes
(``t{scope}/album/{rid}/``) are NOT exclusive: production measurement
2026-08-03 found 82/82 (100%) of them co-referenced by a live
``parsed_media.download_path`` <-> ``resources.file_path`` pair — the ``rid``
segment is a ``resource_versions.id`` that gets written back into that row's
own ``file_path`` and copied into both columns (see ``MediaKeyBuilder.
album_prefix``'s docstring). So an album-shaped prefix goes through the exact
same reference query as a single content-addressed key before being wiped
via ``remove_prefix`` — the query doesn't need to change to catch this, it
already matches on the raw string; the earlier version of this module's bug
was short-circuiting on ``is_prefix`` before ever reaching the query.
"""

from __future__ import annotations

from contextlib import nullcontext
from typing import Optional

from loguru import logger
from sqlalchemy import select, union_all

from app.db.scope import is_enforced, system_request_scope
from app.db.session import read_scope
from app.models import (
    FileVersions,
    ParsedMedia,
    ProjectFiles,
    Resources,
    ResourceVersions,
)
from app.services.library.media_storage import ObjectStore, resolve_media_source

# ── Reference check ──────────────────────────────────────────────────────
#
# Same 11 index columns as ``app.workflows.storage_audit._COLLECT_ARMS`` — keep
# both lists in sync; a column added to one and not the other lets a route
# either leak objects (missing here) or delete a still-referenced one
# (missing there is less dangerous, but still a drift). The two modules
# cross-reference each other in these comments on purpose.
#
# Each entry: (table, column, exclude-key, pk-column-checked-against-exclude).
_PARSED_MEDIA_COLS = [
    "download_path",
    "cover_download_path",
    "music_download_path",
    "extract_audio_path",
]
_RESOURCES_COLS = ["thumbnail_path", "cover_image_path", "file_path"]
_RESOURCE_VERSIONS_COLS = ["hls_path", "file_path"]
# I3: projects_service.upload_file / upload_new_version write into the SAME
# library bucket with the SAME content-addressed scheme (scope_id resolves to
# the owning team's snowflake via _resolve_project_scope_id — identical
# derivation to a resource upload's scope), so a byte-identical upload to a
# project and to that team's resource library produces one object referenced
# from two different tables. 0 actual collisions found in a full-schema scan
# 2026-08-03, but nothing prevents one as unified storage adoption grows.
_PROJECT_FILES_COLS = ["file_path"]
_FILE_VERSIONS_COLS = ["file_path"]


def _int_ids(values) -> list[int]:
    """Coerce an exclude id list to ints (bigint columns). Never silently
    drops a bad value — a non-numeric id raises, which the caller (
    ``delete_object_if_unreferenced``) treats as an uncertain reference check
    and skips the delete (never delete on an uncertain refcount — same
    posture as ``count_resources_by_media_id`` elsewhere in this service)."""
    return [int(str(v)) for v in values]


def _build_reference_query(raw_path: str, exclude: Optional[dict]):
    """Build the UNION ALL existence query for one raw ``sb://`` value.

    ``exclude`` lets the caller declare "these rows are being deleted right
    now (or already are) — don't count them as a reference": e.g.
    ``{"resources": [rid]}``, ``{"resource_versions": [vid]}``,
    ``{"parsed_media": [pmid]}`` — any combination. Only non-empty exclude
    lists add a ``.notin_(...)`` clause; this repo's established convention
    (see resource_ref_resolver.py / project_stages_repository.py / others)
    is to never bind an empty array to ``ANY``/``ALL`` — the exclusion
    clause for a table is simply omitted when there is nothing to exclude.

    Phase C task 2: migrated off the hand-built UNION ALL SQL string to a
    real ORM ``union_all()`` of per-column ``select(pk)`` statements — each
    arm projects the owning model's PK column (not a bare ``literal(1)``) so
    Resources/ResourceVersions land in the columns clause the choke point's
    positive compile-check probes, matching the "project a scoped column"
    shape ``app/db/scope.py`` recommends for an injectable read.
    """
    exclude = exclude or {}
    pm_ids = _int_ids(exclude.get("parsed_media") or [])
    resource_ids = _int_ids(exclude.get("resources") or [])
    version_ids = _int_ids(exclude.get("resource_versions") or [])
    project_file_ids = _int_ids(exclude.get("project_files") or [])
    file_version_ids = _int_ids(exclude.get("file_versions") or [])

    selects = []

    def add(pk_col, column_attr, ids: list[int]) -> None:
        stmt = select(pk_col).where(column_attr == raw_path)
        if ids:
            stmt = stmt.where(pk_col.notin_(ids))
        selects.append(stmt)

    for col in _PARSED_MEDIA_COLS:
        add(ParsedMedia.id, getattr(ParsedMedia, col), pm_ids)
    for col in _RESOURCES_COLS:
        add(Resources.id, getattr(Resources, col), resource_ids)
    for col in _RESOURCE_VERSIONS_COLS:
        add(ResourceVersions.id, getattr(ResourceVersions, col), version_ids)
    for col in _PROJECT_FILES_COLS:
        add(ProjectFiles.id, getattr(ProjectFiles, col), project_file_ids)
    for col in _FILE_VERSIONS_COLS:
        add(FileVersions.id, getattr(FileVersions, col), file_version_ids)

    return union_all(*selects).limit(1)


async def _is_referenced(raw_path: str, exclude: Optional[dict]) -> bool:
    """Whether ANY row (across every user/tenant) still references ``raw_path``.

    C1 / the module docstring: this MUST be a cross-tenant check — deleting
    the last user-visible reference to a content-addressed object must not
    let a USER-scoped read hide another user's reference to the SAME object
    (that would delete a still-referenced object, the exact data-loss bug
    this module exists to prevent). Wrapped in ``system_request_scope`` —
    unconditionally correct regardless of the caller's ambient scope — gated
    on ``is_enforced("resources")`` to stay byte-for-byte legacy (no wrap,
    no audit log) while enforcement is off, same convention as
    ``resources_repository.count_resources_by_media_id``'s
    ``media-refcount-gc`` wrap.
    """
    stmt = _build_reference_query(raw_path, exclude)
    scope_cm = (
        system_request_scope(
            reason="object-gc-reference-check: cross-tenant by design — "
            "the last deletable reference to a content-addressed object "
            "must be visible regardless of which user is deleting"
        )
        if is_enforced("resources")
        else nullcontext()
    )
    async with scope_cm:
        async with read_scope() as session:
            row = (await session.execute(stmt)).first()
    return row is not None


# ── Prefix namespace classification ──────────────────────────────────────
#
# C1: only these two prefix namespaces are genuinely exclusive (named purely
# from IDs the owning row controls, never shared). ``album`` prefixes
# (``t{scope}/album/{rid}/``) deliberately share the content-addressed
# ``t{scope}/`` root and are NOT in this set — see the module docstring for
# the production measurement (82/82 album prefixes co-referenced).
_EXCLUSIVE_PREFIX_NAMESPACES = ("hls/", "derived/")


def _is_exclusive_prefix(key: str) -> bool:
    """True iff ``key`` belongs to a prefix namespace that can never be
    co-referenced by another row (hls/{rid}/{vid}/, derived/{rid}/). Any
    other prefix shape (today: album) must go through the same reference
    check as a single content-addressed key before being wiped."""
    return key.startswith(_EXCLUSIVE_PREFIX_NAMESPACES)


# ── Public primitive ─────────────────────────────────────────────────────


async def delete_object_if_unreferenced(
    raw_path: str, *, exclude: Optional[dict] = None
) -> str:
    """Delete the object backing ``raw_path`` iff nothing else references it.

    Returns one of ``"deleted" | "kept_referenced" | "skipped_fs" | "noop"``:

      * ``"skipped_fs"`` — ``raw_path`` is not an ``sb://`` value; the caller
        owns the legacy filesystem delete for it (this module never touches
        the filesystem).
      * EXCLUSIVE prefix-shaped ``sb://`` key (``hls/{rid}/{vid}/`` or
        ``derived/{rid}/`` — namespaced purely by IDs the caller owns, never
        shared) — removed outright via ``remove_prefix``, no reference query
        (nothing else can validly point into another resource's private
        prefix).
      * Any OTHER shape — a single content-addressed key, OR a
        non-exclusive prefix (today: ``album``, which production data shows
        is routinely co-referenced — see module docstring) — a reference
        query runs FIRST. Another live row (besides ``exclude``) pointing at
        the same raw value → ``"kept_referenced"``, object left alone. No
        other reference → removed (``remove`` for a plain key, ``
        remove_prefix`` for an album prefix) → ``"deleted"``.
      * Any storage-call failure, or a failure to even determine references
        (DB error) is caught and only logged (never raised — deletion is a
        best-effort cleanup and this must not turn into a 500 for the
        record-delete endpoints that call it); returns ``"noop"``. Never
        deleting on an uncertain reference check is deliberate — the same
        posture ``count_resources_by_media_id`` callers already take
        elsewhere in this service (never fabricate "0 references").
    """
    if not raw_path:
        return "noop"

    loc = resolve_media_source(raw_path)
    if not loc.is_object_store:
        return "skipped_fs"

    store = ObjectStore(loc.bucket)

    if loc.is_prefix and _is_exclusive_prefix(loc.key):
        try:
            await store.remove_prefix(loc.key)
            return "deleted"
        except Exception as e:  # noqa: BLE001 — best-effort GC, never raise
            logger.warning(
                f"[object-gc] remove_prefix failed for {loc.bucket}/{loc.key}: {e!r}"
            )
            return "noop"

    # Single content-addressed key, or a non-exclusive prefix (album) — both
    # can legitimately be co-referenced by another row via the same raw sb://
    # string, so both go through the reference check before anything is
    # removed (C1 — never short-circuit on ``is_prefix`` alone).
    try:
        referenced = await _is_referenced(raw_path, exclude)
    except Exception as e:  # noqa: BLE001 — uncertain refcount, never delete
        logger.warning(
            f"[object-gc] reference check failed for {raw_path!r}, "
            f"skipping delete (never delete on an uncertain refcount): {e!r}"
        )
        return "noop"

    if referenced:
        return "kept_referenced"

    try:
        if loc.is_prefix:
            await store.remove_prefix(loc.key)
        else:
            await store.remove(loc.key)
        return "deleted"
    except Exception as e:  # noqa: BLE001 — best-effort GC, never raise
        logger.warning(f"[object-gc] remove failed for {loc.bucket}/{loc.key}: {e!r}")
        return "noop"
