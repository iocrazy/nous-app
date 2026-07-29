"""derived module: DB-列驱动(thumbnail_path/cover_image_path) + 磁盘发现
(preview_sprite.jpg,无 DB 列)。

Rework(2026-07-29): 全量 dry-run 暴露旧版"只扫 derived/ 目录"的 _list_derived_rows
只覆盖 10 个缩略图 —— 真实数据 892 个 thumbnail_path / 632 个 cover_image_path
非 sb,其中 765 个 next-to-source(和视频同目录)+ 119 个 teams/,老版磁盘遍历完全
够不着。改成 DB 列驱动后,逐行 resolve 到真实文件(不管在哪),迁到干净前缀
``derived/{rid}/{filename}``(单文件 put_file,不是 put_dir)。sprite 没有 DB 列,
仍走磁盘发现(``derived/thumbnails/{rid}/preview_sprite.jpg`` 固定约定)。

同 rid 的 thumbnail/cover/sprite 三者共享 ``derived/{rid}/`` 前缀但 filename 不同,
无碰撞;delete_source 只 unlink 单文件(next-to-source 缩略图和源文件同目录,
绝不能 rmtree)。
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from app.services.library import media_storage
from app.workflows import storage_migration as sm

# NB: asyncio_mode = "auto"(pyproject.toml)自动识别 async def 测试—— 无需
# pytestmark,文件级打标会误伤下面的同步 registry/placeholder 测试。


# ── FakeDerivedStore —— 单文件 put_file + get_size,不是 put_dir ─────────


class FakeDerivedStore:
    bucket = "library"

    def __init__(self, *, size_offset: int = 0):
        self.put_file_calls: list[tuple[str, str, str]] = []
        # key -> size(bytes)。put_file 成功后写入,get_size 读它。
        self._sizes: dict[str, int] = {}
        # 用于模拟"上传后校验发现 size 不符"的场景(round-trip 篡改)。
        self._size_offset = size_offset

    async def put_file(self, key: str, file_path: str, mime: str, *, upsert=True):
        self.put_file_calls.append((key, file_path, mime))
        self._sizes[key] = Path(file_path).stat().st_size + self._size_offset

    async def get_size(self, key: str) -> int:
        if key not in self._sizes:
            raise RuntimeError(f"no such object: {key}")
        return self._sizes[key]


def _patch_store(monkeypatch, store: FakeDerivedStore) -> None:
    monkeypatch.setattr(media_storage, "library_store", lambda: store)


# ── _list_derived_rows —— DB 列驱动(thumbnail/cover) ∪ 磁盘发现(sprite) ──


async def test_list_derived_rows_db_driven_thumbnail_and_cover(monkeypatch):
    fetch_all = AsyncMock(
        return_value=[
            {
                "resource_id": 100,
                "thumbnail_path": "global/resources/web/douyin/100/thumbnail.webp",
                "cover_image_path": None,
            },
            {
                "resource_id": 200,
                "thumbnail_path": None,
                "cover_image_path": "teams/42/derived/covers/200/cover.jpg",
            },
        ]
    )
    monkeypatch.setattr(sm.db_engine, "fetch_all", fetch_all)

    rows = await sm._list_derived_rows(None, limit=100)

    assert {
        "resource_id": 100,
        "column": "thumbnail_path",
        "rel_path": "global/resources/web/douyin/100/thumbnail.webp",
    } in rows
    assert {
        "resource_id": 200,
        "column": "cover_image_path",
        "rel_path": "teams/42/derived/covers/200/cover.jpg",
    } in rows
    assert len(rows) == 2


async def test_list_derived_rows_skips_already_sb_columns(monkeypatch):
    fetch_all = AsyncMock(
        return_value=[
            {
                "resource_id": 100,
                "thumbnail_path": "sb://library/derived/100/thumbnail.webp",
                "cover_image_path": None,
            }
        ]
    )
    monkeypatch.setattr(sm.db_engine, "fetch_all", fetch_all)

    rows = await sm._list_derived_rows(None, limit=100)

    assert rows == []


async def test_list_derived_rows_same_resource_both_columns(monkeypatch):
    """一个 resource 同时有 thumbnail_path + cover_image_path 非 sb —— 两个
    独立行,不是合并成一行。"""
    fetch_all = AsyncMock(
        return_value=[
            {
                "resource_id": 100,
                "thumbnail_path": "global/resources/web/douyin/100/thumbnail.webp",
                "cover_image_path": "global/resources/web/douyin/100/cover.jpg",
            }
        ]
    )
    monkeypatch.setattr(sm.db_engine, "fetch_all", fetch_all)

    rows = await sm._list_derived_rows(None, limit=100)

    columns = {r["column"] for r in rows}
    assert columns == {"thumbnail_path", "cover_image_path"}
    assert all(r["resource_id"] == 100 for r in rows)


async def test_list_derived_rows_disk_walk_finds_sprite(tmp_path, monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "DOWNLOAD_PATH", str(tmp_path))
    sprite_dir = tmp_path / "derived" / "thumbnails" / "555"
    sprite_dir.mkdir(parents=True)
    (sprite_dir / "preview_sprite.jpg").write_bytes(b"sprite-bytes")

    monkeypatch.setattr(sm.db_engine, "fetch_all", AsyncMock(return_value=[]))

    rows = await sm._list_derived_rows(None, limit=100)

    assert rows == [
        {
            "resource_id": "555",
            "column": None,
            "rel_path": "derived/thumbnails/555/preview_sprite.jpg",
        }
    ]


async def test_list_derived_rows_disk_walk_skips_empty_dirs(tmp_path, monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "DOWNLOAD_PATH", str(tmp_path))
    empty_dir = tmp_path / "derived" / "thumbnails" / "999"
    empty_dir.mkdir(parents=True)

    monkeypatch.setattr(sm.db_engine, "fetch_all", AsyncMock(return_value=[]))

    rows = await sm._list_derived_rows(None, limit=100)

    assert rows == []


async def test_list_derived_rows_combines_db_and_sprite_same_resource(
    tmp_path, monkeypatch
):
    """同一 resource_id 既有 DB 列(thumbnail)又有磁盘 sprite —— 两行都要出现,
    三种派生资源(thumbnail + cover + sprite)独立 key 无碰撞的前置条件。"""
    from app.core.config import settings

    monkeypatch.setattr(settings, "DOWNLOAD_PATH", str(tmp_path))
    sprite_dir = tmp_path / "derived" / "thumbnails" / "100"
    sprite_dir.mkdir(parents=True)
    (sprite_dir / "preview_sprite.jpg").write_bytes(b"sprite-bytes")

    fetch_all = AsyncMock(
        return_value=[
            {
                "resource_id": 100,
                "thumbnail_path": "global/resources/web/douyin/100/thumbnail.webp",
                "cover_image_path": "global/resources/web/douyin/100/cover.jpg",
            }
        ]
    )
    monkeypatch.setattr(sm.db_engine, "fetch_all", fetch_all)

    rows = await sm._list_derived_rows(None, limit=100)

    assert len(rows) == 3
    columns = {r["column"] for r in rows}
    assert columns == {"thumbnail_path", "cover_image_path", None}


async def test_list_derived_rows_respects_limit_after_union(tmp_path, monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "DOWNLOAD_PATH", str(tmp_path))
    for rid in ("1", "2", "3"):
        d = tmp_path / "derived" / "thumbnails" / rid
        d.mkdir(parents=True)
        (d / "preview_sprite.jpg").write_bytes(b"s")

    monkeypatch.setattr(sm.db_engine, "fetch_all", AsyncMock(return_value=[]))

    rows = await sm._list_derived_rows(None, limit=2)

    assert len(rows) == 2


async def test_list_derived_rows_rejects_scope_id():
    """derived assets carry no scope of their own —— 非 None scope_id 必须
    raise,不能静默返回 unscoped batch。"""
    with pytest.raises(ValueError, match="does not support scope_id"):
        await sm._list_derived_rows(5, limit=100)


# ── _migrate_derived_row —— 单文件 put_file + verify + 列更新 ───────────


async def test_migrate_derived_row_thumbnail_next_to_source(tmp_path, monkeypatch):
    """thumbnail_path 是 next-to-source(视频目录里)—— put_file 到干净前缀
    derived/{rid}/thumbnail.webp,列更新为 sb://library/derived/{rid}/..."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "DOWNLOAD_PATH", str(tmp_path))
    video_dir = tmp_path / "global" / "resources" / "web" / "douyin" / "100"
    video_dir.mkdir(parents=True)
    (video_dir / "video.mp4").write_bytes(b"not-a-real-video")
    (video_dir / "thumbnail.webp").write_bytes(b"thumb-bytes")

    store = FakeDerivedStore()
    _patch_store(monkeypatch, store)
    execute = AsyncMock(return_value=0)
    monkeypatch.setattr(sm.db_engine, "execute", execute)

    outcome = await sm._migrate_derived_row(
        {
            "resource_id": 100,
            "column": "thumbnail_path",
            "rel_path": "global/resources/web/douyin/100/thumbnail.webp",
        },
        dry_run=False,
        delete_source=False,
    )

    assert outcome == "migrated"
    key, local_path, mime = store.put_file_calls[0]
    assert key == "derived/100/thumbnail.webp"
    assert mime == "image/webp"
    execute.assert_awaited_once()
    sql_arg, params_arg = execute.call_args.args
    assert "thumbnail_path" in sql_arg
    assert params_arg["path"] == "sb://library/derived/100/thumbnail.webp"
    assert params_arg["resource_id"] == 100
    # next-to-source 缩略图,视频文件必须原地不动(没碰 delete_source)。
    assert (video_dir / "video.mp4").is_file()


async def test_migrate_derived_row_cover_image(tmp_path, monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "DOWNLOAD_PATH", str(tmp_path))
    cover_dir = tmp_path / "teams" / "42" / "derived" / "covers" / "200"
    cover_dir.mkdir(parents=True)
    (cover_dir / "cover.jpg").write_bytes(b"cover-bytes")

    store = FakeDerivedStore()
    _patch_store(monkeypatch, store)
    execute = AsyncMock(return_value=0)
    monkeypatch.setattr(sm.db_engine, "execute", execute)

    outcome = await sm._migrate_derived_row(
        {
            "resource_id": 200,
            "column": "cover_image_path",
            "rel_path": "teams/42/derived/covers/200/cover.jpg",
        },
        dry_run=False,
        delete_source=False,
    )

    assert outcome == "migrated"
    key, _, _ = store.put_file_calls[0]
    assert key == "derived/200/cover.jpg"
    sql_arg, params_arg = execute.call_args.args
    assert "cover_image_path" in sql_arg
    assert params_arg["path"] == "sb://library/derived/200/cover.jpg"


async def test_migrate_derived_row_sprite_no_column_update(tmp_path, monkeypatch):
    """sprite 无 DB 列 —— 迁完不更新任何列。"""
    from app.core.config import settings

    monkeypatch.setattr(settings, "DOWNLOAD_PATH", str(tmp_path))
    sprite_dir = tmp_path / "derived" / "thumbnails" / "555"
    sprite_dir.mkdir(parents=True)
    (sprite_dir / "preview_sprite.jpg").write_bytes(b"sprite-bytes")

    store = FakeDerivedStore()
    _patch_store(monkeypatch, store)
    execute = AsyncMock(return_value=0)
    monkeypatch.setattr(sm.db_engine, "execute", execute)

    outcome = await sm._migrate_derived_row(
        {
            "resource_id": "555",
            "column": None,
            "rel_path": "derived/thumbnails/555/preview_sprite.jpg",
        },
        dry_run=False,
        delete_source=False,
    )

    assert outcome == "migrated"
    key, _, _ = store.put_file_calls[0]
    assert key == "derived/555/preview_sprite.jpg"
    execute.assert_not_called()


async def test_migrate_derived_row_same_rid_three_kinds_no_key_collision(
    tmp_path, monkeypatch
):
    """同 rid 有 thumbnail + cover + sprite 三个 —— 三行都迁成功,三个独立
    key,无 false mismatch。"""
    from app.core.config import settings

    monkeypatch.setattr(settings, "DOWNLOAD_PATH", str(tmp_path))
    video_dir = tmp_path / "global" / "resources" / "web" / "douyin" / "100"
    video_dir.mkdir(parents=True)
    (video_dir / "thumbnail.webp").write_bytes(b"thumb-bytes")
    (video_dir / "cover.jpg").write_bytes(b"cover-bytes")
    sprite_dir = tmp_path / "derived" / "thumbnails" / "100"
    sprite_dir.mkdir(parents=True)
    (sprite_dir / "preview_sprite.jpg").write_bytes(b"sprite-bytes")

    store = FakeDerivedStore()
    _patch_store(monkeypatch, store)
    monkeypatch.setattr(sm.db_engine, "execute", AsyncMock(return_value=0))

    outcome_thumb = await sm._migrate_derived_row(
        {
            "resource_id": 100,
            "column": "thumbnail_path",
            "rel_path": "global/resources/web/douyin/100/thumbnail.webp",
        },
        dry_run=False,
        delete_source=False,
    )
    outcome_cover = await sm._migrate_derived_row(
        {
            "resource_id": 100,
            "column": "cover_image_path",
            "rel_path": "global/resources/web/douyin/100/cover.jpg",
        },
        dry_run=False,
        delete_source=False,
    )
    outcome_sprite = await sm._migrate_derived_row(
        {
            "resource_id": "100",
            "column": None,
            "rel_path": "derived/thumbnails/100/preview_sprite.jpg",
        },
        dry_run=False,
        delete_source=False,
    )

    assert (outcome_thumb, outcome_cover, outcome_sprite) == (
        "migrated",
        "migrated",
        "migrated",
    )
    keys = [k for k, _, _ in store.put_file_calls]
    assert keys == [
        "derived/100/thumbnail.webp",
        "derived/100/cover.jpg",
        "derived/100/preview_sprite.jpg",
    ]
    assert len(set(keys)) == 3


async def test_migrate_derived_row_missing_file_returns_missing(tmp_path, monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "DOWNLOAD_PATH", str(tmp_path))
    store = FakeDerivedStore()
    _patch_store(monkeypatch, store)
    execute = AsyncMock(return_value=0)
    monkeypatch.setattr(sm.db_engine, "execute", execute)

    outcome = await sm._migrate_derived_row(
        {
            "resource_id": 999,
            "column": "thumbnail_path",
            "rel_path": "global/resources/web/douyin/999/thumbnail.webp",
        },
        dry_run=False,
        delete_source=False,
    )

    assert outcome == "missing"
    execute.assert_not_called()
    assert store.put_file_calls == []


async def test_migrate_derived_row_dry_run_never_mutates(tmp_path, monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "DOWNLOAD_PATH", str(tmp_path))
    video_dir = tmp_path / "global" / "resources" / "web" / "douyin" / "100"
    video_dir.mkdir(parents=True)
    (video_dir / "thumbnail.webp").write_bytes(b"thumb-bytes")

    store = FakeDerivedStore()
    _patch_store(monkeypatch, store)
    execute = AsyncMock(return_value=0)
    monkeypatch.setattr(sm.db_engine, "execute", execute)

    outcome = await sm._migrate_derived_row(
        {
            "resource_id": 100,
            "column": "thumbnail_path",
            "rel_path": "global/resources/web/douyin/100/thumbnail.webp",
        },
        dry_run=True,
        delete_source=True,
    )

    assert outcome == "dry_run_ok"
    # put_file 发生了(dedup-safe,可重放)但不更新 DB、不删源文件。
    assert len(store.put_file_calls) == 1
    execute.assert_not_called()
    assert (video_dir / "thumbnail.webp").is_file()


async def test_migrate_derived_row_delete_source_unlinks_single_file_not_dir(
    tmp_path, monkeypatch
):
    """delete_source 必须单文件 unlink,不能 rmtree —— next-to-source 缩略图
    和视频同目录,rmtree 会把视频也删掉。"""
    from app.core.config import settings

    monkeypatch.setattr(settings, "DOWNLOAD_PATH", str(tmp_path))
    video_dir = tmp_path / "global" / "resources" / "web" / "douyin" / "100"
    video_dir.mkdir(parents=True)
    (video_dir / "video.mp4").write_bytes(b"video-bytes")
    (video_dir / "thumbnail.webp").write_bytes(b"thumb-bytes")

    store = FakeDerivedStore()
    _patch_store(monkeypatch, store)
    monkeypatch.setattr(sm.db_engine, "execute", AsyncMock(return_value=0))

    outcome = await sm._migrate_derived_row(
        {
            "resource_id": 100,
            "column": "thumbnail_path",
            "rel_path": "global/resources/web/douyin/100/thumbnail.webp",
        },
        dry_run=False,
        delete_source=True,
    )

    assert outcome == "migrated"
    assert not (video_dir / "thumbnail.webp").exists()
    # 视频文件必须还在 —— 单文件 unlink,不是目录删除。
    assert (video_dir / "video.mp4").is_file()
    assert video_dir.is_dir()


async def test_migrate_derived_row_size_mismatch_raises_before_mutation(
    tmp_path, monkeypatch
):
    from app.core.config import settings

    monkeypatch.setattr(settings, "DOWNLOAD_PATH", str(tmp_path))
    video_dir = tmp_path / "global" / "resources" / "web" / "douyin" / "100"
    video_dir.mkdir(parents=True)
    (video_dir / "thumbnail.webp").write_bytes(b"thumb-bytes")

    store = FakeDerivedStore(size_offset=-1)  # 模拟上传后 size 对不上
    _patch_store(monkeypatch, store)
    execute = AsyncMock(return_value=0)
    monkeypatch.setattr(sm.db_engine, "execute", execute)

    with pytest.raises(RuntimeError, match="size mismatch"):
        await sm._migrate_derived_row(
            {
                "resource_id": 100,
                "column": "thumbnail_path",
                "rel_path": "global/resources/web/douyin/100/thumbnail.webp",
            },
            dry_run=False,
            delete_source=True,
        )

    execute.assert_not_called()
    assert (video_dir / "thumbnail.webp").is_file()


async def test_migrate_derived_row_already_sb_is_skipped(monkeypatch):
    """防御性 idempotent-replay 保护 —— 正常流程行源已经过滤掉 sb://,但万一
    重放到一个已迁移的行,必须 skip 而不是当文件系统路径瞎解析。"""
    store = FakeDerivedStore()

    outcome = await sm._migrate_derived_row(
        {
            "resource_id": 100,
            "column": "thumbnail_path",
            "rel_path": "sb://library/derived/100/thumbnail.webp",
        },
        dry_run=False,
        delete_source=False,
    )

    assert outcome == "skipped"
    assert store.put_file_calls == []


async def test_migrate_derived_row_escapes_download_path_raises(tmp_path, monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "DOWNLOAD_PATH", str(tmp_path))
    store = FakeDerivedStore()
    _patch_store(monkeypatch, store)

    with pytest.raises(RuntimeError, match="escapes DOWNLOAD_PATH"):
        await sm._migrate_derived_row(
            {
                "resource_id": 100,
                "column": "thumbnail_path",
                "rel_path": "../../etc/passwd",
            },
            dry_run=False,
            delete_source=False,
        )


# ── registry wiring / placeholder-callable guards ───────────────────────


def test_derived_module_registered_with_list_rows():
    cfg = sm._MODULES["derived"]
    assert cfg.list_rows is sm._list_derived_rows


def test_derived_extract_and_update_row_raise_if_ever_called():
    """这些是未使用的占位符(derived 完全绕过 _migrate_row)—— 万一未来重构
    不小心把 'derived' 路由进通用路径,必须响亮地失败,而不是悄悄出错。"""
    with pytest.raises(RuntimeError, match="_migrate_derived_row"):
        sm._derived_extract({})


async def test_derived_update_row_raises_if_ever_called():
    with pytest.raises(RuntimeError, match="_migrate_derived_row"):
        await sm._derived_update_row({}, "sb://library/derived/1/x.jpg", None)
