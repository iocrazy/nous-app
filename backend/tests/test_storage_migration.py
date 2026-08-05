"""storage_migration_workflow tests (Task 4.1).

``_migrate_row`` is the safety-contract core — tested directly with a
synthetic ``ModuleConfig`` + a FakeStore (house style borrowed from
test_media_storage_unified.py): verify (get_size) BEFORE any DB mutation,
dry_run stops before mutation, DB UPDATE before local delete.

The workflow body is driven the same way test_upload_postprocess_workflow.py
drives upload_postprocess_workflow — ``inspect.unwrap`` past @DBOS.workflow,
``read_scope``/``write_scope`` (the ORM seam, Phase C task 2) + get_task_manager
patched so no real DB/DBOS runtime is needed.

The real ``uploads`` / ``project_files`` ``update_row`` closures (current-
version parent-sync) are exercised directly against a recording fake
``write_scope`` session — no real DB needed since we only inspect the compiled
ORM statements.
"""

from __future__ import annotations

import inspect
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import literal, select
from sqlalchemy.dialects import postgresql

from app.services.library import media_storage
from app.workflows import storage_migration as sm

pytestmark = pytest.mark.asyncio


# ── FakeStore (mirrors test_media_storage_unified.py's house style) ────


class FakeStore:
    bucket = "library"

    def __init__(self, *, size: int | None = None, already_exists: bool = False):
        self.puts: list[tuple[str, str, str]] = []
        self._exists = already_exists
        self._size = size

    async def exists(self, key: str) -> bool:
        return self._exists

    async def put_file(self, key: str, path: str, mime: str) -> None:
        self.puts.append((key, path, mime))

    async def get_size(self, key: str) -> int:
        return self._size


def _patch_store(monkeypatch, fake: FakeStore) -> None:
    monkeypatch.setattr(media_storage, "library_store", lambda: fake)


def _module_cfg(update_row=None, extract=None, select_stmt=None):
    return sm.ModuleConfig(
        name="test",
        select_stmt=select_stmt or (lambda scope_id, limit: select(literal(1))),
        extract=extract
        or (lambda row: sm.RowExtract(scope_id=1, mime="video/mp4", filename="a.mp4")),
        update_row=update_row or AsyncMock(return_value=None),
    )


# ── Fake ORM seams (house style borrowed from test_ai_transcription_sql.py) ──


class _FakeExecuteRowsResult:
    """Multi-row result — supports ``.mappings().all()``, mirroring the real
    ``session.execute(stmt)).mappings().all()`` the workflow body calls."""

    def __init__(self, rows):
        self._rows = rows

    def mappings(self):
        return self

    def all(self):
        return self._rows


class _FakeScopeSession:
    def __init__(self, execute_result=None):
        self._execute_result = execute_result

    async def execute(self, stmt):
        return self._execute_result


def _fake_read_scope(execute_result):
    @asynccontextmanager
    async def _read_scope():
        yield _FakeScopeSession(execute_result=execute_result)

    return _read_scope


class _CapturingWriteSession:
    """Records every statement passed to ``execute()`` for compile-level
    (column/param) assertions — stands in for ``write_scope()``."""

    def __init__(self):
        self.statements = []

    async def execute(self, stmt):
        self.statements.append(stmt)

        class _R:
            rowcount = 1

        return _R()


def _fake_write_scope(session):
    @asynccontextmanager
    async def _write_scope():
        yield session

    return _write_scope


def _compiled(stmt):
    """Compile an UPDATE statement to (sql_text, bound_params) — dialect
    agnostic (default/generic compiler is enough, no live DB needed)."""
    compiled = stmt.compile()
    return str(compiled), dict(compiled.params)


# ── 1. idempotent skip ──────────────────────────────────────────────────


async def test_migrate_row_skips_already_sb(monkeypatch):
    called = {"store": False}

    async def _boom(*a, **kw):
        called["store"] = True
        raise AssertionError("store_local_file must not be called for an sb:// row")

    monkeypatch.setattr(media_storage, "store_local_file", _boom)

    row = {"id": 1, "file_path": "sb://library/t1/ab/cd/x.mp4"}
    outcome = await sm._migrate_row(
        row, _module_cfg(), dry_run=False, delete_source=True
    )

    assert outcome == "skipped"
    assert called["store"] is False


async def test_migrate_row_missing_local_file(monkeypatch, tmp_path):
    from app.core.config import settings

    monkeypatch.setattr(settings, "DOWNLOAD_PATH", str(tmp_path))
    row = {"id": 1, "file_path": "teams/1/uploads/1/v1/gone.mp4"}
    outcome = await sm._migrate_row(
        row, _module_cfg(), dry_run=False, delete_source=True
    )
    assert outcome == "missing"


# ── 1b. containment: traversal file_path → row failed, nothing touched ──


async def test_migrate_row_traversal_path_raises_nothing_deleted(monkeypatch, tmp_path):
    from app.core.config import settings

    downloads = tmp_path / "downloads"
    downloads.mkdir()
    monkeypatch.setattr(settings, "DOWNLOAD_PATH", str(downloads))

    # A real file OUTSIDE DOWNLOAD_PATH that the poisoned row points at.
    victim = tmp_path / "secret.txt"
    victim.write_text("do not delete me")

    async def _boom(*a, **kw):  # store must never be reached
        raise AssertionError("store_local_file must not run for a traversal row")

    monkeypatch.setattr(media_storage, "store_local_file", _boom)
    update_row = AsyncMock(return_value=None)

    for poisoned in ("../secret.txt", "../../etc/passwd", "a/../../secret.txt"):
        with pytest.raises(RuntimeError, match="escapes DOWNLOAD_PATH"):
            await sm._migrate_row(
                {"id": 1, "file_path": poisoned},
                _module_cfg(update_row=update_row),
                dry_run=False,
                delete_source=True,
            )

    update_row.assert_not_awaited()
    assert victim.exists()  # nothing deleted


# ── 2. dry_run: PUT may happen, zero UPDATE, zero delete ────────────────


async def test_migrate_row_dry_run_no_update_no_delete(monkeypatch, tmp_path):
    from app.core.config import settings

    monkeypatch.setattr(settings, "DOWNLOAD_PATH", str(tmp_path))
    rel = "teams/1/uploads/1/v1/a.mp4"
    f = tmp_path / rel
    f.parent.mkdir(parents=True)
    f.write_bytes(b"x" * 10)

    fake = FakeStore(size=10, already_exists=False)
    _patch_store(monkeypatch, fake)
    update_row = AsyncMock(return_value=None)

    outcome = await sm._migrate_row(
        {"id": 1, "file_path": rel},
        _module_cfg(update_row=update_row),
        dry_run=True,
        delete_source=True,  # even with delete_source=True, dry_run must win
    )

    assert outcome == "dry_run_ok"
    assert len(fake.puts) == 1  # PUT happened (dedup-safe, harmless)
    update_row.assert_not_awaited()
    assert f.exists()  # never deleted


# ── 3. verify failure: size mismatch → raise, no UPDATE, no delete ─────


async def test_migrate_row_verify_mismatch_raises_no_mutation(monkeypatch, tmp_path):
    from app.core.config import settings

    monkeypatch.setattr(settings, "DOWNLOAD_PATH", str(tmp_path))
    rel = "teams/1/uploads/1/v1/a.mp4"
    f = tmp_path / rel
    f.parent.mkdir(parents=True)
    f.write_bytes(b"x" * 10)

    fake = FakeStore(size=999, already_exists=False)  # wrong size
    _patch_store(monkeypatch, fake)
    update_row = AsyncMock(return_value=None)

    with pytest.raises(RuntimeError, match="size mismatch"):
        await sm._migrate_row(
            {"id": 1, "file_path": rel},
            _module_cfg(update_row=update_row),
            dry_run=False,
            delete_source=True,
        )

    update_row.assert_not_awaited()
    assert f.exists()  # untouched


# ── 4/5. delete_source True vs False ────────────────────────────────────


async def test_migrate_row_delete_source_false_keeps_file(monkeypatch, tmp_path):
    from app.core.config import settings

    monkeypatch.setattr(settings, "DOWNLOAD_PATH", str(tmp_path))
    rel = "teams/1/uploads/1/v1/a.mp4"
    f = tmp_path / rel
    f.parent.mkdir(parents=True)
    f.write_bytes(b"x" * 10)

    fake = FakeStore(size=10, already_exists=False)
    _patch_store(monkeypatch, fake)
    update_row = AsyncMock(return_value=None)

    outcome = await sm._migrate_row(
        {"id": 1, "file_path": rel},
        _module_cfg(update_row=update_row),
        dry_run=False,
        delete_source=False,
    )

    assert outcome == "migrated"
    update_row.assert_awaited_once()
    assert f.exists()


async def test_migrate_row_delete_source_true_deletes_after_update(
    monkeypatch, tmp_path
):
    from app.core.config import settings

    monkeypatch.setattr(settings, "DOWNLOAD_PATH", str(tmp_path))
    rel = "teams/1/uploads/1/v1/a.mp4"
    f = tmp_path / rel
    f.parent.mkdir(parents=True)
    f.write_bytes(b"x" * 10)

    fake = FakeStore(size=10, already_exists=False)
    _patch_store(monkeypatch, fake)
    update_row = AsyncMock(return_value=None)

    outcome = await sm._migrate_row(
        {"id": 1, "file_path": rel},
        _module_cfg(update_row=update_row),
        dry_run=False,
        delete_source=True,
    )

    assert outcome == "migrated"
    update_row.assert_awaited_once()
    call_args = update_row.await_args.args
    assert call_args[0]["id"] == 1
    assert call_args[1].startswith("sb://library/")
    assert not f.exists()


# ── 6. scope filter reaches select_stmt ─────────────────────────────────


def _body():
    return inspect.unwrap(sm.storage_migration_workflow)


def _make_manager() -> MagicMock:
    mgr = MagicMock()
    mgr.create = AsyncMock(return_value="task-1")
    mgr.start = AsyncMock(return_value=None)
    mgr.update_progress = AsyncMock(return_value=None)
    mgr.complete = AsyncMock(return_value=None)
    mgr.fail = AsyncMock(return_value=None)
    mgr.patch_metadata = AsyncMock(return_value=None)
    return mgr


async def test_workflow_passes_scope_id_to_select(monkeypatch):
    manager = _make_manager()
    monkeypatch.setattr(
        "app.services.infra.unified_task_manager.get_task_manager",
        lambda: manager,
    )
    seen_params = {}

    def spy_select_stmt(scope_id, limit):
        seen_params["scope_id"] = scope_id
        seen_params["limit"] = limit
        return select(literal(1))

    monkeypatch.setattr(sm, "read_scope", _fake_read_scope(_FakeExecuteRowsResult([])))
    monkeypatch.setitem(
        sm._MODULES, "uploads", _module_cfg(select_stmt=spy_select_stmt)
    )

    result = await _body()(
        module="uploads", scope_id=42, limit=10, dry_run=False, delete_source=False
    )

    # scope_id/limit are now plain Python args passed straight to the
    # select_stmt callable — not SQL bind params — so the assertion is "was
    # the callable invoked with these args", not "were these in a params dict".
    assert seen_params == {"scope_id": 42, "limit": 10}
    assert result["total"] == 0
    manager.complete.assert_awaited_once()


async def test_workflow_scope_wrap_is_gated_by_is_enforced(monkeypatch):
    """Final review Finding 1: the workflow-body ``system_request_scope`` wrap
    must be gated on ``is_enforced("resources")`` like every other site in
    this batch — an accidentally-unconditional wrap means a future second
    scoped table would default to a silent SYSTEM view instead of a loud
    fail-closed raise while the flag is off. Verified via ``current_scope()``
    observed from inside ``select_stmt``, for both enforcement states."""
    import app.db.scope as scope_module
    from app.db.scope import SYSTEM, current_scope

    manager = _make_manager()
    monkeypatch.setattr(
        "app.services.infra.unified_task_manager.get_task_manager",
        lambda: manager,
    )

    seen_scope = {}

    def spy_select_stmt(scope_id, limit):
        seen_scope["value"] = current_scope()
        return select(literal(1))

    monkeypatch.setattr(sm, "read_scope", _fake_read_scope(_FakeExecuteRowsResult([])))
    monkeypatch.setitem(
        sm._MODULES, "uploads", _module_cfg(select_stmt=spy_select_stmt)
    )

    # enforcement OFF → nullcontext() → no ambient scope set by the wrap.
    monkeypatch.setattr(scope_module.settings, "SCOPE_ENFORCE_RESOURCES", False)
    await _body()(
        module="uploads", scope_id=None, limit=10, dry_run=False, delete_source=False
    )
    assert seen_scope["value"] is None

    # enforcement ON → real system_request_scope → SYSTEM ambient scope.
    monkeypatch.setattr(scope_module.settings, "SCOPE_ENFORCE_RESOURCES", True)
    await _body()(
        module="uploads", scope_id=None, limit=10, dry_run=False, delete_source=False
    )
    assert seen_scope["value"] is SYSTEM


# ── 7. one row failing doesn't abort the batch, but failed>0 raises ─────


async def test_workflow_row_failure_does_not_abort_but_batch_raises(
    monkeypatch, tmp_path
):
    from app.core.config import settings

    monkeypatch.setattr(settings, "DOWNLOAD_PATH", str(tmp_path))
    manager = _make_manager()
    monkeypatch.setattr(
        "app.services.infra.unified_task_manager.get_task_manager",
        lambda: manager,
    )

    ok_rel = "teams/1/uploads/1/v1/ok.mp4"
    bad_rel = "teams/1/uploads/2/v1/bad.mp4"
    for rel, size in ((ok_rel, 10), (bad_rel, 5)):
        f = tmp_path / rel
        f.parent.mkdir(parents=True)
        f.write_bytes(b"x" * size)

    fake = FakeStore(size=10, already_exists=False)  # matches ok row only
    _patch_store(monkeypatch, fake)

    rows = [
        {"id": 1, "file_path": ok_rel},
        {"id": 2, "file_path": bad_rel},
    ]

    monkeypatch.setattr(
        sm, "read_scope", _fake_read_scope(_FakeExecuteRowsResult(rows))
    )
    update_row = AsyncMock(return_value=None)
    monkeypatch.setitem(sm._MODULES, "uploads", _module_cfg(update_row=update_row))

    with pytest.raises(RuntimeError, match=r"1/2 row\(s\) failed"):
        await _body()(
            module="uploads",
            scope_id=None,
            limit=10,
            dry_run=False,
            delete_source=False,
        )

    # row 1 migrated despite row 2 failing later in the same batch.
    update_row.assert_awaited_once()
    manager.complete.assert_not_awaited()
    # Failed-run observability: partial counts landed in task metadata
    # BEFORE the raise (patch_metadata — unthrottled, metadata-only).
    manager.patch_metadata.assert_awaited_once()
    patched = manager.patch_metadata.await_args.args[1]
    assert patched["failed"] == 1
    assert patched["migrated"] == 1
    assert patched["total"] == 2


# ── 8. current_version parent-sync (real module update_row closures) ────
#
# Atomicity pin: child UPDATE + parent sync must land in ONE ``write_scope()``
# session (one transaction, one commit — write_scope's session.begin() owns
# it). Two separate transactions left a crash window that permanently
# orphaned the parent's file_path (replay excludes the already-sb child).
# The parent leg is gated by ``sync_parent`` — a plain Python branch now,
# not a SQL-side boolean flag.


async def test_uploads_update_row_syncs_resources_on_current_version(monkeypatch):
    session = _CapturingWriteSession()
    monkeypatch.setattr(sm, "write_scope", _fake_write_scope(session))

    row = {"id": 10, "resource_id": 100, "version_number": 2, "current_version": 2}
    await sm._uploads_update_row(row, "sb://library/t1/ab/cd/x.mp4", "deadbeef")

    # TWO statements — child update, then parent sync — same write_scope
    # session/transaction.
    assert len(session.statements) == 2
    child_stmt, parent_stmt = session.statements

    child_sql, child_params = _compiled(child_stmt)
    assert "resource_versions" in child_sql
    assert child_params["file_path"] == "sb://library/t1/ab/cd/x.mp4"
    assert child_params["file_hash"] == "deadbeef"
    assert child_params["id_1"] == 10

    parent_sql, parent_params = _compiled(parent_stmt)
    assert "UPDATE public.resources" in parent_sql or "resources" in parent_sql
    assert parent_params["file_path"] == "sb://library/t1/ab/cd/x.mp4"
    assert parent_params["file_hash"] == "deadbeef"
    assert parent_params["id_1"] == 100


async def test_uploads_update_row_skips_resources_when_not_current(monkeypatch):
    session = _CapturingWriteSession()
    monkeypatch.setattr(sm, "write_scope", _fake_write_scope(session))

    row = {"id": 10, "resource_id": 100, "version_number": 1, "current_version": 2}
    await sm._uploads_update_row(row, "sb://library/t1/ab/cd/x.mp4", "deadbeef")

    # Only the child update ran — the parent UPDATE never executes at all
    # (the new implementation guards it with a plain `if sync_parent:` in
    # Python, rather than a SQL-side boolean short-circuit).
    assert len(session.statements) == 1
    sql, _ = _compiled(session.statements[0])
    assert "resource_versions" in sql


async def test_project_files_update_row_syncs_on_current_version(monkeypatch):
    session = _CapturingWriteSession()
    monkeypatch.setattr(sm, "write_scope", _fake_write_scope(session))

    row = {"id": 20, "file_id": 200, "version_number": 3, "current_version": 3}
    await sm._project_files_update_row(row, "sb://library/t1/ab/cd/y.mp4", "cafebabe")

    assert len(session.statements) == 2
    child_stmt, parent_stmt = session.statements

    child_sql, child_params = _compiled(child_stmt)
    assert "file_versions" in child_sql
    assert child_params["file_path"] == "sb://library/t1/ab/cd/y.mp4"
    assert child_params["id_1"] == 20
    # project_files/file_versions have no file_hash column.
    assert "file_hash" not in child_params

    parent_sql, parent_params = _compiled(parent_stmt)
    assert "project_files" in parent_sql
    assert parent_params["file_path"] == "sb://library/t1/ab/cd/y.mp4"
    assert parent_params["id_1"] == 200


async def test_project_files_update_row_skips_parent_when_not_current(monkeypatch):
    session = _CapturingWriteSession()
    monkeypatch.setattr(sm, "write_scope", _fake_write_scope(session))

    row = {"id": 20, "file_id": 200, "version_number": 1, "current_version": 3}
    await sm._project_files_update_row(row, "sb://library/t1/ab/cd/y.mp4", "cafebabe")

    assert len(session.statements) == 1
    sql, _ = _compiled(session.statements[0])
    assert "file_versions" in sql


# ─── ORM statement compile tripwire ───────────────────────────────────
#
# The first prod dispatch (pre-ORM era) died with PostgresSyntaxError: raw
# SQL text() mis-parsed a bind param immediately followed by a `::` cast.
# That failure mode doesn't exist anymore now that every SELECT is a real
# SQLAlchemy ORM statement (SQLAlchemy owns bind rendering end to end) — but
# we still want a tripwire proving every registered module's select_stmt
# actually compiles against the real asyncpg dialect, so a future module
# addition can't silently ship an uncompilable statement.


def test_module_select_stmt_compiles_for_asyncpg_dialect():
    for name, cfg in sm._MODULES.items():
        if cfg.select_stmt is sm._no_select_stmt:
            # derived/pm_assets get their rows from list_rows, not
            # select_stmt — calling it is expected to raise loudly.
            with pytest.raises(RuntimeError):
                cfg.select_stmt(None, 10)
            continue
        stmt = cfg.select_stmt(None, 10)
        # Must compile cleanly against the real driver dialect used in prod.
        compiled = stmt.compile(dialect=postgresql.asyncpg.dialect())
        assert str(compiled)  # non-empty — smoke that compilation succeeded


def test_uploads_select_excludes_download_pipeline():
    """Spec decision (2026-07-12): ``uploads`` does NOT touch the download
    pipeline (source_type='web') — its files are shared/deduped on POSIX and
    album rows point at directories, which broke this module's single-object
    assumptions (70/880 rows IsADirectoryError on the first prod dry-run).
    source_type='web' is instead handled by the sibling ``downloads`` module,
    which branches on is_album — see test_storage_migration_downloads.py."""
    stmt = sm._uploads_select_stmt(None, 10)
    sql = str(stmt.compile(compile_kwargs={"literal_binds": True}))
    assert "'upload'" in sql
    assert "'generated'" in sql
    assert "'derived'" in sql
    assert "'web'" not in sql
