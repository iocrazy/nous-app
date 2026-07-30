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
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from app.services.library import media_storage
from app.workflows import storage_migration as sm

# NB: asyncio_mode = "auto"(pyproject.toml)自动识别 async def 测试。


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


async def test_web_resource_files_select_sql_forwards_scope_id_and_limit(
    monkeypatch,
):
    fetch_all = AsyncMock(return_value=[])
    monkeypatch.setattr(sm.db_engine, "fetch_all", fetch_all)

    cfg = sm._MODULES["web_resource_files"]
    await sm.db_engine.fetch_all(cfg.select_sql, {"scope_id": 42, "limit": 10})

    _, params = fetch_all.call_args.args
    assert params == {"scope_id": 42, "limit": 10}


def test_web_resource_files_select_sql_excludes_sb_and_trashed_and_non_web():
    sql = sm._WEB_RESOURCE_FILES_SELECT_SQL
    assert "source_type" in sql and "'web'" in sql
    assert "NOT LIKE 'sb://%'" in sql
    assert "is_trashed" in sql
    # 不 JOIN resource_versions —— 正是收漏无-rv 行的关键(与 downloads 的
    # SELECT 区分开)。
    assert "resource_versions" not in sql


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
    execute = AsyncMock(return_value=0)
    monkeypatch.setattr(sm.db_engine, "execute", execute)

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
    execute.assert_awaited_once()
    sql_arg, params_arg = execute.call_args.args
    assert "resources" in sql_arg
    assert "file_path" in sql_arg
    assert params_arg["rid"] == 1
    assert params_arg["fp"] == f"sb://library/{key}"
    assert params_arg["sha"] == hashlib.sha256(data).hexdigest()


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
    monkeypatch.setattr(sm.db_engine, "execute", AsyncMock(return_value=0))

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
    execute = AsyncMock(return_value=0)
    monkeypatch.setattr(sm.db_engine, "execute", execute)

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
    execute.assert_awaited_once()
    _, params_arg = execute.call_args.args
    assert params_arg["fp"] == f"sb://library/{key}"


async def test_migrate_web_resource_files_row_missing_file_returns_missing(
    tmp_path, monkeypatch
):
    from app.core.config import settings

    monkeypatch.setattr(settings, "DOWNLOAD_PATH", str(tmp_path))
    store = FakeWebResourceFilesStore()
    _patch_store(monkeypatch, store)
    execute = AsyncMock(return_value=0)
    monkeypatch.setattr(sm.db_engine, "execute", execute)

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
    execute.assert_not_called()
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
    execute = AsyncMock(return_value=0)
    monkeypatch.setattr(sm.db_engine, "execute", execute)

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
    execute.assert_not_called()
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
    execute = AsyncMock(return_value=0)
    monkeypatch.setattr(sm.db_engine, "execute", execute)

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
    execute.assert_not_called()
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
    monkeypatch.setattr(sm.db_engine, "execute", AsyncMock(return_value=0))

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
    execute = AsyncMock(return_value=0)
    monkeypatch.setattr(sm.db_engine, "execute", execute)

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

    execute.assert_not_called()
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
    execute = AsyncMock(return_value=0)
    monkeypatch.setattr(sm.db_engine, "execute", execute)

    await sm._migrate_web_resource_files_row(
        {
            "resource_id": 1,
            "file_path": "global/resources/web/qishui/1/audio.m4a",
            "scope_id": 1,
        },
        dry_run=False,
        delete_source=False,
    )

    execute.assert_awaited_once()
    sql_arg, _ = execute.call_args.args
    assert "parsed_media" not in sql_arg
    assert "download_path" not in sql_arg


# ── registry / placeholder wiring ────────────────────────────────────────


def test_web_resource_files_module_registered_with_select_sql_not_list_rows():
    cfg = sm._MODULES["web_resource_files"]
    assert cfg.list_rows is None
    assert cfg.select_sql is sm._WEB_RESOURCE_FILES_SELECT_SQL


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
    fetch_all = AsyncMock(
        return_value=[
            {
                "resource_id": 1,
                "file_path": "global/resources/web/qishui/1/audio.m4a",
                "scope_id": 42,
            }
        ]
    )
    monkeypatch.setattr(sm.db_engine, "fetch_all", fetch_all)
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
