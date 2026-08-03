"""object_gc.delete_object_if_unreferenced — reference-safe sb:// deletion.

Spec: docs/superpowers/specs/2026-08-03-reference-safe-object-deletion-design.md

Pure unit tests: ObjectStore and db_engine are mocked, no real network/DB.
Covers every bullet in the spec's test list:
  * prefix -> remove_prefix called, no reference query
  * single object with another live reference -> kept_referenced, no remove
  * single object with no reference -> remove called, deleted
  * exclude excludes the caller's own row from the reference query
  * non-sb:// -> skipped_fs, storage untouched
  * storage call failure -> warning only, never raises, reflected in retval
  * reference-check (DB) failure -> never deletes (uncertain refcount)
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app.services.library import object_gc


def _fake_store(monkeypatch, *, remove=None, remove_prefix=None):
    """Patch ``ObjectStore`` so the module-under-test's instantiation
    (``ObjectStore(loc.bucket)``) returns a stand-in with AsyncMock hooks."""
    store = AsyncMock()
    if remove is not None:
        store.remove = remove
    if remove_prefix is not None:
        store.remove_prefix = remove_prefix
    monkeypatch.setattr(object_gc, "ObjectStore", lambda bucket: store)
    return store


@pytest.mark.asyncio
async def test_prefix_removed_without_reference_query(monkeypatch):
    """Prefix-shaped key (album/HLS/derived) -> remove_prefix called, and the
    reference query (db_engine.fetch_one) must NOT be touched at all."""
    remove_prefix = AsyncMock(return_value=3)
    _fake_store(monkeypatch, remove_prefix=remove_prefix)
    fetch_one = AsyncMock(side_effect=AssertionError("must not query for a prefix"))
    monkeypatch.setattr(object_gc.db_engine, "fetch_one", fetch_one)

    outcome = await object_gc.delete_object_if_unreferenced("sb://library/derived/42/")

    assert outcome == "deleted"
    remove_prefix.assert_awaited_once_with("derived/42/")
    fetch_one.assert_not_awaited()


@pytest.mark.asyncio
async def test_single_object_with_other_reference_is_kept(monkeypatch):
    """Another live row still points at the same key -> kept_referenced, and
    remove() must never be called (this is the 991-group over-deletion bug
    regression: resources.file_path <-> parsed_media.download_path)."""
    remove = AsyncMock()
    _fake_store(monkeypatch, remove=remove)
    fetch_one = AsyncMock(return_value={"?column?": 1})  # a row was found
    monkeypatch.setattr(object_gc.db_engine, "fetch_one", fetch_one)

    outcome = await object_gc.delete_object_if_unreferenced(
        "sb://library/t5/aa/bb/shared.mp4"
    )

    assert outcome == "kept_referenced"
    remove.assert_not_awaited()
    fetch_one.assert_awaited_once()


@pytest.mark.asyncio
async def test_single_object_with_no_reference_is_deleted(monkeypatch):
    """No other row references the key -> remove() called, deleted."""
    remove = AsyncMock()
    _fake_store(monkeypatch, remove=remove)
    fetch_one = AsyncMock(return_value=None)  # no reference found
    monkeypatch.setattr(object_gc.db_engine, "fetch_one", fetch_one)

    outcome = await object_gc.delete_object_if_unreferenced(
        "sb://library/t5/aa/bb/solo.mp4"
    )

    assert outcome == "deleted"
    remove.assert_awaited_once_with("t5/aa/bb/solo.mp4")


@pytest.mark.asyncio
async def test_exclude_excludes_the_caller_own_row(monkeypatch):
    """exclude={"resources": [rid]} must reach the reference query as bound
    params, not string-concatenated — and the resulting SQL must reference
    the exclude ids param for the resources.file_path branch."""
    remove = AsyncMock()
    _fake_store(monkeypatch, remove=remove)
    fetch_one = AsyncMock(return_value=None)
    monkeypatch.setattr(object_gc.db_engine, "fetch_one", fetch_one)

    outcome = await object_gc.delete_object_if_unreferenced(
        "sb://library/t5/aa/bb/solo.mp4",
        exclude={"resources": [77], "parsed_media": [99]},
    )

    assert outcome == "deleted"
    fetch_one.assert_awaited_once()
    sql, params = fetch_one.await_args.args
    assert params["raw_path"] == "sb://library/t5/aa/bb/solo.mp4"
    assert params["resource_ids"] == [77]
    assert params["pm_ids"] == [99]
    # Parameterized — the ids never get string-interpolated into the SQL text.
    assert "77" not in sql
    assert "99" not in sql
    assert ":resource_ids" in sql
    assert ":pm_ids" in sql
    # No resource_versions exclude was given -> that clause carries no
    # exclusion (the established repo convention: never bind an empty array
    # to ANY/ALL — see resource_ref_resolver.py and friends).
    assert "version_ids" not in params


@pytest.mark.asyncio
async def test_non_sb_scheme_is_skipped_fs(monkeypatch):
    """A legacy filesystem-shaped path never touches the object store or DB —
    the caller owns the filesystem delete for it."""
    remove = AsyncMock()
    remove_prefix = AsyncMock()
    _fake_store(monkeypatch, remove=remove, remove_prefix=remove_prefix)
    fetch_one = AsyncMock(side_effect=AssertionError("must not query for FS path"))
    monkeypatch.setattr(object_gc.db_engine, "fetch_one", fetch_one)

    outcome = await object_gc.delete_object_if_unreferenced(
        "teams/42/uploads/9/v1/file.mp4"
    )

    assert outcome == "skipped_fs"
    remove.assert_not_awaited()
    remove_prefix.assert_not_awaited()
    fetch_one.assert_not_awaited()


@pytest.mark.asyncio
async def test_empty_raw_path_is_noop(monkeypatch):
    fetch_one = AsyncMock(side_effect=AssertionError("must not query for empty path"))
    monkeypatch.setattr(object_gc.db_engine, "fetch_one", fetch_one)

    assert await object_gc.delete_object_if_unreferenced("") == "noop"
    assert await object_gc.delete_object_if_unreferenced(None) == "noop"


@pytest.mark.asyncio
async def test_storage_remove_failure_is_warning_not_raised(monkeypatch):
    """remove() raising must not propagate — deletion is best-effort GC, not
    a reason to 500 the caller's delete endpoint. Return value reflects the
    failure (noop, not "deleted")."""
    remove = AsyncMock(side_effect=RuntimeError("storage-api unreachable"))
    _fake_store(monkeypatch, remove=remove)
    fetch_one = AsyncMock(return_value=None)
    monkeypatch.setattr(object_gc.db_engine, "fetch_one", fetch_one)

    outcome = await object_gc.delete_object_if_unreferenced(
        "sb://library/t5/aa/bb/solo.mp4"
    )

    assert outcome == "noop"
    remove.assert_awaited_once()


@pytest.mark.asyncio
async def test_storage_remove_prefix_failure_is_warning_not_raised(monkeypatch):
    remove_prefix = AsyncMock(side_effect=RuntimeError("storage-api unreachable"))
    _fake_store(monkeypatch, remove_prefix=remove_prefix)

    outcome = await object_gc.delete_object_if_unreferenced("sb://library/derived/42/")

    assert outcome == "noop"


@pytest.mark.asyncio
async def test_reference_check_failure_never_deletes(monkeypatch):
    """A DB error while checking references must be treated as an
    UNCERTAIN reference (never fabricate "no references" from a failed
    query) — same posture as count_resources_by_media_id elsewhere in this
    service. remove() must never be called."""
    remove = AsyncMock()
    _fake_store(monkeypatch, remove=remove)
    fetch_one = AsyncMock(side_effect=RuntimeError("db connection lost"))
    monkeypatch.setattr(object_gc.db_engine, "fetch_one", fetch_one)

    outcome = await object_gc.delete_object_if_unreferenced(
        "sb://library/t5/aa/bb/solo.mp4"
    )

    assert outcome == "noop"
    remove.assert_not_awaited()


def test_reference_query_covers_all_nine_index_columns():
    """Regression guard mirroring storage_audit's own _COLLECT_SQL test: the
    reference query must cover the exact same 9 index columns, so a
    reference-safe delete never misses a live reference (which would cause
    over-deletion) and the audit + GC lists never drift apart."""
    sql, _ = object_gc._build_reference_query("sb://library/x", None)
    sql_flat = " ".join(sql.split())

    assert "SELECT 1 FROM parsed_media WHERE download_path = :raw_path" in sql_flat
    assert (
        "SELECT 1 FROM parsed_media WHERE cover_download_path = :raw_path" in sql_flat
    )
    assert (
        "SELECT 1 FROM parsed_media WHERE music_download_path = :raw_path" in sql_flat
    )
    assert "SELECT 1 FROM parsed_media WHERE extract_audio_path = :raw_path" in sql_flat
    assert "SELECT 1 FROM resources WHERE thumbnail_path = :raw_path" in sql_flat
    assert "SELECT 1 FROM resources WHERE cover_image_path = :raw_path" in sql_flat
    assert "SELECT 1 FROM resources WHERE file_path = :raw_path" in sql_flat
    assert "SELECT 1 FROM resource_versions WHERE hls_path = :raw_path" in sql_flat
    assert "SELECT 1 FROM resource_versions WHERE file_path = :raw_path" in sql_flat
