"""pm_assets module: parsed_media 三列(cover_download_path/music_download_path/
extract_audio_path)迁 S3。

DB-列驱动(同 derived module 的模式),但用 content-addressed
``store_local_file``(不是 derived 的固定前缀 ``put_file``)—— 631 个
cover_download_path 对应的 cover.jpg 若与某个已迁移对象同 sha/scope/ext,
``store_local_file`` 内部的 ``exists()`` 检查会自动 skip-PUT,本 module 无需
特判"refresh vs 真迁",content-addressing 统一处理(见 task brief)。

scope 来自 resource_items(通过 resources.media_id 反查,LEFT JOIN LATERAL,
同 uploads/downloads module 的写法)—— 孤儿 parsed_media(无 resource,或
resource 无 resource_items scope)拿到 scope_id=NULL,必须 skip 而不是 raise
(一个孤儿不该拖垮整批)。

column 名只能是白名单三列之一(``_PM_ASSETS_COLUMNS_WHITELIST``),绝不字符串
插值列名 —— 防 SQL 注入。

Phase C task 2: DB 读写走 ``read_scope()``/``write_scope()`` 的真实 SQLAlchemy
ORM 语句(``select(ParsedMedia...)``/``update(ParsedMedia)...``),不再是裸
``db_engine.fetch_all``/``execute`` —— 测试相应地 patch 这两个 ORM seam。
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


# ── Fake ORM seams (house style borrowed from test_ai_transcription_sql.py) ──


class _FakeExecuteRowsResult:
    """Multi-row result — supports ``.mappings().all()``."""

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


def _fake_read_scope(rows):
    @asynccontextmanager
    async def _read_scope():
        yield _FakeScopeSession(execute_result=_FakeExecuteRowsResult(rows))

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
    compiled = stmt.compile()
    return str(compiled), dict(compiled.params)


# ── FakePmAssetsStore —— exists/put_file/get_size,支持预置"已存在"的 key ──


class FakePmAssetsStore:
    bucket = "library"

    def __init__(self, *, size_offset: int = 0):
        self.put_file_calls: list[tuple[str, str, str]] = []
        # key -> size(bytes)。exists()/get_size() 都读它;预先塞入一个 key
        # 即可模拟"内容已在别处迁移过,dedup skip-PUT"的场景。
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
        """预置一个"已存在"的对象 —— 模拟同 sha 已被别处迁移过。"""
        self._sizes[key] = size


def _patch_store(monkeypatch, store: FakePmAssetsStore) -> None:
    monkeypatch.setattr(media_storage, "library_store", lambda: store)


def _expected_key(scope_id: int, data: bytes, ext: str) -> str:
    sha = hashlib.sha256(data).hexdigest()
    return f"t{scope_id}/{sha[:2]}/{sha[2:4]}/{sha}{ext}"


# ── _list_pm_assets_rows —— DB 行 → 最多 3 个迁移单元的扇出 ─────────────


async def test_list_pm_assets_rows_single_column_fans_to_one_unit(monkeypatch):
    rows = [
        {
            "pm_id": 1,
            "cover_download_path": "global/resources/web/douyin/1/cover.jpg",
            "music_download_path": None,
            "extract_audio_path": None,
            "scope_id": 42,
        }
    ]
    monkeypatch.setattr(sm, "read_scope", _fake_read_scope(rows))

    result = await sm._list_pm_assets_rows(None, limit=100)

    assert result == [
        {
            "pm_id": 1,
            "column": "cover_download_path",
            "rel_path": "global/resources/web/douyin/1/cover.jpg",
            "scope_id": 42,
            "mime": "image/jpeg",
        }
    ]


async def test_list_pm_assets_rows_three_columns_fan_to_three_units(monkeypatch):
    rows = [
        {
            "pm_id": 7,
            "cover_download_path": "global/resources/web/douyin/7/cover.jpg",
            "music_download_path": "global/resources/web/douyin/7/music.m4a",
            "extract_audio_path": "global/resources/web/douyin/7/audio.m4a",
            "scope_id": 99,
        }
    ]
    monkeypatch.setattr(sm, "read_scope", _fake_read_scope(rows))

    result = await sm._list_pm_assets_rows(None, limit=100)

    assert len(result) == 3
    columns = {r["column"] for r in result}
    assert columns == {
        "cover_download_path",
        "music_download_path",
        "extract_audio_path",
    }
    mimes = {r["column"]: r["mime"] for r in result}
    assert mimes["cover_download_path"] == "image/jpeg"
    assert mimes["music_download_path"] == "audio/mp4"
    assert mimes["extract_audio_path"] == "audio/mp4"
    assert all(r["pm_id"] == 7 and r["scope_id"] == 99 for r in result)


async def test_list_pm_assets_rows_skips_already_sb_columns(monkeypatch):
    rows = [
        {
            "pm_id": 1,
            "cover_download_path": "sb://library/t1/ab/cd/deadbeef.jpg",
            "music_download_path": None,
            "extract_audio_path": None,
            "scope_id": 1,
        }
    ]
    monkeypatch.setattr(sm, "read_scope", _fake_read_scope(rows))

    result = await sm._list_pm_assets_rows(None, limit=100)

    assert result == []


async def test_list_pm_assets_rows_preserves_null_scope_for_orphans(monkeypatch):
    """孤儿 parsed_media(无 resource / 无 resource_items scope)—— scope_id
    在扇出时保留为 None,不在这里 raise/skip;实际的 skip 决策留给
    ``_migrate_pm_assets_row``(它才是决定"孤儿 skip 不 raise"的地方)。"""
    rows = [
        {
            "pm_id": 5,
            "cover_download_path": "global/resources/web/douyin/5/cover.jpg",
            "music_download_path": None,
            "extract_audio_path": None,
            "scope_id": None,
        }
    ]
    monkeypatch.setattr(sm, "read_scope", _fake_read_scope(rows))

    result = await sm._list_pm_assets_rows(None, limit=100)

    assert len(result) == 1
    assert result[0]["scope_id"] is None


async def test_list_pm_assets_rows_limit_applied_after_fanout(monkeypatch):
    """2 行、每行 3 列非 sb → 扇出 6 个 unit,但 limit=4 时必须裁到 4 个,
    不是让每行各自的 SELECT LIMIT 起作用(同 derived 的 union-limit 语义)。"""
    rows = [
        {
            "pm_id": 1,
            "cover_download_path": "a/cover.jpg",
            "music_download_path": "a/music.m4a",
            "extract_audio_path": "a/audio.m4a",
            "scope_id": 1,
        },
        {
            "pm_id": 2,
            "cover_download_path": "b/cover.jpg",
            "music_download_path": "b/music.m4a",
            "extract_audio_path": "b/audio.m4a",
            "scope_id": 2,
        },
    ]
    monkeypatch.setattr(sm, "read_scope", _fake_read_scope(rows))

    result = await sm._list_pm_assets_rows(None, limit=4)

    assert len(result) == 4


async def test_list_pm_assets_rows_forwards_scope_id_and_limit(monkeypatch):
    seen_args = {}
    original_select_stmt = sm._pm_assets_select_stmt

    def spy_select_stmt(scope_id, limit):
        seen_args["scope_id"] = scope_id
        seen_args["limit"] = limit
        return original_select_stmt(scope_id, limit)

    monkeypatch.setattr(sm, "_pm_assets_select_stmt", spy_select_stmt)
    monkeypatch.setattr(sm, "read_scope", _fake_read_scope([]))

    await sm._list_pm_assets_rows(42, limit=10)

    assert seen_args == {"scope_id": 42, "limit": 10}


# ── _migrate_pm_assets_row —— content-addressed store_local_file + verify ──


async def test_migrate_pm_assets_row_cover_column(tmp_path, monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "DOWNLOAD_PATH", str(tmp_path))
    media_dir = tmp_path / "global" / "resources" / "web" / "douyin" / "1"
    media_dir.mkdir(parents=True)
    data = b"cover-bytes"
    (media_dir / "cover.jpg").write_bytes(data)

    store = FakePmAssetsStore()
    _patch_store(monkeypatch, store)
    session = _CapturingWriteSession()
    monkeypatch.setattr(sm, "write_scope", _fake_write_scope(session))

    outcome = await sm._migrate_pm_assets_row(
        {
            "pm_id": 1,
            "column": "cover_download_path",
            "rel_path": "global/resources/web/douyin/1/cover.jpg",
            "scope_id": 42,
            "mime": "image/jpeg",
        },
        dry_run=False,
        delete_source=False,
    )

    assert outcome == "migrated"
    key, local_path, mime = store.put_file_calls[0]
    assert key == _expected_key(42, data, ".jpg")
    assert mime == "image/jpeg"
    assert len(session.statements) == 1
    sql, params = _compiled(session.statements[0])
    assert "cover_download_path" in sql
    assert "parsed_media" in sql
    assert params["id_1"] == 1
    assert params["cover_download_path"] == f"sb://library/{key}"


async def test_migrate_pm_assets_row_music_column(tmp_path, monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "DOWNLOAD_PATH", str(tmp_path))
    media_dir = tmp_path / "global" / "resources" / "web" / "douyin" / "7"
    media_dir.mkdir(parents=True)
    data = b"music-bytes"
    (media_dir / "music.m4a").write_bytes(data)

    store = FakePmAssetsStore()
    _patch_store(monkeypatch, store)
    session = _CapturingWriteSession()
    monkeypatch.setattr(sm, "write_scope", _fake_write_scope(session))

    outcome = await sm._migrate_pm_assets_row(
        {
            "pm_id": 7,
            "column": "music_download_path",
            "rel_path": "global/resources/web/douyin/7/music.m4a",
            "scope_id": 99,
            "mime": "audio/mp4",
        },
        dry_run=False,
        delete_source=False,
    )

    assert outcome == "migrated"
    key, _, mime = store.put_file_calls[0]
    assert key == _expected_key(99, data, ".m4a")
    assert mime == "audio/mp4"
    sql, params = _compiled(session.statements[0])
    assert "music_download_path" in sql
    assert params["id_1"] == 7


async def test_migrate_pm_assets_row_extract_audio_column(tmp_path, monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "DOWNLOAD_PATH", str(tmp_path))
    media_dir = tmp_path / "global" / "resources" / "web" / "douyin" / "7"
    media_dir.mkdir(parents=True)
    data = b"extract-audio-bytes"
    (media_dir / "audio.m4a").write_bytes(data)

    store = FakePmAssetsStore()
    _patch_store(monkeypatch, store)
    session = _CapturingWriteSession()
    monkeypatch.setattr(sm, "write_scope", _fake_write_scope(session))

    outcome = await sm._migrate_pm_assets_row(
        {
            "pm_id": 7,
            "column": "extract_audio_path",
            "rel_path": "global/resources/web/douyin/7/audio.m4a",
            "scope_id": 99,
            "mime": "audio/mp4",
        },
        dry_run=False,
        delete_source=False,
    )

    assert outcome == "migrated"
    sql, params = _compiled(session.statements[0])
    assert "extract_audio_path" in sql
    assert params["id_1"] == 7


async def test_migrate_pm_assets_row_one_row_three_units_three_migrates(
    tmp_path, monkeypatch
):
    """一行三列都非 sb —— 上游 _list_pm_assets_rows 扇出 3 个 unit,这里验证
    对 3 个 unit 分别调用 _migrate_pm_assets_row 会产生 3 次 store 写入 + 3 次
    UPDATE,各自落到正确的列。"""
    from app.core.config import settings

    monkeypatch.setattr(settings, "DOWNLOAD_PATH", str(tmp_path))
    media_dir = tmp_path / "global" / "resources" / "web" / "douyin" / "3"
    media_dir.mkdir(parents=True)
    (media_dir / "cover.jpg").write_bytes(b"cover-bytes")
    (media_dir / "music.m4a").write_bytes(b"music-bytes")
    (media_dir / "audio.m4a").write_bytes(b"extract-bytes")

    store = FakePmAssetsStore()
    _patch_store(monkeypatch, store)
    session = _CapturingWriteSession()
    monkeypatch.setattr(sm, "write_scope", _fake_write_scope(session))

    units = [
        {
            "pm_id": 3,
            "column": "cover_download_path",
            "rel_path": "global/resources/web/douyin/3/cover.jpg",
            "scope_id": 10,
            "mime": "image/jpeg",
        },
        {
            "pm_id": 3,
            "column": "music_download_path",
            "rel_path": "global/resources/web/douyin/3/music.m4a",
            "scope_id": 10,
            "mime": "audio/mp4",
        },
        {
            "pm_id": 3,
            "column": "extract_audio_path",
            "rel_path": "global/resources/web/douyin/3/audio.m4a",
            "scope_id": 10,
            "mime": "audio/mp4",
        },
    ]

    outcomes = [
        await sm._migrate_pm_assets_row(u, dry_run=False, delete_source=False)
        for u in units
    ]

    assert outcomes == ["migrated", "migrated", "migrated"]
    assert len(store.put_file_calls) == 3
    assert len(session.statements) == 3
    updated_sqls = [_compiled(stmt)[0] for stmt in session.statements]
    assert any("cover_download_path" in sql for sql in updated_sqls)
    assert any("music_download_path" in sql for sql in updated_sqls)
    assert any("extract_audio_path" in sql for sql in updated_sqls)


async def test_migrate_pm_assets_row_content_addressed_dedup_skips_put(
    tmp_path, monkeypatch
):
    """同 sha/scope/ext 的对象已存在(比如被 derived module 或更早一次
    pm_assets 运行迁移过)—— store_local_file 的 exists() 命中,put_file 不会
    被调用,但列仍然要刷成那个 sb:// 值(不是特判 refresh,是 content-
    addressing 天然统一处理)。"""
    from app.core.config import settings

    monkeypatch.setattr(settings, "DOWNLOAD_PATH", str(tmp_path))
    media_dir = tmp_path / "global" / "resources" / "web" / "douyin" / "1"
    media_dir.mkdir(parents=True)
    data = b"already-migrated-cover"
    (media_dir / "cover.jpg").write_bytes(data)

    store = FakePmAssetsStore()
    key = _expected_key(42, data, ".jpg")
    store.seed_existing(key, len(data))
    _patch_store(monkeypatch, store)
    session = _CapturingWriteSession()
    monkeypatch.setattr(sm, "write_scope", _fake_write_scope(session))

    outcome = await sm._migrate_pm_assets_row(
        {
            "pm_id": 1,
            "column": "cover_download_path",
            "rel_path": "global/resources/web/douyin/1/cover.jpg",
            "scope_id": 42,
            "mime": "image/jpeg",
        },
        dry_run=False,
        delete_source=False,
    )

    assert outcome == "migrated"
    assert store.put_file_calls == []  # dedup skip-PUT
    assert len(session.statements) == 1
    _, params = _compiled(session.statements[0])
    assert params["cover_download_path"] == f"sb://library/{key}"


async def test_migrate_pm_assets_row_missing_file_returns_missing(
    tmp_path, monkeypatch
):
    from app.core.config import settings

    monkeypatch.setattr(settings, "DOWNLOAD_PATH", str(tmp_path))
    store = FakePmAssetsStore()
    _patch_store(monkeypatch, store)
    session = _CapturingWriteSession()
    monkeypatch.setattr(sm, "write_scope", _fake_write_scope(session))

    outcome = await sm._migrate_pm_assets_row(
        {
            "pm_id": 999,
            "column": "cover_download_path",
            "rel_path": "global/resources/web/douyin/999/cover.jpg",
            "scope_id": 1,
            "mime": "image/jpeg",
        },
        dry_run=False,
        delete_source=False,
    )

    assert outcome == "missing"
    assert session.statements == []
    assert store.put_file_calls == []


async def test_migrate_pm_assets_row_orphan_null_scope_skips_not_raises(
    tmp_path, monkeypatch
):
    """孤儿 parsed_media(scope_id=None)—— skip,不 raise,不能拖垮整批。"""
    from app.core.config import settings

    monkeypatch.setattr(settings, "DOWNLOAD_PATH", str(tmp_path))
    media_dir = tmp_path / "global" / "resources" / "web" / "douyin" / "5"
    media_dir.mkdir(parents=True)
    (media_dir / "cover.jpg").write_bytes(b"orphan-cover")

    store = FakePmAssetsStore()
    _patch_store(monkeypatch, store)
    session = _CapturingWriteSession()
    monkeypatch.setattr(sm, "write_scope", _fake_write_scope(session))

    outcome = await sm._migrate_pm_assets_row(
        {
            "pm_id": 5,
            "column": "cover_download_path",
            "rel_path": "global/resources/web/douyin/5/cover.jpg",
            "scope_id": None,
            "mime": "image/jpeg",
        },
        dry_run=False,
        delete_source=False,
    )

    assert outcome == "skipped_no_scope"
    assert session.statements == []
    assert store.put_file_calls == []


async def test_migrate_pm_assets_row_dry_run_never_mutates(tmp_path, monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "DOWNLOAD_PATH", str(tmp_path))
    media_dir = tmp_path / "global" / "resources" / "web" / "douyin" / "1"
    media_dir.mkdir(parents=True)
    (media_dir / "cover.jpg").write_bytes(b"cover-bytes")

    store = FakePmAssetsStore()
    _patch_store(monkeypatch, store)
    session = _CapturingWriteSession()
    monkeypatch.setattr(sm, "write_scope", _fake_write_scope(session))

    outcome = await sm._migrate_pm_assets_row(
        {
            "pm_id": 1,
            "column": "cover_download_path",
            "rel_path": "global/resources/web/douyin/1/cover.jpg",
            "scope_id": 42,
            "mime": "image/jpeg",
        },
        dry_run=True,
        delete_source=True,
    )

    assert outcome == "dry_run_ok"
    # put 发生了(dedup-safe,可重放)但不更新 DB、不删源文件。
    assert len(store.put_file_calls) == 1
    assert session.statements == []
    assert (media_dir / "cover.jpg").is_file()


async def test_migrate_pm_assets_row_delete_source_unlinks_single_file_not_dir(
    tmp_path, monkeypatch
):
    """delete_source 必须单文件 unlink,不能 rmtree —— audio 和视频同目录,
    rmtree 会把视频也删掉。"""
    from app.core.config import settings

    monkeypatch.setattr(settings, "DOWNLOAD_PATH", str(tmp_path))
    media_dir = tmp_path / "global" / "resources" / "web" / "douyin" / "1"
    media_dir.mkdir(parents=True)
    (media_dir / "video.mp4").write_bytes(b"video-bytes")
    (media_dir / "audio.m4a").write_bytes(b"audio-bytes")

    store = FakePmAssetsStore()
    _patch_store(monkeypatch, store)
    session = _CapturingWriteSession()
    monkeypatch.setattr(sm, "write_scope", _fake_write_scope(session))

    outcome = await sm._migrate_pm_assets_row(
        {
            "pm_id": 1,
            "column": "extract_audio_path",
            "rel_path": "global/resources/web/douyin/1/audio.m4a",
            "scope_id": 1,
            "mime": "audio/mp4",
        },
        dry_run=False,
        delete_source=True,
    )

    assert outcome == "migrated"
    assert not (media_dir / "audio.m4a").exists()
    assert (media_dir / "video.mp4").is_file()
    assert media_dir.is_dir()


async def test_migrate_pm_assets_row_size_mismatch_raises_before_mutation(
    tmp_path, monkeypatch
):
    from app.core.config import settings

    monkeypatch.setattr(settings, "DOWNLOAD_PATH", str(tmp_path))
    media_dir = tmp_path / "global" / "resources" / "web" / "douyin" / "1"
    media_dir.mkdir(parents=True)
    (media_dir / "cover.jpg").write_bytes(b"cover-bytes")

    store = FakePmAssetsStore(size_offset=-1)  # 模拟上传后 size 对不上
    _patch_store(monkeypatch, store)
    session = _CapturingWriteSession()
    monkeypatch.setattr(sm, "write_scope", _fake_write_scope(session))

    with pytest.raises(RuntimeError, match="size mismatch"):
        await sm._migrate_pm_assets_row(
            {
                "pm_id": 1,
                "column": "cover_download_path",
                "rel_path": "global/resources/web/douyin/1/cover.jpg",
                "scope_id": 1,
                "mime": "image/jpeg",
            },
            dry_run=False,
            delete_source=True,
        )

    assert session.statements == []
    assert (media_dir / "cover.jpg").is_file()


async def test_migrate_pm_assets_row_already_sb_is_skipped(monkeypatch):
    """防御性 idempotent-replay 保护 —— 正常流程 _list_pm_assets_rows 已过滤
    掉 sb://,但万一重放到已迁移的行,必须 skip 而不是当文件系统路径瞎解析。"""
    store = FakePmAssetsStore()

    outcome = await sm._migrate_pm_assets_row(
        {
            "pm_id": 1,
            "column": "cover_download_path",
            "rel_path": "sb://library/t1/de/ad/deadbeef.jpg",
            "scope_id": 1,
            "mime": "image/jpeg",
        },
        dry_run=False,
        delete_source=False,
    )

    assert outcome == "skipped"
    assert store.put_file_calls == []


async def test_migrate_pm_assets_row_escapes_download_path_raises(
    tmp_path, monkeypatch
):
    from app.core.config import settings

    monkeypatch.setattr(settings, "DOWNLOAD_PATH", str(tmp_path))
    store = FakePmAssetsStore()
    _patch_store(monkeypatch, store)

    with pytest.raises(RuntimeError, match="escapes DOWNLOAD_PATH"):
        await sm._migrate_pm_assets_row(
            {
                "pm_id": 1,
                "column": "cover_download_path",
                "rel_path": "../../etc/passwd",
                "scope_id": 1,
                "mime": "image/jpeg",
            },
            dry_run=False,
            delete_source=False,
        )


async def test_migrate_pm_assets_row_rejects_unknown_column():
    """column 白名单防注入 —— 未知列名必须 raise,绝不字符串插值进 SQL。"""
    with pytest.raises(RuntimeError, match="whitelist"):
        await sm._migrate_pm_assets_row(
            {
                "pm_id": 1,
                "column": "cover_download_path; DROP TABLE parsed_media;--",
                "rel_path": "global/resources/web/douyin/1/cover.jpg",
                "scope_id": 1,
                "mime": "image/jpeg",
            },
            dry_run=False,
            delete_source=False,
        )


# ── column whitelist / registry wiring ───────────────────────────────────


def test_pm_assets_columns_whitelist_tuple():
    assert set(sm._PM_ASSETS_COLUMNS_WHITELIST) == {
        "cover_download_path",
        "music_download_path",
        "extract_audio_path",
    }


def test_pm_assets_module_registered_with_list_rows():
    cfg = sm._MODULES["pm_assets"]
    assert cfg.list_rows is sm._list_pm_assets_rows


def test_pm_assets_extract_and_update_row_raise_if_ever_called():
    """这些是未使用的占位符(pm_assets 完全绕过 _migrate_row,同 derived)——
    万一未来重构不小心把 'pm_assets' 路由进通用路径,必须响亮地失败。"""
    with pytest.raises(RuntimeError, match="_migrate_pm_assets_row"):
        sm._pm_assets_extract({})


async def test_pm_assets_update_row_raises_if_ever_called():
    with pytest.raises(RuntimeError, match="_migrate_pm_assets_row"):
        await sm._pm_assets_update_row({}, "sb://library/t1/de/ad/x.jpg", None)


# ── workflow dispatch wiring ─────────────────────────────────────────────


async def test_storage_migration_workflow_dispatches_pm_assets_module(monkeypatch):
    """storage_migration_workflow 必须把 module='pm_assets' 路由到
    _migrate_pm_assets_row,而不是通用 _migrate_row(同 'derived' 的特判)。

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
    list_rows = AsyncMock(
        return_value=[
            {
                "pm_id": 1,
                "column": "cover_download_path",
                "rel_path": "global/resources/web/douyin/1/cover.jpg",
                "scope_id": 42,
                "mime": "image/jpeg",
            }
        ]
    )
    migrate_row = AsyncMock(return_value="migrated")
    monkeypatch.setattr(sm, "_migrate_pm_assets_row", migrate_row)
    # Registry holds a direct reference to the module-level function object
    # captured at import time — patch the ModuleConfig too so the dispatch
    # (which calls module_cfg.list_rows, not sm._list_pm_assets_rows by name)
    # actually uses our mock.
    monkeypatch.setitem(
        sm._MODULES,
        "pm_assets",
        sm.ModuleConfig(
            name="pm_assets",
            select_stmt=sm._MODULES["pm_assets"].select_stmt,
            extract=sm._MODULES["pm_assets"].extract,
            update_row=sm._MODULES["pm_assets"].update_row,
            list_rows=list_rows,
        ),
    )

    workflow_body = inspect.unwrap(sm.storage_migration_workflow)
    result = await workflow_body(
        module="pm_assets",
        scope_id=None,
        limit=100,
        dry_run=False,
        delete_source=False,
    )

    migrate_row.assert_awaited_once()
    assert result["migrated"] == 1
    assert result["module"] == "pm_assets"
