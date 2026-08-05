# tests/test_object_gc.py

"""object_gc.delete_object_if_unreferenced — reference-safe sb:// deletion.

Spec: docs/superpowers/specs/2026-08-03-reference-safe-object-deletion-design.md

Phase C task 2: ``_build_reference_query``/``_is_referenced`` were migrated
off hand-built ``(sql, params)`` tuples + ``db_engine.fetch_one`` to a real
SQLAlchemy ``union_all()`` executed through ``app.db.session.read_scope()``.
Most tests here fake ``object_gc.read_scope`` (see ``_FakeExecuteResult`` /
``_FakeScopeSession`` / ``_fake_read_scope`` below); ``ObjectStore`` is still
mocked with ``AsyncMock``, no real network.

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

Plus a SECURITY-CRITICAL real-aiosqlite end-to-end test (no mocking of the
DB layer at all): two DIFFERENT users' ``resources`` rows pointing at the
SAME content-addressed ``sb://`` key must both count as live references,
even when an ambient per-request ``Scope`` for only ONE of those users is
open around the call — this is the exact cross-tenant reference-check
guarantee the module's docstring promises (``_is_referenced`` wraps
``system_request_scope()`` specifically so the check is "unconditionally
correct regardless of the caller's ambient scope").
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.services.library import object_gc

# ── Fake read_scope() infra (Phase C task 1 pattern, see
# tests/test_ai_transcription_sql.py) ───────────────────────────────────


class _FakeExecuteResult:
    """Stand-in for the awaited ``session.execute(stmt)`` Result — only
    ``.first()`` is exercised (mirrors ``_is_referenced``'s own
    ``(await session.execute(stmt)).first()`` call, no ``.mappings()``)."""

    def __init__(self, row):
        self._row = row

    def first(self):
        return self._row


class _FakeScopeSession:
    def __init__(self, row, calls: list):
        self._row = row
        self._calls = calls

    async def execute(self, stmt):
        self._calls.append(stmt)
        return _FakeExecuteResult(self._row)


def _fake_read_scope(row, calls: list):
    """Factory for a zero-arg ``read_scope()`` replacement yielding a fresh
    session per call; ``calls`` (list, mutated in place) records every
    statement passed to ``execute()`` so tests can assert "queried exactly
    once" without needing an ``AsyncMock``."""

    @asynccontextmanager
    async def _read_scope():
        yield _FakeScopeSession(row, calls)

    return _read_scope


class _UnreachableReadScope:
    """``read_scope()`` replacement whose very entry raises — proves a code
    path never reaches the DB at all (stand-in for the old
    ``fetch_one = AsyncMock(side_effect=AssertionError(...))`` pattern,
    updated for the ORM seam)."""

    async def __aenter__(self):
        raise AssertionError("read_scope must not be reached for this path")

    async def __aexit__(self, *exc):
        return False


def _unreachable_read_scope():
    return _UnreachableReadScope()


def _raising_execute_read_scope(exc: Exception):
    """``read_scope()`` replacement whose session's ``execute()`` raises —
    for the "reference check itself fails" regression."""

    class _BoomSession:
        async def execute(self, stmt):
            raise exc

    @asynccontextmanager
    async def _read_scope():
        yield _BoomSession()

    return _read_scope


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
    other) -> remove_prefix called, and the reference query (read_scope) must
    NOT be touched at all. These two namespaces are named purely from IDs the
    caller owns and are genuinely never shared — unlike album prefixes, see
    test_album_prefix_* below (C1)."""
    remove_prefix = AsyncMock(return_value=3)
    _fake_store(monkeypatch, remove_prefix=remove_prefix)
    monkeypatch.setattr(object_gc, "read_scope", _unreachable_read_scope)

    outcome = await object_gc.delete_object_if_unreferenced("sb://library/derived/42/")

    assert outcome == "deleted"
    remove_prefix.assert_awaited_once_with("derived/42/")


@pytest.mark.asyncio
async def test_exclusive_hls_prefix_removed_without_reference_query(monkeypatch):
    """hls/{rid}/{vid}/ is the other exclusive namespace — same treatment."""
    remove_prefix = AsyncMock(return_value=5)
    _fake_store(monkeypatch, remove_prefix=remove_prefix)
    monkeypatch.setattr(object_gc, "read_scope", _unreachable_read_scope)

    outcome = await object_gc.delete_object_if_unreferenced("sb://library/hls/9/3/")

    assert outcome == "deleted"
    remove_prefix.assert_awaited_once_with("hls/9/3/")


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
    calls: list = []
    monkeypatch.setattr(object_gc, "read_scope", _fake_read_scope((1,), calls))

    outcome = await object_gc.delete_object_if_unreferenced("sb://library/t5/album/42/")

    assert outcome == "kept_referenced"
    remove_prefix.assert_not_awaited()
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_album_prefix_without_reference_is_removed(monkeypatch):
    """No other row references the album prefix -> reference query runs,
    finds nothing, THEN remove_prefix is called (not remove — it's still a
    prefix, just not an exclusive-namespace one)."""
    remove_prefix = AsyncMock(return_value=2)
    _fake_store(monkeypatch, remove_prefix=remove_prefix)
    calls: list = []
    monkeypatch.setattr(object_gc, "read_scope", _fake_read_scope(None, calls))

    outcome = await object_gc.delete_object_if_unreferenced("sb://library/t5/album/99/")

    assert outcome == "deleted"
    assert len(calls) == 1
    remove_prefix.assert_awaited_once_with("t5/album/99/")


@pytest.mark.asyncio
async def test_single_object_with_other_reference_is_kept(monkeypatch):
    """Another live row still points at the same key -> kept_referenced, and
    remove() must never be called (this is the 991-group over-deletion bug
    regression: resources.file_path <-> parsed_media.download_path)."""
    remove = AsyncMock()
    _fake_store(monkeypatch, remove=remove)
    calls: list = []
    monkeypatch.setattr(object_gc, "read_scope", _fake_read_scope((1,), calls))

    outcome = await object_gc.delete_object_if_unreferenced(
        "sb://library/t5/aa/bb/shared.mp4"
    )

    assert outcome == "kept_referenced"
    remove.assert_not_awaited()
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_single_object_with_no_reference_is_deleted(monkeypatch):
    """No other row references the key -> remove() called, deleted."""
    remove = AsyncMock()
    _fake_store(monkeypatch, remove=remove)
    calls: list = []
    monkeypatch.setattr(object_gc, "read_scope", _fake_read_scope(None, calls))

    outcome = await object_gc.delete_object_if_unreferenced(
        "sb://library/t5/aa/bb/solo.mp4"
    )

    assert outcome == "deleted"
    remove.assert_awaited_once_with("t5/aa/bb/solo.mp4")


@pytest.mark.asyncio
async def test_exclude_excludes_the_caller_own_row(monkeypatch):
    """exclude={"resources": [rid]} must reach the reference query as bound
    ORM parameters, not string-concatenated — and only the tables actually
    named in ``exclude`` get a ``NOT IN`` clause (the established repo
    convention: never bind an empty array to ANY/ALL — see
    resource_ref_resolver.py / project_stages_repository.py / others)."""
    remove = AsyncMock()
    _fake_store(monkeypatch, remove=remove)
    calls: list = []
    monkeypatch.setattr(object_gc, "read_scope", _fake_read_scope(None, calls))

    outcome = await object_gc.delete_object_if_unreferenced(
        "sb://library/t5/aa/bb/solo.mp4",
        exclude={"resources": [77], "parsed_media": [99]},
    )

    assert outcome == "deleted"
    assert len(calls) == 1

    # Compile-level check of the exact statement _is_referenced built: ids
    # are real bound parameters (never string-interpolated), and only the
    # two tables named in exclude carry a NOT IN clause.
    stmt = object_gc._build_reference_query(
        "sb://library/t5/aa/bb/solo.mp4",
        exclude={"resources": [77], "parsed_media": [99]},
    )
    compiled = stmt.compile()
    sql = str(compiled)
    params = dict(compiled.params)

    # Parameterized — the ids never get string-interpolated into the SQL text.
    assert "77" not in sql
    assert "99" not in sql

    # The 4 parsed_media arms exclude 99; the 3 resources arms exclude 77
    # (verified param names against the real compiled statement).
    assert params["id_1"] == [99]
    assert params["id_2"] == [99]
    assert params["id_3"] == [99]
    assert params["id_4"] == [99]
    assert params["id_5"] == [77]
    assert params["id_6"] == [77]
    assert params["id_7"] == [77]

    # No resource_versions/project_files/file_versions exclude was given ->
    # those arms carry no exclusion clause at all.
    assert "resource_versions.id NOT IN" not in sql
    assert "project_files.id NOT IN" not in sql
    assert "file_versions.id NOT IN" not in sql


@pytest.mark.asyncio
async def test_non_sb_scheme_is_skipped_fs(monkeypatch):
    """A legacy filesystem-shaped path never touches the object store or DB —
    the caller owns the filesystem delete for it."""
    remove = AsyncMock()
    remove_prefix = AsyncMock()
    _fake_store(monkeypatch, remove=remove, remove_prefix=remove_prefix)
    monkeypatch.setattr(object_gc, "read_scope", _unreachable_read_scope)

    outcome = await object_gc.delete_object_if_unreferenced(
        "teams/42/uploads/9/v1/file.mp4"
    )

    assert outcome == "skipped_fs"
    remove.assert_not_awaited()
    remove_prefix.assert_not_awaited()


@pytest.mark.asyncio
async def test_empty_raw_path_is_noop(monkeypatch):
    monkeypatch.setattr(object_gc, "read_scope", _unreachable_read_scope)

    assert await object_gc.delete_object_if_unreferenced("") == "noop"
    assert await object_gc.delete_object_if_unreferenced(None) == "noop"


@pytest.mark.asyncio
async def test_storage_remove_failure_is_warning_not_raised(monkeypatch):
    """remove() raising must not propagate — deletion is best-effort GC, not
    a reason to 500 the caller's delete endpoint. Return value reflects the
    failure (noop, not "deleted")."""
    remove = AsyncMock(side_effect=RuntimeError("storage-api unreachable"))
    _fake_store(monkeypatch, remove=remove)
    calls: list = []
    monkeypatch.setattr(object_gc, "read_scope", _fake_read_scope(None, calls))

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
    monkeypatch.setattr(
        object_gc,
        "read_scope",
        _raising_execute_read_scope(RuntimeError("db connection lost")),
    )

    outcome = await object_gc.delete_object_if_unreferenced(
        "sb://library/t5/aa/bb/solo.mp4"
    )

    assert outcome == "noop"
    remove.assert_not_awaited()


def test_reference_query_covers_all_eleven_index_columns():
    """Regression guard mirroring storage_audit's own _COLLECT_ARMS test: the
    reference query must cover the exact same 11 index columns, so a
    reference-safe delete never misses a live reference (which would cause
    over-deletion) and the audit + GC lists never drift apart."""
    stmt = object_gc._build_reference_query("sb://library/x", None)
    sql = " ".join(str(stmt.compile(compile_kwargs={"literal_binds": True})).split())

    assert "parsed_media.download_path = 'sb://library/x'" in sql
    assert "parsed_media.cover_download_path = 'sb://library/x'" in sql
    assert "parsed_media.music_download_path = 'sb://library/x'" in sql
    assert "parsed_media.extract_audio_path = 'sb://library/x'" in sql
    assert "resources.thumbnail_path = 'sb://library/x'" in sql
    assert "resources.cover_image_path = 'sb://library/x'" in sql
    assert "resources.file_path = 'sb://library/x'" in sql
    assert "resource_versions.hls_path = 'sb://library/x'" in sql
    assert "resource_versions.file_path = 'sb://library/x'" in sql
    # I3: project_files / file_versions share the same library bucket + same
    # content-addressed scheme as a resource upload (both resolve scope_id to
    # the owning team's snowflake) — a byte-identical upload to a project and
    # to that team's resource library can produce one object referenced from
    # two tables. 0 actual collisions found in a full-schema scan 2026-08-03,
    # but nothing prevents one as unified storage adoption grows.
    assert "project_files.file_path = 'sb://library/x'" in sql
    assert "file_versions.file_path = 'sb://library/x'" in sql


def test_exclusive_prefix_namespace_classifier():
    """Direct unit coverage of the namespace split C1 hinges on: hls/ and
    derived/ are exclusive; everything else (including album) is not."""
    assert object_gc._is_exclusive_prefix("hls/9/3/master.m3u8")
    assert object_gc._is_exclusive_prefix("derived/42/")
    assert not object_gc._is_exclusive_prefix("t5/album/42/")
    assert not object_gc._is_exclusive_prefix("t5/aa/bb/deadbeef.mp4")


# ── Cross-tenant reference safety — real aiosqlite e2e (no mocking) ─────
#
# The single most important regression in this batch: _is_referenced MUST
# see another user's live reference to the same content-addressed key, even
# when the calling code has an ambient per-request Scope open for a
# DIFFERENT (single) user. That guarantee lives entirely in _is_referenced's
# system_request_scope() wrap — this test proves the wrap is load-bearing by
# running the SAME query with and without it, against a real database.


def _sqlite_type_for(col) -> str:
    """Crude storage-only type mapping for a real model column's SQLAlchemy
    type — good enough for SQLite (dynamically typed). Mirrors
    tests/test_orm_c_task1_row_shape_e2e.py's helper of the same name."""
    tname = type(col.type).__name__
    if tname in ("Integer", "BigInteger", "SmallInteger", "Boolean"):
        return "INTEGER"
    if tname in ("Numeric", "Double", "Float"):
        return "REAL"
    if tname in ("DateTime", "Date"):
        return "TIMESTAMP"
    return "TEXT"


def _sqlite_ddl_for_table(sa_table) -> str:
    """Generate a permissive SQLite CREATE TABLE from a real model's
    ``Table`` object (no constraints/defaults — storage shape only). Mirrors
    tests/test_orm_c_task1_row_shape_e2e.py's helper of the same name."""
    cols = ", ".join(f'"{c.name}" {_sqlite_type_for(c)}' for c in sa_table.columns)
    return f"CREATE TABLE {sa_table.name} ({cols})"


_USER_A = uuid.UUID("11111111-1111-1111-1111-111111111111")
_USER_B = uuid.UUID("22222222-2222-2222-2222-222222222222")
_SHARED_PATH = "sb://library/t5/aa/bb/shared-dedup.mp4"


@asynccontextmanager
async def _real_engine_with_cross_user_rows():
    """Real ``sqlite+aiosqlite`` engine with the 5 index tables the
    reference query unions across, and TWO ``resources`` rows — one per
    user (A id=1001, B id=1002) — both pointing at the exact same
    content-addressed ``sb://`` key (the dedup scenario the module's
    docstring measured in production: 991/967 co-referenced groups)."""
    from app.models import (
        FileVersions,
        ParsedMedia,
        ProjectFiles,
        Resources,
        ResourceVersions,
    )

    engine = create_async_engine("sqlite+aiosqlite://")
    engine = engine.execution_options(schema_translate_map={"public": None})
    async with engine.begin() as conn:
        for table in (
            ParsedMedia.__table__,
            Resources.__table__,
            ResourceVersions.__table__,
            ProjectFiles.__table__,
            FileVersions.__table__,
        ):
            await conn.exec_driver_sql(_sqlite_ddl_for_table(table))

        await conn.execute(
            insert(Resources.__table__).values(
                id=1001,
                creator_id=_USER_A,
                source_type="upload",
                filename="a.mp4",
                current_version=1,
                is_trashed=False,
                file_path=_SHARED_PATH,
            )
        )
        await conn.execute(
            insert(Resources.__table__).values(
                id=1002,
                creator_id=_USER_B,
                source_type="upload",
                filename="b.mp4",
                current_version=1,
                is_trashed=False,
                file_path=_SHARED_PATH,
            )
        )
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_delete_object_kept_referenced_across_different_users(monkeypatch):
    """SECURITY-CRITICAL / real aiosqlite, no mocking of the DB layer:
    deleting user A's resource (excluding ONLY A's own row id) must return
    "kept_referenced" because user B's row still points at the identical
    ``sb://`` key — even with SCOPE_ENFORCE_RESOURCES on and an ambient
    per-request Scope open for user A alone (the realistic shape: the HTTP
    handler deleting A's resource already opened a user-scoped session for
    the request). If _is_referenced's system_request_scope() wrap were ever
    removed, the choke point would inject creator_id == A onto every
    resources-sourced arm of the UNION, silently filtering out B's row —
    proven by the negative control below, which runs the exact same
    statement WITHOUT that wrap and shows it comes back empty."""
    import app.db.scope as scope_module
    from app.db.scope import Scope, UnscopedQueryError, request_scope

    monkeypatch.setattr(scope_module.settings, "SCOPE_ENFORCE_RESOURCES", True)

    async with _real_engine_with_cross_user_rows() as engine:
        sessionmaker = async_sessionmaker(
            engine, class_=AsyncSession, expire_on_commit=False
        )

        @asynccontextmanager
        async def _fake_read_scope_real_db():
            async with sessionmaker() as session:
                yield session

        monkeypatch.setattr(object_gc, "read_scope", _fake_read_scope_real_db)

        # ── Negative control: prove the assertion is actually sensitive to
        # the bug class — the SAME statement, executed under the SAME
        # ambient user-A scope but WITHOUT object_gc's system_request_scope
        # wrap, must NOT find user B's row (it gets silently filtered out,
        # or the choke point fail-closed raises — either way, "kept_referenced"
        # is unreachable via this path).
        stmt = object_gc._build_reference_query(_SHARED_PATH, {"resources": [1001]})
        async with request_scope(Scope(user_id=_USER_A)):
            async with sessionmaker() as session:
                try:
                    row = (await session.execute(stmt)).first()
                except UnscopedQueryError:
                    row = None
                assert row is None, (
                    "negative control must NOT see user B's row without the "
                    "system_request_scope wrap — otherwise this test isn't "
                    "sensitive to the cross-tenant bug it exists to catch"
                )

        # ── Positive: the REAL delete_object_if_unreferenced, called under
        # the same ambient per-user Scope(user_id=A), must still see B's row
        # via its own internal system_request_scope() wrap.
        async with request_scope(Scope(user_id=_USER_A)):
            outcome = await object_gc.delete_object_if_unreferenced(
                _SHARED_PATH, exclude={"resources": [1001]}
            )

    assert outcome == "kept_referenced"
