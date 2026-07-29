"""derived module:迁 derived/thumbnails/{rid}/ 与 derived/covers/{rid}/ 到
sb://library/derived/{rid}/。

Runtime 勘察(2026-07-29, prod nous-backend 容器)：
  DOWNLOAD_PATH/derived/thumbnails/{resource_id}/thumbnail.{webp,png}
    [+ preview_sprite.jpg]
  DOWNLOAD_PATH/derived/hls/...          ← 不是本 module 的活,hls module
                                            已经通过 resource_versions
                                            .hls_path 管了
  DOWNLOAD_PATH/derived/covers/...       ← prod 还没有(cover 上传到
                                            derived/covers/ 是新逻辑),但
                                            形态跟 thumbnails 一样

preview_sprite.jpg 在任何地方都没有 DB 列记录它的位置(thumbnail_service.py
只在生成 thumbnail 时写 resources.thumbnail_path,从不为 sprite 写任何
列;resources_crud_router.py 的 serve_preview_sprite 纯靠 resource_id + 固定
文件名探测)——所以本 module 的"行"来自文件系统遍历(``_list_derived_rows``),
不是 SQL SELECT,跟其余四个 module 的 house style(FakeStore + 4 步安全序)
不同的地方仅在于:行源不是 db_engine.fetch_all,而是目录遍历;DB 同步是
"能同步的列就同步"（thumbnail_path/cover_image_path），不是必须的一步。
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from app.services.library import media_storage
from app.workflows import storage_migration as sm

# NB: asyncio_mode = "auto" (pyproject.toml) auto-detects async def tests —
# no pytestmark needed, and applying one file-wide would wrongly tag the
# sync registry/placeholder tests below as asyncio tests (PytestWarning) —
# same convention as test_storage_migration_hls.py / _downloads.py.


# ── FakeDerivedStore — same put_dir/list_prefix shape as FakeAlbumStore in
#    test_storage_migration_downloads.py. ───────────────────────────────


class FakeDerivedStore:
    bucket = "library"

    def __init__(self, *, drop_last: bool = False):
        self._keys: list[str] = []
        self.put_dir_called = False
        self._drop_last = drop_last

    async def put_dir(self, local_dir, key_for, *, concurrency=8, skip_existing=False):
        root = Path(local_dir)
        files = sorted(p for p in root.rglob("*") if p.is_file())
        self.put_dir_called = True
        for p in files:
            self._keys.append(key_for(p.relative_to(root).as_posix()))
        if self._drop_last and self._keys:
            self._keys.pop()
        return len(files)

    async def list_prefix(self, prefix: str) -> list[str]:
        return [k for k in self._keys if k.startswith(prefix)]


def _patch_store(monkeypatch, store: FakeDerivedStore) -> None:
    monkeypatch.setattr(media_storage, "library_store", lambda: store)


# ── _list_derived_rows — filesystem walk, not SQL ───────────────────────


async def test_list_derived_rows_walks_thumbnails_and_covers(tmp_path, monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "DOWNLOAD_PATH", str(tmp_path))
    thumb_dir = tmp_path / "derived" / "thumbnails" / "100"
    thumb_dir.mkdir(parents=True)
    (thumb_dir / "thumbnail.webp").write_bytes(b"t")
    (thumb_dir / "preview_sprite.jpg").write_bytes(b"s")

    cover_dir = tmp_path / "derived" / "covers" / "200"
    cover_dir.mkdir(parents=True)
    (cover_dir / "cover.png").write_bytes(b"c")

    # derived/hls must never surface here — that's the hls module's job.
    hls_dir = tmp_path / "derived" / "hls" / "300" / "400"
    hls_dir.mkdir(parents=True)
    (hls_dir / "master.m3u8").write_text("#EXTM3U\n")

    rows = await sm._list_derived_rows(None, limit=100)

    kinds = {(r["resource_id"], r["kind"]) for r in rows}
    assert kinds == {("100", "thumbnails"), ("200", "covers")}


async def test_list_derived_rows_skips_empty_dirs(tmp_path, monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "DOWNLOAD_PATH", str(tmp_path))
    empty_dir = tmp_path / "derived" / "thumbnails" / "999"
    empty_dir.mkdir(parents=True)

    rows = await sm._list_derived_rows(None, limit=100)

    assert rows == []


async def test_list_derived_rows_respects_limit(tmp_path, monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "DOWNLOAD_PATH", str(tmp_path))
    for rid in ("1", "2", "3"):
        d = tmp_path / "derived" / "thumbnails" / rid
        d.mkdir(parents=True)
        (d / "thumbnail.webp").write_bytes(b"t")

    rows = await sm._list_derived_rows(None, limit=2)

    assert len(rows) == 2


async def test_list_derived_rows_rejects_scope_id():
    """derived assets carry no scope of their own — a non-None scope_id must
    raise, not silently return an unscoped batch."""
    with pytest.raises(ValueError, match="does not support scope_id"):
        await sm._list_derived_rows(5, limit=100)


# ── _migrate_derived_row — put_dir + verify + best-effort column sync ──


def _make_thumb_dir(tmp_path: Path, rid: str = "100") -> Path:
    d = tmp_path / "derived" / "thumbnails" / rid
    d.mkdir(parents=True)
    (d / "thumbnail.webp").write_bytes(b"thumb-bytes")
    (d / "preview_sprite.jpg").write_bytes(b"sprite-bytes")
    return d


async def test_migrate_derived_row_dry_run_never_mutates(tmp_path, monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "DOWNLOAD_PATH", str(tmp_path))
    d = _make_thumb_dir(tmp_path)
    store = FakeDerivedStore()
    _patch_store(monkeypatch, store)
    fetch_one = AsyncMock(return_value=None)
    monkeypatch.setattr(sm.db_engine, "fetch_one", fetch_one)
    execute = AsyncMock(return_value=0)
    monkeypatch.setattr(sm.db_engine, "execute", execute)

    outcome = await sm._migrate_derived_row(
        {"resource_id": "100", "kind": "thumbnails", "dir": str(d)},
        dry_run=True,
        delete_source=True,
    )

    assert outcome == "dry_run_ok"
    fetch_one.assert_not_called()
    execute.assert_not_called()
    assert d.exists()


async def test_migrate_derived_row_syncs_thumbnail_path_column(tmp_path, monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "DOWNLOAD_PATH", str(tmp_path))
    d = _make_thumb_dir(tmp_path)
    store = FakeDerivedStore()
    _patch_store(monkeypatch, store)
    fetch_one = AsyncMock(
        return_value={
            "thumbnail_path": "derived/thumbnails/100/thumbnail.webp",
            "cover_image_path": None,
        }
    )
    monkeypatch.setattr(sm.db_engine, "fetch_one", fetch_one)
    execute = AsyncMock(return_value=0)
    monkeypatch.setattr(sm.db_engine, "execute", execute)

    outcome = await sm._migrate_derived_row(
        {"resource_id": "100", "kind": "thumbnails", "dir": str(d)},
        dry_run=False,
        delete_source=False,
    )

    assert outcome == "migrated"
    # thumbnail_path column synced to the new sb:// value (basename kept).
    execute.assert_awaited_once()
    sql_arg, params_arg = execute.call_args.args
    assert "thumbnail_path" in sql_arg
    assert params_arg["path"] == "sb://library/derived/100/thumbnail.webp"
    assert params_arg["resource_id"] == 100
    # preview_sprite.jpg has no column to sync — only one UPDATE fired.


async def test_migrate_derived_row_no_db_row_still_migrates_files(
    tmp_path, monkeypatch
):
    """preview_sprite-only resources (no thumbnail_path/cover_image_path
    anywhere, or the resource row lookup itself fails) must still migrate
    the files — the DB sync is best-effort, not a precondition."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "DOWNLOAD_PATH", str(tmp_path))
    d = tmp_path / "derived" / "thumbnails" / "555"
    d.mkdir(parents=True)
    (d / "preview_sprite.jpg").write_bytes(b"sprite-only")
    store = FakeDerivedStore()
    _patch_store(monkeypatch, store)
    fetch_one = AsyncMock(side_effect=RuntimeError("boom"))
    monkeypatch.setattr(sm.db_engine, "fetch_one", fetch_one)
    execute = AsyncMock(return_value=0)
    monkeypatch.setattr(sm.db_engine, "execute", execute)

    outcome = await sm._migrate_derived_row(
        {"resource_id": "555", "kind": "thumbnails", "dir": str(d)},
        dry_run=False,
        delete_source=False,
    )

    assert outcome == "migrated"
    assert store.put_dir_called is True
    execute.assert_not_called()  # nothing to sync, and the lookup itself failed


async def test_migrate_derived_row_delete_source_removes_dir(tmp_path, monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "DOWNLOAD_PATH", str(tmp_path))
    d = _make_thumb_dir(tmp_path)
    store = FakeDerivedStore()
    _patch_store(monkeypatch, store)
    monkeypatch.setattr(sm.db_engine, "fetch_one", AsyncMock(return_value=None))
    monkeypatch.setattr(sm.db_engine, "execute", AsyncMock(return_value=0))

    outcome = await sm._migrate_derived_row(
        {"resource_id": "100", "kind": "thumbnails", "dir": str(d)},
        dry_run=False,
        delete_source=True,
    )

    assert outcome == "migrated"
    assert not d.exists()


async def test_migrate_derived_row_count_mismatch_raises_before_mutation(
    tmp_path, monkeypatch
):
    from app.core.config import settings

    monkeypatch.setattr(settings, "DOWNLOAD_PATH", str(tmp_path))
    d = _make_thumb_dir(tmp_path)
    store = FakeDerivedStore(drop_last=True)
    _patch_store(monkeypatch, store)
    fetch_one = AsyncMock(return_value=None)
    monkeypatch.setattr(sm.db_engine, "fetch_one", fetch_one)
    execute = AsyncMock(return_value=0)
    monkeypatch.setattr(sm.db_engine, "execute", execute)

    with pytest.raises(RuntimeError, match="derived objects missing"):
        await sm._migrate_derived_row(
            {"resource_id": "100", "kind": "thumbnails", "dir": str(d)},
            dry_run=False,
            delete_source=True,
        )

    fetch_one.assert_not_called()
    execute.assert_not_called()
    assert d.exists()


async def test_migrate_derived_row_thumbnails_and_covers_share_prefix_no_false_mismatch(
    tmp_path, monkeypatch
):
    """fix round 1, Finding 2: derived_key_prefix(rid) is flat —
    ``derived/{rid}/`` — with no kind segment, so thumbnails/{rid}/ and
    covers/{rid}/ for the SAME resource_id land under the SAME prefix.
    _list_derived_rows produces them as two separate rows; migrating the
    second kind must not count the first kind's already-migrated objects as
    a mismatch just because list_prefix(prefix) now returns more objects
    than this row's own local file count."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "DOWNLOAD_PATH", str(tmp_path))
    thumb_dir = _make_thumb_dir(tmp_path, rid="100")
    cover_dir = tmp_path / "derived" / "covers" / "100"
    cover_dir.mkdir(parents=True)
    (cover_dir / "cover.png").write_bytes(b"cover-bytes")

    store = FakeDerivedStore()
    _patch_store(monkeypatch, store)
    monkeypatch.setattr(sm.db_engine, "fetch_one", AsyncMock(return_value=None))
    monkeypatch.setattr(sm.db_engine, "execute", AsyncMock(return_value=0))

    outcome_thumb = await sm._migrate_derived_row(
        {"resource_id": "100", "kind": "thumbnails", "dir": str(thumb_dir)},
        dry_run=False,
        delete_source=False,
    )
    # Second row for the SAME resource_id — by the time this runs,
    # list_prefix("derived/100/") already returns the first row's objects
    # too (shared prefix), which must not be mistaken for a mismatch here.
    outcome_cover = await sm._migrate_derived_row(
        {"resource_id": "100", "kind": "covers", "dir": str(cover_dir)},
        dry_run=False,
        delete_source=False,
    )

    assert outcome_thumb == "migrated"
    assert outcome_cover == "migrated"


async def test_migrate_derived_row_missing_dir_is_skipped(tmp_path, monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "DOWNLOAD_PATH", str(tmp_path))
    store = FakeDerivedStore()
    _patch_store(monkeypatch, store)

    outcome = await sm._migrate_derived_row(
        {
            "resource_id": "999",
            "kind": "thumbnails",
            "dir": str(tmp_path / "derived" / "thumbnails" / "999"),
        },
        dry_run=False,
        delete_source=False,
    )

    assert outcome == "missing"


# ── registry wiring / placeholder-callable guards ───────────────────────


def test_derived_module_registered_with_list_rows():
    cfg = sm._MODULES["derived"]
    assert cfg.list_rows is sm._list_derived_rows


def test_derived_extract_and_update_row_raise_if_ever_called():
    """These are unused placeholders (derived bypasses _migrate_row
    entirely) — they must fail loudly, not silently misbehave, if some
    future refactor routes 'derived' through the generic path by mistake."""
    with pytest.raises(RuntimeError, match="_migrate_derived_row"):
        sm._derived_extract({})


async def test_derived_update_row_raises_if_ever_called():
    with pytest.raises(RuntimeError, match="_migrate_derived_row"):
        await sm._derived_update_row({}, "sb://library/derived/1/x.jpg", None)
