"""downloads module:web 下载迁 S3,图集展开为前缀形态。

2026-07-12 首次 dry-run 因把图集目录当文件处理,70/880 行 IsADirectoryError。
本 module 显式判 isdir,图集走前缀展开,视频走单对象。

The extract-only tests below (video vs. album classification) come straight
from the task brief. The ``_migrate_row``/``_migrate_album_row`` tests that
follow go further — they exercise the real 4-step safety-contract ordering
(verify-before-mutate, dry_run stops before mutation, delete only after DB
commit) end to end for the album path, the same way test_storage_migration.py
already does for the single-object path.
"""

from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from app.services.library import media_storage
from app.workflows import storage_migration as sm
from app.workflows.storage_migration import _downloads_extract

# NB: asyncio_mode = "auto" (pyproject.toml) auto-detects async def tests —
# no pytestmark needed, and applying one file-wide would wrongly tag the two
# sync extract-only tests above as asyncio tests (PytestWarning).


# ── FakeAlbumStore — mirrors test_storage_migration.py's FakeStore house
#    style, but implements put_dir/list_prefix instead of put_file/get_size
#    since the album path never content-addresses a single blob. ─────────


class FakeAlbumStore:
    bucket = "library"

    def __init__(self, *, drop_last: bool = False):
        self._keys: list[str] = []
        self.put_dir_called = False
        # Simulates a partial/aborted put_dir: one fewer object landed than
        # was walked locally — this is exactly what the count check must catch.
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


def _album_module_cfg(update_row=None):
    return sm.ModuleConfig(
        name="downloads",
        # Never executed by these tests (they only exercise extract()/
        # update_row() via _migrate_row) — a dummy callable satisfies
        # ModuleConfig's required field shape without needing a real
        # sqlalchemy.Select.
        select_stmt=lambda scope_id, limit: None,
        extract=_downloads_extract,
        update_row=update_row or AsyncMock(return_value=None),
    )


def test_video_file_is_single_object(tmp_path, monkeypatch):
    from app.core import config

    monkeypatch.setattr(config.settings, "DOWNLOAD_PATH", str(tmp_path))
    (tmp_path / "web").mkdir()
    (tmp_path / "web" / "video.mp4").write_bytes(b"x" * 16)
    ex = _downloads_extract(
        {
            "file_path": "web/video.mp4",
            "id": 1,
            "version_number": 1,
            "current_version": 1,
            "scope_id": 5,
        }
    )
    assert ex.is_album is False
    assert ex.scope_id == 5


def test_album_dir_is_prefix_form(tmp_path, monkeypatch):
    from app.core import config

    monkeypatch.setattr(config.settings, "DOWNLOAD_PATH", str(tmp_path))
    d = tmp_path / "album123"
    d.mkdir()
    (d / "7613_0.jpg").write_bytes(b"a")
    (d / "cover.jpg").write_bytes(b"b")
    ex = _downloads_extract(
        {
            "file_path": "album123",
            "id": 2,
            "version_number": 1,
            "current_version": 1,
            "scope_id": 5,
        }
    )
    assert ex.is_album is True


# ── _migrate_row / _migrate_album_row — end-to-end album path ──────────


def _make_album(tmp_path) -> Path:
    d = tmp_path / "album123"
    d.mkdir()
    (d / "7613_0.jpg").write_bytes(b"a")
    (d / "cover.jpg").write_bytes(b"b")
    return d


def _album_row(scope_id: int = 5) -> dict:
    return {
        "id": 2,
        "file_path": "album123",
        "version_number": 1,
        "current_version": 1,
        "scope_id": scope_id,
    }


async def test_migrate_album_row_dry_run_puts_but_never_mutates(tmp_path, monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "DOWNLOAD_PATH", str(tmp_path))
    _make_album(tmp_path)
    fake = FakeAlbumStore()
    monkeypatch.setattr(media_storage, "library_store", lambda: fake)
    update_row = AsyncMock(return_value=None)

    outcome = await sm._migrate_row(
        _album_row(), _album_module_cfg(update_row), dry_run=True, delete_source=True
    )

    assert outcome == "dry_run_ok"
    # PUT may happen in dry_run (dedup-safe, matches the single-object
    # contract) — but zero DB mutation and zero delete.
    assert fake.put_dir_called is True
    update_row.assert_not_called()
    assert (tmp_path / "album123").exists()


async def test_migrate_album_row_writes_prefix_file_path(tmp_path, monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "DOWNLOAD_PATH", str(tmp_path))
    _make_album(tmp_path)
    fake = FakeAlbumStore()
    monkeypatch.setattr(media_storage, "library_store", lambda: fake)
    update_row = AsyncMock(return_value=None)

    outcome = await sm._migrate_row(
        _album_row(scope_id=5),
        _album_module_cfg(update_row),
        dry_run=False,
        delete_source=False,
    )

    assert outcome == "migrated"
    update_row.assert_awaited_once()
    row_arg, file_path_arg, sha256_arg = update_row.call_args.args
    assert file_path_arg == "sb://library/t5/album/2/"
    assert sha256_arg is None  # no single content hash for a multi-file album
    # delete_source=False → local directory untouched
    assert (tmp_path / "album123").exists()


async def test_migrate_album_row_delete_source_removes_dir(tmp_path, monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "DOWNLOAD_PATH", str(tmp_path))
    _make_album(tmp_path)
    fake = FakeAlbumStore()
    monkeypatch.setattr(media_storage, "library_store", lambda: fake)

    outcome = await sm._migrate_row(
        _album_row(), _album_module_cfg(), dry_run=False, delete_source=True
    )

    assert outcome == "migrated"
    assert not (tmp_path / "album123").exists()


async def test_migrate_album_row_count_mismatch_raises_before_mutation(
    tmp_path, monkeypatch
):
    """A partial/aborted put_dir (fewer objects landed than walked locally)
    must raise BEFORE any DB mutation — same verify-before-mutate ordering
    the single-object path enforces via its size check."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "DOWNLOAD_PATH", str(tmp_path))
    _make_album(tmp_path)
    fake = FakeAlbumStore(drop_last=True)
    monkeypatch.setattr(media_storage, "library_store", lambda: fake)
    update_row = AsyncMock(return_value=None)

    with pytest.raises(RuntimeError, match="album file count mismatch"):
        await sm._migrate_row(
            _album_row(),
            _album_module_cfg(update_row),
            dry_run=False,
            delete_source=True,
        )

    update_row.assert_not_called()
    # Nothing deleted either — the raise happens before the delete_source step.
    assert (tmp_path / "album123").exists()
