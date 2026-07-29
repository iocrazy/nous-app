"""check_global_cache_step._file_present — object-storage-aware L4 dedup
cache probe (PR-1 终审 fix #1).

Storage unification: Task1 made ``parsed_media.download_path`` an ``sb://``
object-store value once a video is migrated. The step's inner
``_file_present`` closure still treated every path as filesystem
(``os.path.join(DOWNLOAD_PATH, path)`` + ``os.path.exists``/``getsize``) —
``os.path.isabs("sb://...")`` is False, so it joined a garbage path and
always returned False. That makes ``cache_hit`` permanently False for every
S3-migrated video: L4 cross-user dedup silently stops working (source
re-download + re-upload on every request instead of reusing the shared
copy). This mirrors C7 (``assert_audio_present_step``'s sb:// existence
check): dispatch by backend, sb:// goes through ``ObjectStore``, filesystem
keeps the original ``os.path`` logic untouched (zero regression for
not-yet-migrated rows).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

pytestmark = pytest.mark.unit

SB_VIDEO_PATH = "sb://library/331438215859255/ab/cd/abcdef1234567890.mp4"
PLATFORM_ID = "7123456789"


def _media(**over):
    base = {
        "download_path": "",
        "cover_download_path": "",
        "video_download_status": "completed",
        "cover_download_status": "completed",
        "image_download_status": "completed",
    }
    base.update(over)
    return base


def _patch_repo(media: dict | None):
    repo = AsyncMock()
    repo.get_by_platform_id = AsyncMock(return_value=media)
    return patch(
        "app.repositories.media_repository.get_media_repository",
        lambda: repo,
    )


@pytest.mark.asyncio
async def test_sb_video_path_cache_hit_when_object_exists():
    """download_path is sb:// and the object exists in the store —
    ObjectStore.exists/get_size are queried (not os.path), cache_hit=True."""
    from app.workflows.download import check_global_cache_step

    media = _media(download_path=SB_VIDEO_PATH)
    exists = AsyncMock(return_value=True)
    get_size = AsyncMock(return_value=100)

    with (
        _patch_repo(media),
        patch("app.services.library.media_storage.ObjectStore.exists", new=exists),
        patch("app.services.library.media_storage.ObjectStore.get_size", new=get_size),
    ):
        result = await check_global_cache_step(
            platform_id=PLATFORM_ID,
            media_type=0,
            download_video=True,
            download_cover=False,
        )

    assert result == {"cache_hit": True}
    exists.assert_awaited_once()
    get_size.assert_awaited_once()


@pytest.mark.asyncio
async def test_sb_video_path_cache_miss_when_object_missing():
    """Object doesn't exist in the store — cache_hit=False, not an
    exception (dedup probe failure is treated as a cache miss so the
    workflow proceeds to re-download rather than crashing)."""
    from app.workflows.download import check_global_cache_step

    media = _media(download_path=SB_VIDEO_PATH)
    exists = AsyncMock(return_value=False)
    get_size = AsyncMock(side_effect=RuntimeError("no content-length"))

    with (
        _patch_repo(media),
        patch("app.services.library.media_storage.ObjectStore.exists", new=exists),
        patch("app.services.library.media_storage.ObjectStore.get_size", new=get_size),
    ):
        result = await check_global_cache_step(
            platform_id=PLATFORM_ID,
            media_type=0,
            download_video=True,
            download_cover=False,
        )

    assert result == {"cache_hit": False}


@pytest.mark.asyncio
async def test_sb_probe_exception_swallowed_as_cache_miss():
    """A raised exception during the sb:// probe (network hiccup, storage-api
    down) must not bubble up — swallowed to False so the workflow proceeds
    to re-download instead of failing the whole step."""
    from app.workflows.download import check_global_cache_step

    media = _media(download_path=SB_VIDEO_PATH)
    exists = AsyncMock(side_effect=RuntimeError("storage-api unreachable"))

    with (
        _patch_repo(media),
        patch("app.services.library.media_storage.ObjectStore.exists", new=exists),
    ):
        result = await check_global_cache_step(
            platform_id=PLATFORM_ID,
            media_type=0,
            download_video=True,
            download_cover=False,
        )

    assert result == {"cache_hit": False}


@pytest.mark.asyncio
async def test_filesystem_video_path_zero_regression(tmp_path, monkeypatch):
    """A plain filesystem-relative download_path that exists on disk still
    works exactly as before — the filesystem branch is untouched."""
    import app.core.config as config_module
    from app.workflows.download import check_global_cache_step

    rel = "global/resources/web/7123456789/video.mp4"
    real_file = tmp_path / rel
    real_file.parent.mkdir(parents=True, exist_ok=True)
    real_file.write_bytes(b"fake-mp4-bytes")

    monkeypatch.setattr(config_module.settings, "DOWNLOAD_PATH", str(tmp_path))

    media = _media(download_path=rel)

    with _patch_repo(media):
        result = await check_global_cache_step(
            platform_id=PLATFORM_ID,
            media_type=0,
            download_video=True,
            download_cover=False,
        )

    assert result == {"cache_hit": True}


@pytest.mark.asyncio
async def test_filesystem_video_path_missing_file_cache_miss(tmp_path, monkeypatch):
    """Filesystem row pointing at a file that isn't actually there (dev DB
    drift) still yields cache_hit=False — same as before the fix."""
    import app.core.config as config_module
    from app.workflows.download import check_global_cache_step

    monkeypatch.setattr(config_module.settings, "DOWNLOAD_PATH", str(tmp_path))

    media = _media(download_path="global/resources/web/does-not-exist.mp4")

    with _patch_repo(media):
        result = await check_global_cache_step(
            platform_id=PLATFORM_ID,
            media_type=0,
            download_video=True,
            download_cover=False,
        )

    assert result == {"cache_hit": False}


@pytest.mark.asyncio
async def test_no_media_row_cache_miss():
    with _patch_repo(None):
        from app.workflows.download import check_global_cache_step

        result = await check_global_cache_step(
            platform_id=PLATFORM_ID,
            media_type=0,
            download_video=True,
            download_cover=False,
        )
    assert result == {"cache_hit": False}
