"""hls module:把文件系统 HLS 目录迁到对象存储,复用 HlsPublisher。

HlsPublisher 的对象存储路径已在 PR2 验证(master.m3u8 最后上传的顺序
不变量、产物完整性)。本 module 只负责逐行调它 + 迁移前后的行级安全序
(verify-before-mutate / dry_run 先于任何写入 / DB UPDATE 先于删除本地)。

review round-1 fix: ``_hls_extract`` originally parsed rid/vid out of
``hls_path`` TEXT via regex, which only matches the
``derived/hls/{rid}/{vid}/`` layout. hls_path has a second, path-text-blind
layout (``{source.parent}/hls/`` — wherever the download pipeline put the
source file, see transcode_service.py:155-173) that has no rid/vid encoded
in it at all, and — since the download pipeline never had ObjectStore
support before this epic's Task 1 — is very likely the MAJORITY shape among
the 97 legacy rows. The regex silently raised (row counted "failed") on
that shape, which would have made the PR-4 dry-run count come up far short
of 97. Fixed to read resource_id/id straight off the row instead (see the
module-level comment in storage_migration.py). The tests below cover both
layouts to make sure this doesn't regress.

The ``_migrate_row``/``_migrate_hls_row`` tests that follow exercise the
real 4-step safety-contract ordering end to end, the same way
test_storage_migration_downloads.py already does for the album path.
HlsPublisher itself is stood in for by a fake (its object-store upload
behavior is PR2's concern, already covered there) so these tests stay
focused on what THIS module owns: dispatch, verify-before-mutate, and the
DB/delete side effects.
"""

from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from app.services.library import media_storage
from app.workflows import storage_migration as sm
from app.workflows.storage_migration import _hls_extract

# NB: asyncio_mode = "auto" (pyproject.toml) auto-detects async def tests —
# no pytestmark needed, and applying one file-wide would wrongly tag the
# sync extract-only tests as asyncio tests (PytestWarning).


def test_hls_extract_uses_row_resource_and_version_id():
    """rid/vid come from the row's resource_id/id columns, not from parsing
    hls_path text — the path here deliberately encodes a DIFFERENT "200"
    so a regression back to path-text parsing would be caught immediately
    (hls_vid must be "1", the row's own id, not "200")."""
    ex = _hls_extract(
        {
            "id": 1,
            "resource_id": 100,
            "version_number": 1,
            "hls_path": "derived/hls/100/200/master.m3u8",
        }
    )
    assert ex.hls_rid == "100"
    assert ex.hls_vid == "1"


def test_hls_extract_handles_download_pipeline_layout():
    """The download-pipeline hls_path layout (source.parent/hls/master.m3u8,
    e.g. under global/resources/web/...) has NO rid/vid encoded in the path
    at all. This is the layout most of the 97 legacy rows are actually in
    (download pipeline never had ObjectStore support before this epic) —
    extract must succeed via the row's own columns, not path parsing."""
    ex = _hls_extract(
        {
            "id": 55,
            "resource_id": 123,
            "version_number": 1,
            "hls_path": "global/resources/web/douyin/123/hls/master.m3u8",
        }
    )
    assert ex.hls_rid == "123"
    assert ex.hls_vid == "55"


def test_hls_extract_raises_on_missing_resource_or_version_id():
    """A row missing resource_id/id can't be safely addressed — this must
    fail loudly (counted "failed"), not silently skip or migrate to a
    bogus prefix."""
    with pytest.raises(RuntimeError, match="missing resource_id/id"):
        _hls_extract({"hls_path": "derived/hls/100/200/master.m3u8"})


# ── FakeHlsStore/FakeHlsPublisher — HlsPublisher's actual object-store
#    upload path is PR2's concern (already verified there); these fakes only
#    need to mimic "objects landed under the hls prefix" so this module's
#    own verify-before-mutate count check has something real to compare
#    against. ───────────────────────────────────────────────────────────────


class FakeHlsStore:
    bucket = "library"

    def __init__(self):
        self._keys: list[str] = []

    async def list_prefix(self, prefix: str) -> list[str]:
        return [k for k in self._keys if k.startswith(prefix)]


class FakeHlsPublisher:
    """Stand-in for ``HlsPublisher`` — records what a real ``publish()`` call
    would have landed in the store, optionally dropping one object to
    simulate a partial/aborted upload."""

    def __init__(self, store: FakeHlsStore, *, drop_last: bool = False):
        self._store = store
        self._drop_last = drop_last

    async def publish(
        self, hls_dir: Path, base: Path, resource_id: str, version_id: str
    ) -> str:
        files = sorted(p for p in Path(hls_dir).rglob("*") if p.is_file())
        prefix = f"hls/{resource_id}/{version_id}"
        keys = [f"{prefix}/{p.relative_to(hls_dir).as_posix()}" for p in files]
        if self._drop_last and keys:
            keys.pop()
        self._store._keys.extend(keys)
        return f"sb://library/{prefix}/master.m3u8"


def _hls_module_cfg(update_row=None):
    return sm.ModuleConfig(
        name="hls",
        select_sql="SELECT 1",
        extract=_hls_extract,
        update_row=update_row or AsyncMock(return_value=None),
    )


def _hls_row(rid: int = 100, vid: int = 200, scope_id: int = 5) -> dict:
    # ``id`` == ``vid``: for the derived/hls/{rid}/{vid}/ layout, the real
    # transcode code writes the directory using resource_id/version_id (the
    # row's own id) — see storage_migration.py's _hls_extract for why
    # extract now reads these off the row instead of the path text.
    return {
        "id": vid,
        "resource_id": rid,
        "version_number": 1,
        "hls_path": f"derived/hls/{rid}/{vid}/master.m3u8",
        "scope_id": scope_id,
    }


def _make_hls_dir(tmp_path: Path, rid: int = 100, vid: int = 200) -> Path:
    d = tmp_path / "derived" / "hls" / str(rid) / str(vid)
    d.mkdir(parents=True)
    (d / "master.m3u8").write_text("#EXTM3U\n")
    tier = d / "480p"
    tier.mkdir()
    (tier / "stream.m3u8").write_text("#EXTM3U\n")
    (tier / "seg0.ts").write_bytes(b"x" * 16)
    return d


def _patch_publisher(monkeypatch, store: FakeHlsStore, *, drop_last: bool = False):
    monkeypatch.setattr(media_storage, "library_store", lambda: store)
    monkeypatch.setattr(
        sm, "HlsPublisher", lambda: FakeHlsPublisher(store, drop_last=drop_last)
    )


async def test_migrate_hls_row_dry_run_publishes_but_never_mutates(
    tmp_path, monkeypatch
):
    from app.core.config import settings

    monkeypatch.setattr(settings, "DOWNLOAD_PATH", str(tmp_path))
    hls_dir = _make_hls_dir(tmp_path)
    store = FakeHlsStore()
    _patch_publisher(monkeypatch, store)
    update_row = AsyncMock(return_value=None)

    outcome = await sm._migrate_row(
        _hls_row(), _hls_module_cfg(update_row), dry_run=True, delete_source=True
    )

    assert outcome == "dry_run_ok"
    # Upload may happen in dry_run (dedup-safe, matches the single-object /
    # album contract) — but zero DB mutation and zero delete.
    update_row.assert_not_called()
    assert hls_dir.exists()


async def test_migrate_hls_row_writes_hls_path(tmp_path, monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "DOWNLOAD_PATH", str(tmp_path))
    _make_hls_dir(tmp_path)
    store = FakeHlsStore()
    _patch_publisher(monkeypatch, store)
    update_row = AsyncMock(return_value=None)

    outcome = await sm._migrate_row(
        _hls_row(), _hls_module_cfg(update_row), dry_run=False, delete_source=False
    )

    assert outcome == "migrated"
    update_row.assert_awaited_once()
    row_arg, file_path_arg, sha256_arg = update_row.call_args.args
    assert file_path_arg == "sb://library/hls/100/200/master.m3u8"
    assert sha256_arg is None  # no single content hash for a directory tree


async def test_migrate_hls_row_delete_source_removes_dir(tmp_path, monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "DOWNLOAD_PATH", str(tmp_path))
    hls_dir = _make_hls_dir(tmp_path)
    store = FakeHlsStore()
    _patch_publisher(monkeypatch, store)

    outcome = await sm._migrate_row(
        _hls_row(), _hls_module_cfg(), dry_run=False, delete_source=True
    )

    assert outcome == "migrated"
    assert not hls_dir.exists()


async def test_migrate_hls_row_count_mismatch_raises_before_mutation(
    tmp_path, monkeypatch
):
    """A partial/aborted publish (fewer objects landed than walked locally)
    must raise BEFORE any DB mutation — same verify-before-mutate ordering
    the single-object and album paths enforce."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "DOWNLOAD_PATH", str(tmp_path))
    hls_dir = _make_hls_dir(tmp_path)
    store = FakeHlsStore()
    _patch_publisher(monkeypatch, store, drop_last=True)
    update_row = AsyncMock(return_value=None)

    with pytest.raises(RuntimeError, match="HLS file count mismatch"):
        await sm._migrate_row(
            _hls_row(), _hls_module_cfg(update_row), dry_run=False, delete_source=True
        )

    update_row.assert_not_called()
    # Nothing deleted either — the raise happens before the delete_source step.
    assert hls_dir.exists()


async def test_migrate_hls_row_missing_local_dir_is_skipped(tmp_path, monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "DOWNLOAD_PATH", str(tmp_path))
    store = FakeHlsStore()
    _patch_publisher(monkeypatch, store)

    outcome = await sm._migrate_row(
        _hls_row(), _hls_module_cfg(), dry_run=False, delete_source=False
    )

    assert outcome == "missing"


async def test_migrate_hls_row_already_object_store_is_skipped(tmp_path, monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "DOWNLOAD_PATH", str(tmp_path))
    row = {
        "id": 4,
        "resource_id": 100,
        "version_number": 1,
        "hls_path": "sb://library/hls/100/200/master.m3u8",
        "scope_id": 5,
    }

    outcome = await sm._migrate_row(
        row, _hls_module_cfg(), dry_run=False, delete_source=False
    )

    assert outcome == "skipped"
