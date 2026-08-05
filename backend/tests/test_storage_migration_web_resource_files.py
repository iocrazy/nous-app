"""web_resource_files module：收 downloads module 漏掉的无-resource_versions 的
web 资源(qishui 音频等)—— ``resources.file_path`` 有值但没有对应
``resource_versions`` 行,``downloads`` module 靠 JOIN resource_versions 选行,
天然扫不到这 202 个孤儿行(见 task brief)。

行源是一条直接 SELECT resources(不 JOIN resource_versions,正是为了收漏的),
DB-列驱动、单文件、content-addressed(同 pm_assets 的写法,但没有 pm_assets
那种"一行扇出多列"的复杂度 —— 这里永远是 resources.file_path 一列)。

scope 来自 resource_items(LEFT JOIN LATERAL,同 uploads/downloads/pm_assets
的写法)—— 孤儿 resource(无 resource_items scope)拿到 scope_id=NULL,必须
skip 而不是 raise(一个孤儿不该拖垮整批)。

只 UPDATE resources.file_path —— 不碰 parsed_media.download_path,那一列由
后置 SQL 处理(不在本 module 职责内)。
"""

from __future__ import annotations

import hashlib
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from app.services.library import media_storage
from app.workflows import storage_migration as sm

# NB: asyncio_mode = "auto"(pyproject.toml)自动识别 async def 测试。


# ── Fake read_scope()/write_scope() session infrastructure ─────────────
# Same shape as tests/test_ai_transcription_sql.py's Phase C task 1 seam —
# patch is applied directly on ``sm`` (the module's own ``read_scope``/
# ``write_scope`` attribute), not ``app.db.session``, because
# ``storage_migration.py`` does ``from app.db.session import read_scope,
# write_scope`` — that binds a fresh name in ``sm``'s own namespace, so
# patching the source module's attribute after the fact would not be seen
# by code already referencing ``sm.read_scope``/``sm.write_scope``.


class _FakeExecuteRowsResult:
    def __init__(self, rows):
        self._rows = rows

    def mappings(self):
        return self

    def all(self):
        return self._rows


class _FakeReadScopeSession:
    def __init__(self, rows):
        self._rows = rows

    async def execute(self, stmt):
        return _FakeExecuteRowsResult(self._rows)


def _fake_read_scope(rows):
    @asynccontextmanager
    async def _read_scope():
        yield _FakeReadScopeSession(rows)

    return _read_scope


class _CapturingWriteSession:
    """Records every ``execute()`` call's statement for column/param-level
    assertions — stands in for ``write_scope()``."""

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


# ── FakeWebResourceFilesStore —— exists/put_file/get_size,支持预置"已存在"的 key ──


class FakeWebResourceFilesStore:
    bucket = "library"

    def __init__(self, *, size_offset: int = 0):
        self.put_file_calls: list[tuple[str, str, str]] = []
        self._sizes: dict[str, int] = {}
        # 模拟"上传后校验发现 size 不符"(round-trip 篡改)。
        self._size_offset = size_offset

    async def exists(self, key: str) -> bool:
        return key in self._sizes

    async def put_file(self, key: str, file_path: str, mime: str, *, upsert=True):
        self.put_file_calls.append((key, file_path, mime))
        self._sizes[key] = Path(file_path).stat().st_size + self._size_offset

    async def get_size(self, key: str) -> int:
        if key not in self._sizes:
            raise RuntimeError(f"no such object: {key}")
        return self._sizes[key]

    def seed_existing(self, key: str, size: int) -> None:
        self._sizes[key] = size


def _patch_store(monkeypatch, store: FakeWebResourceFilesStore) -> None:
    monkeypatch.setattr(media_storage, "library_store", lambda: store)


def _expected_key(scope_id: int, data: bytes, ext: str) -> str:
    sha = hashlib.sha256(data).hexdigest()
    return f"t{scope_id}/{sha[:2]}/{sha[2:4]}/{sha}{ext}"


# ── SELECT wiring — scope_id/limit forwarding ───────────────────────────


def _compiled(stmt) -> str:
    """Compile with literal binds so the WHERE clause values are visible in
    the SQL text — same technique test_storage_migration.py's ``_compilable``
    helper uses for its own ``select_stmt`` compile-fence tests."""
    from sqlalchemy.dialects import postgresql

    return str(
        stmt.compile(
            dialect=postgresql.asyncpg.dialect(), compile_kwargs={"literal_binds": True}
        )
    )


def test_web_resource_files_select_stmt_forwards_scope_id_and_limit():
    """``select_stmt`` is now a plain ``(scope_id, limit) -> Select`` Python
    callable, not a raw-SQL/params-dict pair forwarded through
    ``db_engine.fetch_all`` — so "forwarding" is now verified by compiling the
    statement built from a given ``(scope_id, limit)`` and checking both
    values actually landed in the WHERE/LIMIT clauses."""
    cfg = sm._MODULES["web_resource_files"]
    stmt = cfg.select_stmt(42, 10)
    sql = _compiled(stmt)
    assert "ri.scope_id = 42" in sql
    assert "LIMIT 10" in sql

    # scope_id=None must NOT add a scope filter at all (optional-filter
    # convention — see _web_resource_files_select_stmt's own docstring). The
    # lateral subquery's own SELECT/WHERE always mention ``ri.scope_id`` (its
    # projected column) — what must be absent is the outer equality filter.
    stmt_no_scope = cfg.select_stmt(None, 5)
    sql_no_scope = _compiled(stmt_no_scope)
    assert "ri.scope_id =" not in sql_no_scope
    assert "LIMIT 5" in sql_no_scope


def test_web_resource_files_select_stmt_excludes_sb_and_trashed_and_non_web():
    sql = _compiled(sm._web_resource_files_select_stmt(None, 10))
    assert "source_type" in sql and "'web'" in sql
    assert "NOT LIKE 'sb://%'" in sql
    assert "is_trashed" in sql


def test_web_resource_files_select_stmt_structurally_excludes_rows_with_versions():
    """与 downloads 的互斥必须是结构性的(NOT EXISTS resource_versions),不能只
    依赖"202 行凑巧全无 rv + downloads 已跑完"这种数据状态假设 —— 否则一个
    "有 resource_versions 但 downloads 还没迁"的 web 资源会被本模块也 SELECT
    到,迁完 resources.file_path 但漏了 resource_versions.file_path,
    delete_source 会把两者共享的本地文件删掉,留下 rv 侧悬空死链(没有任何后置
    代码会修)。原来的裸 SQL 文本断言("NOT EXISTS" / "rv.resource_id = r.id"
    出现在字符串里)换成对真实编译出的 ORM 语句做等价断言 —— 确认
    NOT EXISTS 子查询完整落在 WHERE 里、且引用的是
    resource_versions.resource_id = resources.id。"""
    sql = _compiled(sm._web_resource_files_select_stmt(None, 10))
    assert "NOT (EXISTS" in sql
    assert "resource_versions" in sql
    assert "resource_versions.resource_id = " in sql
    assert "resources.id" in sql


# ── _migrate_web_resource_files_row —— content-addressed store_local_file ──


async def test_migrate_web_resource_files_row_migrates_qishui_audio(
    tmp_path, monkeypatch
):
    from app.core.config import settings

    monkeypatch.setattr(settings, "DOWNLOAD_PATH", str(tmp_path))
    media_dir = tmp_path / "global" / "resources" / "web" / "qishui" / "1"
    media_dir.mkdir(parents=True)
    data = b"qishui-audio-bytes"
    (media_dir / "audio.m4a").write_bytes(data)

    store = FakeWebResourceFilesStore()
    _patch_store(monkeypatch, store)
    write_session = _CapturingWriteSession()
    monkeypatch.setattr(sm, "write_scope", _fake_write_scope(write_session))

    outcome = await sm._migrate_web_resource_files_row(
        {
            "resource_id": 1,
            "file_path": "global/resources/web/qishui/1/audio.m4a",
            "scope_id": 42,
        },
        dry_run=False,
        delete_source=False,
    )

    assert outcome == "migrated"
    key, local_path, mime = store.put_file_calls[0]
    assert key == _expected_key(42, data, ".m4a")
    assert mime == "audio/mp4"  # mimetypes 对 .m4a 的推断
    assert len(write_session.statements) == 1
    stmt = write_session.statements[0]
    assert stmt.table.name == "resources"
    params = stmt.compile().params
    assert params["id_1"] == 1
    assert params["file_path"] == f"sb://library/{key}"
    assert params["file_hash"] == hashlib.sha256(data).hexdigest()


async def test_migrate_web_resource_files_row_unknown_mime_falls_back_octet_stream(
    tmp_path, monkeypatch
):
    from app.core.config import settings

    monkeypatch.setattr(settings, "DOWNLOAD_PATH", str(tmp_path))
    media_dir = tmp_path / "global" / "resources" / "web" / "qishui" / "2"
    media_dir.mkdir(parents=True)
    (media_dir / "asset.unknownext").write_bytes(b"data")

    store = FakeWebResourceFilesStore()
    _patch_store(monkeypatch, store)
    monkeypatch.setattr(sm, "write_scope", _fake_write_scope(_CapturingWriteSession()))

    outcome = await sm._migrate_web_resource_files_row(
        {
            "resource_id": 2,
            "file_path": "global/resources/web/qishui/2/asset.unknownext",
            "scope_id": 1,
        },
        dry_run=False,
        delete_source=False,
    )

    assert outcome == "migrated"
    _, _, mime = store.put_file_calls[0]
    assert mime == "application/octet-stream"


async def test_migrate_web_resource_files_row_content_addressed_dedup_skips_put(
    tmp_path, monkeypatch
):
    from app.core.config import settings

    monkeypatch.setattr(settings, "DOWNLOAD_PATH", str(tmp_path))
    media_dir = tmp_path / "global" / "resources" / "web" / "qishui" / "1"
    media_dir.mkdir(parents=True)
    data = b"already-migrated-audio"
    (media_dir / "audio.m4a").write_bytes(data)

    store = FakeWebResourceFilesStore()
    key = _expected_key(42, data, ".m4a")
    store.seed_existing(key, len(data))
    _patch_store(monkeypatch, store)
    write_session = _CapturingWriteSession()
    monkeypatch.setattr(sm, "write_scope", _fake_write_scope(write_session))

    outcome = await sm._migrate_web_resource_files_row(
        {
            "resource_id": 1,
            "file_path": "global/resources/web/qishui/1/audio.m4a",
            "scope_id": 42,
        },
        dry_run=False,
        delete_source=False,
    )

    assert outcome == "migrated"
    assert store.put_file_calls == []  # dedup skip-PUT
    assert len(write_session.statements) == 1
    params = write_session.statements[0].compile().params
    assert params["file_path"] == f"sb://library/{key}"


async def test_migrate_web_resource_files_row_missing_file_returns_missing(
    tmp_path, monkeypatch
):
    from app.core.config import settings

    monkeypatch.setattr(settings, "DOWNLOAD_PATH", str(tmp_path))
    store = FakeWebResourceFilesStore()
    _patch_store(monkeypatch, store)
    write_session = _CapturingWriteSession()
    monkeypatch.setattr(sm, "write_scope", _fake_write_scope(write_session))

    outcome = await sm._migrate_web_resource_files_row(
        {
            "resource_id": 999,
            "file_path": "global/resources/web/qishui/999/audio.m4a",
            "scope_id": 1,
        },
        dry_run=False,
        delete_source=False,
    )

    assert outcome == "missing"
    assert write_session.statements == []
    assert store.put_file_calls == []


async def test_migrate_web_resource_files_row_orphan_null_scope_skips_not_raises(
    tmp_path, monkeypatch
):
    """孤儿 resource(scope_id=None)—— skip,不 raise,不能拖垮整批。"""
    from app.core.config import settings

    monkeypatch.setattr(settings, "DOWNLOAD_PATH", str(tmp_path))
    media_dir = tmp_path / "global" / "resources" / "web" / "qishui" / "5"
    media_dir.mkdir(parents=True)
    (media_dir / "audio.m4a").write_bytes(b"orphan-audio")

    store = FakeWebResourceFilesStore()
    _patch_store(monkeypatch, store)
    write_session = _CapturingWriteSession()
    monkeypatch.setattr(sm, "write_scope", _fake_write_scope(write_session))

    outcome = await sm._migrate_web_resource_files_row(
        {
            "resource_id": 5,
            "file_path": "global/resources/web/qishui/5/audio.m4a",
            "scope_id": None,
        },
        dry_run=False,
        delete_source=False,
    )

    assert outcome == "skipped_no_scope"
    assert write_session.statements == []
    assert store.put_file_calls == []


async def test_migrate_web_resource_files_row_dry_run_never_mutates(
    tmp_path, monkeypatch
):
    from app.core.config import settings

    monkeypatch.setattr(settings, "DOWNLOAD_PATH", str(tmp_path))
    media_dir = tmp_path / "global" / "resources" / "web" / "qishui" / "1"
    media_dir.mkdir(parents=True)
    (media_dir / "audio.m4a").write_bytes(b"audio-bytes")

    store = FakeWebResourceFilesStore()
    _patch_store(monkeypatch, store)
    write_session = _CapturingWriteSession()
    monkeypatch.setattr(sm, "write_scope", _fake_write_scope(write_session))

    outcome = await sm._migrate_web_resource_files_row(
        {
            "resource_id": 1,
            "file_path": "global/resources/web/qishui/1/audio.m4a",
            "scope_id": 42,
        },
        dry_run=True,
        delete_source=True,
    )

    assert outcome == "dry_run_ok"
    # put 发生了(dedup-safe,可重放)但不更新 DB、不删源文件。
    assert len(store.put_file_calls) == 1
    assert write_session.statements == []
    assert (media_dir / "audio.m4a").is_file()


async def test_migrate_web_resource_files_row_delete_source_unlinks_single_file(
    tmp_path, monkeypatch
):
    """delete_source 必须单文件 unlink,不能 rmtree —— 目录里可能还有别的文件。"""
    from app.core.config import settings

    monkeypatch.setattr(settings, "DOWNLOAD_PATH", str(tmp_path))
    media_dir = tmp_path / "global" / "resources" / "web" / "qishui" / "1"
    media_dir.mkdir(parents=True)
    (media_dir / "audio.m4a").write_bytes(b"audio-bytes")
    (media_dir / "sibling.txt").write_bytes(b"sibling-bytes")

    store = FakeWebResourceFilesStore()
    _patch_store(monkeypatch, store)
    monkeypatch.setattr(sm, "write_scope", _fake_write_scope(_CapturingWriteSession()))

    outcome = await sm._migrate_web_resource_files_row(
        {
            "resource_id": 1,
            "file_path": "global/resources/web/qishui/1/audio.m4a",
            "scope_id": 1,
        },
        dry_run=False,
        delete_source=True,
    )

    assert outcome == "migrated"
    assert not (media_dir / "audio.m4a").exists()
    assert (media_dir / "sibling.txt").is_file()
    assert media_dir.is_dir()


async def test_migrate_web_resource_files_row_size_mismatch_raises_before_mutation(
    tmp_path, monkeypatch
):
    from app.core.config import settings

    monkeypatch.setattr(settings, "DOWNLOAD_PATH", str(tmp_path))
    media_dir = tmp_path / "global" / "resources" / "web" / "qishui" / "1"
    media_dir.mkdir(parents=True)
    (media_dir / "audio.m4a").write_bytes(b"audio-bytes")

    store = FakeWebResourceFilesStore(size_offset=-1)  # 模拟上传后 size 对不上
    _patch_store(monkeypatch, store)
    write_session = _CapturingWriteSession()
    monkeypatch.setattr(sm, "write_scope", _fake_write_scope(write_session))

    with pytest.raises(RuntimeError, match="size mismatch"):
        await sm._migrate_web_resource_files_row(
            {
                "resource_id": 1,
                "file_path": "global/resources/web/qishui/1/audio.m4a",
                "scope_id": 1,
            },
            dry_run=False,
            delete_source=True,
        )

    assert write_session.statements == []
    assert (media_dir / "audio.m4a").is_file()


async def test_migrate_web_resource_files_row_already_sb_is_skipped(monkeypatch):
    """防御性 idempotent-replay 保护 —— 正常流程 SELECT 已过滤掉 sb://,但万一
    重放到已迁移的行,必须 skip 而不是当文件系统路径瞎解析(幂等,不双跑)。"""
    store = FakeWebResourceFilesStore()

    outcome = await sm._migrate_web_resource_files_row(
        {
            "resource_id": 1,
            "file_path": "sb://library/t1/de/ad/deadbeef.m4a",
            "scope_id": 1,
        },
        dry_run=False,
        delete_source=False,
    )

    assert outcome == "skipped"
    assert store.put_file_calls == []


async def test_migrate_web_resource_files_row_escapes_download_path_raises(
    tmp_path, monkeypatch
):
    from app.core.config import settings

    monkeypatch.setattr(settings, "DOWNLOAD_PATH", str(tmp_path))
    store = FakeWebResourceFilesStore()
    _patch_store(monkeypatch, store)

    with pytest.raises(RuntimeError, match="escapes DOWNLOAD_PATH"):
        await sm._migrate_web_resource_files_row(
            {
                "resource_id": 1,
                "file_path": "../../etc/passwd",
                "scope_id": 1,
            },
            dry_run=False,
            delete_source=False,
        )


async def test_migrate_web_resource_files_row_does_not_touch_pm_download_path(
    tmp_path, monkeypatch
):
    """只 UPDATE resources.file_path —— 绝不碰 parsed_media.download_path(那一列
    由后置 SQL 处理,不在本 module 职责内)。"""
    from app.core.config import settings

    monkeypatch.setattr(settings, "DOWNLOAD_PATH", str(tmp_path))
    media_dir = tmp_path / "global" / "resources" / "web" / "qishui" / "1"
    media_dir.mkdir(parents=True)
    (media_dir / "audio.m4a").write_bytes(b"audio-bytes")

    store = FakeWebResourceFilesStore()
    _patch_store(monkeypatch, store)
    write_session = _CapturingWriteSession()
    monkeypatch.setattr(sm, "write_scope", _fake_write_scope(write_session))

    await sm._migrate_web_resource_files_row(
        {
            "resource_id": 1,
            "file_path": "global/resources/web/qishui/1/audio.m4a",
            "scope_id": 1,
        },
        dry_run=False,
        delete_source=False,
    )

    assert len(write_session.statements) == 1
    stmt = write_session.statements[0]
    # ONLY resources.file_path is touched — parsed_media.download_path is a
    # post-migration SQL pass's job, not this module's (see module docstring).
    assert stmt.table.name == "resources"
    assert "download_path" not in stmt.compile().params


# ── registry / placeholder wiring ────────────────────────────────────────


def test_web_resource_files_module_registered_with_select_stmt_not_list_rows():
    cfg = sm._MODULES["web_resource_files"]
    assert cfg.list_rows is None
    assert cfg.select_stmt is sm._web_resource_files_select_stmt


def test_web_resource_files_extract_raises_if_ever_called():
    """占位符 —— web_resource_files 完全绕过 _migrate_row(同 derived/
    pm_assets),万一未来重构不小心把它路由进通用路径,必须响亮地失败。"""
    with pytest.raises(RuntimeError, match="_migrate_web_resource_files_row"):
        sm._web_resource_files_extract({})


async def test_web_resource_files_update_row_raises_if_ever_called():
    with pytest.raises(RuntimeError, match="_migrate_web_resource_files_row"):
        await sm._web_resource_files_update_row({}, "sb://library/t1/de/ad/x.m4a", None)


# ── workflow dispatch wiring ─────────────────────────────────────────────


async def test_storage_migration_workflow_dispatches_web_resource_files_module(
    monkeypatch,
):
    """storage_migration_workflow 必须把 module='web_resource_files' 路由到
    _migrate_web_resource_files_row,而不是通用 _migrate_row(同 'derived'/
    'pm_assets' 的特判)。

    ``inspect.unwrap`` past ``@DBOS.workflow`` — same technique as
    test_storage_migration.py's ``_body()`` helper — so this runs without a
    real DBOS runtime."""
    import inspect

    manager = AsyncMock()
    manager.create = AsyncMock()
    manager.start = AsyncMock()
    manager.update_progress = AsyncMock()
    manager.complete = AsyncMock()
    manager.patch_metadata = AsyncMock()

    monkeypatch.setattr(
        "app.services.infra.unified_task_manager.get_task_manager", lambda: manager
    )
    rows = [
        {
            "resource_id": 1,
            "file_path": "global/resources/web/qishui/1/audio.m4a",
            "scope_id": 42,
        }
    ]
    monkeypatch.setattr(sm, "read_scope", _fake_read_scope(rows))
    migrate_row = AsyncMock(return_value="migrated")
    monkeypatch.setattr(sm, "_migrate_web_resource_files_row", migrate_row)

    workflow_body = inspect.unwrap(sm.storage_migration_workflow)
    result = await workflow_body(
        module="web_resource_files",
        scope_id=None,
        limit=100,
        dry_run=False,
        delete_source=False,
    )

    migrate_row.assert_awaited_once()
    assert result["migrated"] == 1
    assert result["module"] == "web_resource_files"
