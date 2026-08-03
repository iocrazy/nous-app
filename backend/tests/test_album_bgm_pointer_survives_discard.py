"""C1(discard-review): 图集 BGM 指针必须在本地目录被回收之前求值。

``_upload_album_to_s3`` 成功后现在会 ``discard_local_source`` 掉本地图集
目录(slides/ + audio.mp3 + cover.jpg)。``download_images_by_platform_id``
原来在 upload 之后才检查 ``os.path.isfile(.../audio.mp3)`` 来决定要不要写
``music_download_path`` —— 那一刻目录已经被删,检查恒为 False,BGM 指针
永远漏写、永久指向一个已删除的 FS 路径(404)。

修复把该检查挪到 upload 调用之前(``has_audio`` 在 rmtree 之前快照),本测试
钉住这一点:即便 ``_upload_album_to_s3`` 的替身模拟真实实现在成功后
rmtree 掉本地目录,最终写回 parsed_media 的 ``pm_updates`` 仍然带着
``music_download_path``。
"""

import os
import shutil
from unittest.mock import AsyncMock

import pytest

from app.core.enums import DownloadStatus
from app.core.utils import Utils
from app.services.media.downloader.downloader import DownloaderService

MEDIA_ID = "700700700"
PLATFORM_ID = "pid-carousel-1"


@pytest.mark.asyncio
async def test_music_download_path_survives_post_upload_rmtree(monkeypatch, tmp_path):
    resource_dir_full = tmp_path / "global" / "resources" / "web" / "douyin" / MEDIA_ID

    # Utils.create_web_resource_path creates the dir eagerly on the real
    # base path (frontend_config.yml / DOWNLOAD_PATH lookup) — point it at
    # tmp_path instead of touching real machine config.
    monkeypatch.setattr(
        Utils, "get_download_base_path", classmethod(lambda cls: str(tmp_path))
    )

    video_data = {
        "id": MEDIA_ID,
        "title": "carousel post",
        "source_platform": "douyin",
        "video_download_urls": None,
        "image_download_urls": [["http://cdn.example/1.jpg"]],
        "music_play_urls": ["http://cdn.example/audio.mp3"],
    }

    updates: list[dict] = []

    class FakeMediaRepo:
        async def get_by_platform_id(self, pid):
            assert pid == PLATFORM_ID
            return video_data

        async def update(self, pid, data):
            updates.append(dict(data))
            return {}

    monkeypatch.setattr(
        "app.services.media.downloader.downloader.MediaRepository", FakeMediaRepo
    )
    monkeypatch.setattr(
        "app.services.media.downloader.downloader.log_user_action",
        AsyncMock(return_value=None),
    )

    async def fake_download_slide_item(
        index, url_list, slides_dir, is_video, headers, platform_id=None
    ):
        os.makedirs(slides_dir, exist_ok=True)
        with open(os.path.join(slides_dir, "001.jpg"), "wb") as f:
            f.write(b"slide-bytes")
        return {"success": True, "path": os.path.join(slides_dir, "001.jpg")}

    monkeypatch.setattr(
        DownloaderService,
        "download_slide_item",
        staticmethod(fake_download_slide_item),
    )

    async def fake_download_standalone_music(
        platform_id,
        music_play_urls,
        resource_dir_full,
        resource_dir_relative,
        headers,
        repo,
    ):
        # Mirrors real timing: audio.mp3 is written into the album dir
        # BEFORE the upload/discard step runs.
        with open(os.path.join(resource_dir_full, "audio.mp3"), "wb") as f:
            f.write(b"audio-bytes")
        return True

    monkeypatch.setattr(
        DownloaderService,
        "_download_standalone_music",
        staticmethod(fake_download_standalone_music),
    )

    async def fake_ensure_carousel_resource(
        *, media_id, user_id, platform_id, resource_dir_relative, video_data
    ):
        return "42"

    monkeypatch.setattr(
        DownloaderService,
        "_ensure_carousel_resource",
        staticmethod(fake_ensure_carousel_resource),
    )

    async def fake_upload_album_to_s3(
        *, user_id, resource_id, local_dir, relative_path
    ):
        # Real implementation: put_dir succeeds, then discard_local_source
        # rmtree's local_dir. Reproduce exactly that side effect.
        shutil.rmtree(local_dir, ignore_errors=True)
        return "sb://library/t5/album/42/"

    monkeypatch.setattr(
        "app.services.media.downloader.downloader._upload_album_to_s3",
        fake_upload_album_to_s3,
    )
    monkeypatch.setattr(
        "app.services.media.downloader.downloader._repoint_album_resource_version",
        AsyncMock(return_value=None),
    )

    class FakeResourcesRepository:
        async def update_resource(self, resource_id, data):
            return {}

    monkeypatch.setattr(
        "app.repositories.resources_repository.ResourcesRepository",
        FakeResourcesRepository,
    )

    result = await DownloaderService.download_images_by_platform_id(
        PLATFORM_ID, user_id="user-1"
    )

    assert result.video_download_status == DownloadStatus.COMPLETED
    # The local album dir really is gone (proves _upload_album_to_s3's fake
    # exercised the same destructive path the real helper does).
    assert not resource_dir_full.exists()

    pm_update = next(
        u for u in updates if u.get("download_path", "").startswith("sb://")
    )
    assert pm_update["music_download_path"] == "sb://library/t5/album/42/audio.mp3", (
        "music_download_path must be set from a pre-upload snapshot of "
        "audio.mp3's existence, not a post-rmtree check"
    )


@pytest.mark.asyncio
async def test_no_music_download_path_when_no_audio_was_downloaded(
    monkeypatch, tmp_path
):
    """负例:没有背景音乐时不该凭空写出 music_download_path。"""
    resource_dir_full = tmp_path / "global" / "resources" / "web" / "douyin" / MEDIA_ID

    monkeypatch.setattr(
        Utils, "get_download_base_path", classmethod(lambda cls: str(tmp_path))
    )

    video_data = {
        "id": MEDIA_ID,
        "title": "carousel post",
        "source_platform": "douyin",
        "video_download_urls": None,
        "image_download_urls": [["http://cdn.example/1.jpg"]],
        "music_play_urls": [],
    }

    updates: list[dict] = []

    class FakeMediaRepo:
        async def get_by_platform_id(self, pid):
            return video_data

        async def update(self, pid, data):
            updates.append(dict(data))
            return {}

    monkeypatch.setattr(
        "app.services.media.downloader.downloader.MediaRepository", FakeMediaRepo
    )
    monkeypatch.setattr(
        "app.services.media.downloader.downloader.log_user_action",
        AsyncMock(return_value=None),
    )

    async def fake_download_slide_item(
        index, url_list, slides_dir, is_video, headers, platform_id=None
    ):
        os.makedirs(slides_dir, exist_ok=True)
        with open(os.path.join(slides_dir, "001.jpg"), "wb") as f:
            f.write(b"slide-bytes")
        return {"success": True, "path": os.path.join(slides_dir, "001.jpg")}

    monkeypatch.setattr(
        DownloaderService,
        "download_slide_item",
        staticmethod(fake_download_slide_item),
    )

    async def fake_ensure_carousel_resource(
        *, media_id, user_id, platform_id, resource_dir_relative, video_data
    ):
        return "42"

    monkeypatch.setattr(
        DownloaderService,
        "_ensure_carousel_resource",
        staticmethod(fake_ensure_carousel_resource),
    )

    async def fake_upload_album_to_s3(
        *, user_id, resource_id, local_dir, relative_path
    ):
        shutil.rmtree(local_dir, ignore_errors=True)
        return "sb://library/t5/album/42/"

    monkeypatch.setattr(
        "app.services.media.downloader.downloader._upload_album_to_s3",
        fake_upload_album_to_s3,
    )
    monkeypatch.setattr(
        "app.services.media.downloader.downloader._repoint_album_resource_version",
        AsyncMock(return_value=None),
    )

    class FakeResourcesRepository:
        async def update_resource(self, resource_id, data):
            return {}

    monkeypatch.setattr(
        "app.repositories.resources_repository.ResourcesRepository",
        FakeResourcesRepository,
    )

    await DownloaderService.download_images_by_platform_id(
        PLATFORM_ID, user_id="user-1"
    )

    assert not resource_dir_full.exists()
    pm_update = next(
        u for u in updates if u.get("download_path", "").startswith("sb://")
    )
    assert "music_download_path" not in pm_update
