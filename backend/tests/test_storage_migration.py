"""storage_migration_workflow tests (Task 4.1).

``_migrate_row`` is the safety-contract core — tested directly with a
synthetic ``ModuleConfig`` + a FakeStore (house style borrowed from
test_media_storage_unified.py): verify (get_size) BEFORE any DB mutation,
dry_run stops before mutation, DB UPDATE before local delete.

The workflow body is driven the same way test_upload_postprocess_workflow.py
drives upload_postprocess_workflow — ``inspect.unwrap`` past @DBOS.workflow,
db_engine.fetch_all/execute + get_task_manager patched so no real DB/DBOS
runtime is needed.

The real ``uploads`` / ``project_files`` ``update_row`` closures (current-
version parent-sync) are exercised directly against a recording fake
db_engine.execute — no ORM/DB needed since they only build parameterized
SQL text.
"""

from __future__ import annotations

import inspect
from unittest.mock import AsyncMock, MagicMock

import pytest

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


def _module_cfg(update_row=None, extract=None):
    return sm.ModuleConfig(
        name="test",
        select_sql="SELECT 1",
        extract=extract
        or (lambda row: sm.RowExtract(scope_id=1, mime="video/mp4", filename="a.mp4")),
        update_row=update_row or AsyncMock(return_value=None),
    )


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


# ── 6. scope filter reaches the SELECT params ───────────────────────────


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

    async def fake_fetch_all(sql, params=None):
        seen_params.update(params or {})
        return []

    monkeypatch.setattr(sm.db_engine, "fetch_all", fake_fetch_all)
    monkeypatch.setitem(sm._MODULES, "uploads", _module_cfg())

    result = await _body()(
        module="uploads", scope_id=42, limit=10, dry_run=False, delete_source=False
    )

    assert seen_params == {"scope_id": 42, "limit": 10}
    assert result["total"] == 0
    manager.complete.assert_awaited_once()


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

    async def fake_fetch_all(sql, params=None):
        return rows

    monkeypatch.setattr(sm.db_engine, "fetch_all", fake_fetch_all)
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
# Atomicity pin: child UPDATE + parent sync must be ONE statement (one
# db_engine.execute call = one implicit transaction — db_engine has no
# cross-statement transaction API). Two autocommit UPDATEs left a crash
# window that permanently orphaned the parent's file_path (replay excludes
# the already-sb child). The parent leg is gated by :sync_parent.


def _record_execute(monkeypatch) -> list[tuple[str, dict]]:
    calls: list[tuple[str, dict]] = []

    async def fake_execute(sql, params=None):
        calls.append((sql, params or {}))
        return 1

    monkeypatch.setattr(sm.db_engine, "execute", fake_execute)
    return calls


async def test_uploads_update_row_syncs_resources_on_current_version(monkeypatch):
    calls = _record_execute(monkeypatch)

    row = {"id": 10, "resource_id": 100, "version_number": 2, "current_version": 2}
    await sm._uploads_update_row(row, "sb://library/t1/ab/cd/x.mp4", "deadbeef")

    # ONE statement covering both tables — child + parent same transaction.
    assert len(calls) == 1
    sql, params = calls[0]
    assert "WITH" in sql
    assert "resource_versions" in sql
    assert "UPDATE resources" in sql
    assert params["id"] == 10
    assert params["sync_parent"] is True


async def test_uploads_update_row_skips_resources_when_not_current(monkeypatch):
    calls = _record_execute(monkeypatch)

    row = {"id": 10, "resource_id": 100, "version_number": 1, "current_version": 2}
    await sm._uploads_update_row(row, "sb://library/t1/ab/cd/x.mp4", "deadbeef")

    assert len(calls) == 1
    assert calls[0][1]["sync_parent"] is False  # parent leg gated off


async def test_project_files_update_row_syncs_on_current_version(monkeypatch):
    calls = _record_execute(monkeypatch)

    row = {"id": 20, "file_id": 200, "version_number": 3, "current_version": 3}
    await sm._project_files_update_row(row, "sb://library/t1/ab/cd/y.mp4", "cafebabe")

    assert len(calls) == 1
    sql, params = calls[0]
    assert "WITH" in sql
    assert "file_versions" in sql
    assert "UPDATE project_files" in sql
    assert params["id"] == 20
    assert params["sync_parent"] is True


async def test_project_files_update_row_skips_parent_when_not_current(monkeypatch):
    calls = _record_execute(monkeypatch)

    row = {"id": 20, "file_id": 200, "version_number": 1, "current_version": 3}
    await sm._project_files_update_row(row, "sb://library/t1/ab/cd/y.mp4", "cafebabe")

    assert len(calls) == 1
    assert calls[0][1]["sync_parent"] is False


# ─── SQL bind-compilation tripwire ───────────────────────────────────
#
# The first prod dispatch died with PostgresSyntaxError: SQLAlchemy text()
# mis-parses a bind param immediately followed by a `::` cast
# (`:scope_id::bigint`), leaking a bare `:` to Postgres. The unit tests
# mocked db_engine so nothing ever compiled the SQL. Compile every module
# statement against the real asyncpg dialect so a reintroduced param-cast
# can never reach prod again.


def _compilable(sql: str) -> str:
    from sqlalchemy import text
    from sqlalchemy.dialects import postgresql

    return str(text(sql).compile(dialect=postgresql.asyncpg.dialect()))


def test_module_select_sql_compiles_with_expected_binds():
    from app.workflows import storage_migration as sm

    for name, cfg in sm._MODULES.items():
        compiled = _compilable(cfg.select_sql)
        # asyncpg dialect renders binds as $n — a surviving bare `:word`
        # means text() failed to recognize a param (the prod failure shape).
        import re

        stray = re.findall(r"(?<!:):[a-z_]+", compiled)
        assert not stray, f"{name}: unparsed binds {stray} in\n{compiled}"


def test_update_sql_compiles_with_expected_binds():
    import re

    from app.workflows import storage_migration as sm

    for sql in (sm._UPLOADS_UPDATE_SQL, sm._PROJECT_FILES_UPDATE_SQL):
        compiled = _compilable(sql)
        stray = re.findall(r"(?<!:):[a-z_]+", compiled)
        assert not stray, f"unparsed binds {stray} in\n{compiled}"
