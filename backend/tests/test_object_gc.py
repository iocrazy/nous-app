"""object_gc.delete_object_if_unreferenced — reference-safe sb:// deletion.

Spec: docs/superpowers/specs/2026-08-03-reference-safe-object-deletion-design.md

Pure unit tests: ObjectStore and db_engine are mocked, no real network/DB.
Covers every bullet in the spec's test list:
  * EXCLUSIVE prefix (hls/derived) -> remove_prefix called, no reference query
  * NON-exclusive prefix (album) -> reference query runs first, same as a
    single object (C1: production measurement found 82/82 album prefixes
    co-referenced by a live parsed_media/resources row pair)
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
async def test_exclusive_prefix_removed_without_reference_query(monkeypatch):
    """EXCLUSIVE prefix namespace (derived/{rid}/ here, hls/{rid}/{vid}/ is the
    other) -> remove_prefix called, and the reference query (db_engine.
    fetch_one) must NOT be touched at all. These two namespaces are named
    purely from IDs the caller owns and are genuinely never shared — unlike
    album prefixes, see test_album_prefix_* below (C1)."""
    remove_prefix = AsyncMock(return_value=3)
    _fake_store(monkeypatch, remove_prefix=remove_prefix)
    fetch_one = AsyncMock(side_effect=AssertionError("must not query for a prefix"))
    monkeypatch.setattr(object_gc.db_engine, "fetch_one", fetch_one)

    outcome = await object_gc.delete_object_if_unreferenced("sb://library/derived/42/")

    assert outcome == "deleted"
    remove_prefix.assert_awaited_once_with("derived/42/")
    fetch_one.assert_not_awaited()


@pytest.mark.asyncio
async def test_exclusive_hls_prefix_removed_without_reference_query(monkeypatch):
    """hls/{rid}/{vid}/ is the other exclusive namespace — same treatment."""
    remove_prefix = AsyncMock(return_value=5)
    _fake_store(monkeypatch, remove_prefix=remove_prefix)
    fetch_one = AsyncMock(side_effect=AssertionError("must not query for a prefix"))
    monkeypatch.setattr(object_gc.db_engine, "fetch_one", fetch_one)

    outcome = await object_gc.delete_object_if_unreferenced("sb://library/hls/9/3/")

    assert outcome == "deleted"
    remove_prefix.assert_awaited_once_with("hls/9/3/")
    fetch_one.assert_not_awaited()


@pytest.mark.asyncio
async def test_album_prefix_with_reference_is_kept(monkeypatch):
    """C1: an album prefix (t{scope}/album/{rid}/) is NOT namespace-exclusive
    — production measurement 2026-08-03 found 82/82 album prefixes
    co-referenced by a live parsed_media.download_path <-> resources.file_path
    pair (the rid segment is a resource_versions.id written back into that
    row's own file_path and copied into both columns). Another live row
    pointing at the same raw album prefix string -> kept_referenced,
    remove_prefix must NEVER be called. This is the exact regression the
    original (wrong) 'prefixes are always exclusive' invariant would miss."""
    remove_prefix = AsyncMock()
    _fake_store(monkeypatch, remove_prefix=remove_prefix)
    fetch_one = AsyncMock(return_value={"?column?": 1})  # a row was found
    monkeypatch.setattr(object_gc.db_engine, "fetch_one", fetch_one)

    outcome = await object_gc.delete_object_if_unreferenced("sb://library/t5/album/42/")

    assert outcome == "kept_referenced"
    remove_prefix.assert_not_awaited()
    fetch_one.assert_awaited_once()


@pytest.mark.asyncio
async def test_album_prefix_without_reference_is_removed(monkeypatch):
    """No other row references the album prefix -> reference query runs,
    finds nothing, THEN remove_prefix is called (not remove — it's still a
    prefix, just not an exclusive-namespace one)."""
    remove_prefix = AsyncMock(return_value=2)
    _fake_store(monkeypatch, remove_prefix=remove_prefix)
    fetch_one = AsyncMock(return_value=None)  # no reference found
    monkeypatch.setattr(object_gc.db_engine, "fetch_one", fetch_one)

    outcome = await object_gc.delete_object_if_unreferenced("sb://library/t5/album/99/")

    assert outcome == "deleted"
    fetch_one.assert_awaited_once()
    remove_prefix.assert_awaited_once_with("t5/album/99/")


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


def test_reference_query_covers_all_eleven_index_columns():
    """Regression guard mirroring storage_audit's own _COLLECT_SQL test: the
    reference query must cover the exact same 11 index columns, so a
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
    # I3: project_files / file_versions share the same library bucket + same
    # content-addressed scheme as a resource upload (both resolve scope_id to
    # the owning team's snowflake) — a byte-identical upload to a project and
    # to that team's resource library can produce one object referenced from
    # two tables. 0 actual collisions found in a full-schema scan 2026-08-03,
    # but nothing prevents one as unified storage adoption grows.
    assert "SELECT 1 FROM project_files WHERE file_path = :raw_path" in sql_flat
    assert "SELECT 1 FROM file_versions WHERE file_path = :raw_path" in sql_flat


def test_exclusive_prefix_namespace_classifier():
    """Direct unit coverage of the namespace split C1 hinges on: hls/ and
    derived/ are exclusive; everything else (including album) is not."""
    assert object_gc._is_exclusive_prefix("hls/9/3/master.m3u8")
    assert object_gc._is_exclusive_prefix("derived/42/")
    assert not object_gc._is_exclusive_prefix("t5/album/42/")
    assert not object_gc._is_exclusive_prefix("t5/aa/bb/deadbeef.mp4")
