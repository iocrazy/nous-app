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
right: prefix-shaped keys (album / HLS / derived — each namespaced by an
id, never shared) are removed outright; single (content-addressed) keys are
only removed once a DB query confirms no other live row still points at the
same raw ``sb://`` value.
"""

from __future__ import annotations

from typing import Optional

from loguru import logger

from app.db import engine as db_engine
from app.services.library.media_storage import ObjectStore, resolve_media_source

# ── Reference check ──────────────────────────────────────────────────────
#
# Same 9 index columns as ``app.workflows.storage_audit._COLLECT_SQL`` — keep
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


def _int_ids(values) -> list[int]:
    """Coerce an exclude id list to ints (bigint columns). Never silently
    drops a bad value — a non-numeric id raises, which the caller (
    ``delete_object_if_unreferenced``) treats as an uncertain reference check
    and skips the delete (never delete on an uncertain refcount — same
    posture as ``count_resources_by_media_id`` elsewhere in this service)."""
    return [int(str(v)) for v in values]


def _build_reference_query(raw_path: str, exclude: Optional[dict]) -> tuple[str, dict]:
    """Build the UNION ALL existence query for one raw ``sb://`` value.

    ``exclude`` lets the caller declare "these rows are being deleted right
    now (or already are) — don't count them as a reference": e.g.
    ``{"resources": [rid]}``, ``{"resource_versions": [vid]}``,
    ``{"parsed_media": [pmid]}`` — any combination. Only non-empty exclude
    lists add a ``<> ALL(...)`` clause; this repo's established convention
    (see resource_ref_resolver.py / project_stages_repository.py / others)
    is to never bind an empty array to ``ANY``/``ALL`` — the exclusion
    clause for a table is simply omitted when there is nothing to exclude.
    """
    exclude = exclude or {}
    pm_ids = _int_ids(exclude.get("parsed_media") or [])
    resource_ids = _int_ids(exclude.get("resources") or [])
    version_ids = _int_ids(exclude.get("resource_versions") or [])

    params: dict = {"raw_path": raw_path}
    parts: list[str] = []

    def add(
        table: str, column: str, pk_col: str, ids: list[int], param_key: str
    ) -> None:
        clause = f"SELECT 1 FROM {table} WHERE {column} = :raw_path"
        if ids:
            params[param_key] = ids
            clause += f" AND {pk_col} <> ALL(:{param_key})"
        parts.append(clause)

    for col in _PARSED_MEDIA_COLS:
        add("parsed_media", col, "id", pm_ids, "pm_ids")
    for col in _RESOURCES_COLS:
        add("resources", col, "id", resource_ids, "resource_ids")
    for col in _RESOURCE_VERSIONS_COLS:
        add("resource_versions", col, "id", version_ids, "version_ids")

    sql = " UNION ALL ".join(parts) + " LIMIT 1"
    return sql, params


async def _is_referenced(raw_path: str, exclude: Optional[dict]) -> bool:
    sql, params = _build_reference_query(raw_path, exclude)
    row = await db_engine.fetch_one(sql, params)
    return row is not None


# ── Public primitive ─────────────────────────────────────────────────────


async def delete_object_if_unreferenced(
    raw_path: str, *, exclude: Optional[dict] = None
) -> str:
    """Delete the object backing ``raw_path`` iff nothing else references it.

    Returns one of ``"deleted" | "kept_referenced" | "skipped_fs" | "noop"``:

      * ``"skipped_fs"`` — ``raw_path`` is not an ``sb://`` value; the caller
        owns the legacy filesystem delete for it (this module never touches
        the filesystem).
      * prefix-shaped ``sb://`` key (``loc.is_prefix`` — album / HLS / derived,
        each namespaced by an id and never shared) — removed outright via
        ``remove_prefix``, no reference query (nothing else can validly point
        into another resource's private prefix).
      * single (content-addressed) key — a reference query first. Another
        live row (besides ``exclude``) pointing at the same raw value →
        ``"kept_referenced"``, object left alone. No other reference →
        ``remove`` → ``"deleted"``.
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

    if loc.is_prefix:
        try:
            await store.remove_prefix(loc.key)
            return "deleted"
        except Exception as e:  # noqa: BLE001 — best-effort GC, never raise
            logger.warning(
                f"[object-gc] remove_prefix failed for {loc.bucket}/{loc.key}: {e!r}"
            )
            return "noop"

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
        await store.remove(loc.key)
        return "deleted"
    except Exception as e:  # noqa: BLE001 — best-effort GC, never raise
        logger.warning(f"[object-gc] remove failed for {loc.bucket}/{loc.key}: {e!r}")
        return "noop"
