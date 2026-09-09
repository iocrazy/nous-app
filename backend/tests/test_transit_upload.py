"""``upload_transit_file`` — the ONE way a finished download leaves the transit
dir for the object store (2026-09-10).

The main downloader had this as a private helper; the soda (qishui) audio and
UGC-video workflows kept writing their finished files under DOWNLOAD_PATH and
persisting the relative path. Since 2026-09-07 that dir is local NVMe wiped on
deploy — a finished file left there is a lost file. Both workflows now go
through this helper for the media file AND the cover.
"""

from __future__ import annotations

import inspect

import pytest

from app.services.library import storage_flag
from app.services.library.transit_upload import upload_transit_file


class FakeStore:
    bucket = "library"

    def __init__(self):
        self.puts: list = []

    async def exists(self, key):
        return False

    async def put_file(self, key, path, mime):
        self.puts.append((key, path, mime))


@pytest.fixture
def _flag_on(monkeypatch):
    async def _on():
        return True

    monkeypatch.setattr(storage_flag, "unified_storage_enabled", _on)


@pytest.mark.asyncio
async def test_flag_off_returns_the_relative_path_and_keeps_the_file(
    monkeypatch, tmp_path
):
    async def _off():
        return False

    monkeypatch.setattr(storage_flag, "unified_storage_enabled", _off)
    f = tmp_path / "a.m4a"
    f.write_bytes(b"x")
    out = await upload_transit_file(
        user_id="u", local_path=str(f), relative_path="q/a.m4a", mime="audio/mp4"
    )
    assert out == "q/a.m4a" and f.exists()


@pytest.mark.asyncio
async def test_flag_on_uploads_discards_the_local_copy_and_returns_sb(
    _flag_on, monkeypatch, tmp_path
):
    from app.core.config import settings as app_settings
    from app.services.library import media_storage, resources_service

    async def _team(_uid):
        return "42"

    store = FakeStore()
    # discard_local_source refuses to delete anything outside DOWNLOAD_PATH —
    # the transit dir is the only place it may reclaim from.
    monkeypatch.setattr(app_settings, "DOWNLOAD_PATH", str(tmp_path))
    monkeypatch.setattr(resources_service, "_resolve_personal_team_id", _team)
    monkeypatch.setattr(media_storage, "library_store", lambda: store)
    f = tmp_path / "q" / "v.mp4"
    f.parent.mkdir()
    f.write_bytes(b"video")
    out = await upload_transit_file(
        user_id="u", local_path=str(f), relative_path="q/v.mp4", mime="video/mp4"
    )
    assert out.startswith("sb://library/t42/")
    assert len(store.puts) == 1 and store.puts[0][2] == "video/mp4"
    assert not f.exists()


@pytest.mark.asyncio
async def test_no_user_means_no_scope_means_filesystem(_flag_on, tmp_path):
    f = tmp_path / "a.mp3"
    f.write_bytes(b"x")
    out = await upload_transit_file(
        user_id=None, local_path=str(f), relative_path="a.mp3", mime="audio/mpeg"
    )
    assert out == "a.mp3" and f.exists()


def test_the_downloader_delegates_to_the_shared_helper():
    from app.services.media.downloader import downloader

    src = inspect.getsource(downloader._upload_downloaded_video_to_s3)
    assert "upload_transit_file(" in src


@pytest.mark.parametrize(
    "module_name", ["app.workflows.soda_download", "app.workflows.soda_ugc_download"]
)
def test_soda_workflows_upload_media_and_cover(module_name):
    """Media file and cover both leave the transit dir — two call sites each."""
    import importlib

    src = inspect.getsource(importlib.import_module(module_name))
    assert src.count("await upload_transit_file(") >= 2, module_name


def test_ugc_dedup_guard_trusts_an_object_store_path(tmp_path):
    """A row whose download_path is already sb:// was uploaded — there is no
    local file to find, and re-downloading it would be the bug."""
    from app.workflows.soda_ugc_download import already_downloaded

    assert (
        already_downloaded(
            {"download_path": "sb://library/t1/ab/cd/x.mp4"}, str(tmp_path)
        )
        is True
    )
