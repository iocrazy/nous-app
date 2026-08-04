"""C2(discard-review): 图集 ZIP 导出必须支持 sb:// 前缀。

图集迁移对象存储后 ``download_path`` 是 sb:// 前缀,且本地工作副本已被
``discard_local_source`` 回收(见 fix/discard-local-after-s3-upload)。
``download_gallery_zip`` 原本整段是文件系统逻辑(``Path(base_path) /
download_path`` + glob 兜底),对 sb:// 完全零感知,回收后必然 404
"Gallery folder not found"。

本测试钉住:
- 已迁移图集(``_resolve_album_location`` 命中)→ 走 ObjectStore.list_prefix +
  get_bytes,与 media_slides_router 的 slides/ 子目录优先 + 排除封面规则
  完全一致地打包进 zip。
- 未迁移图集(resource 不存在 / 无 sb:// 前缀)→ 原文件系统逻辑逐行不变,
  行为零回归。
"""

from __future__ import annotations

import zipfile
from contextlib import asynccontextmanager, contextmanager
from io import BytesIO
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

pytestmark = pytest.mark.unit

PLATFORM_ID = "pid-gallery-1"
MEDIA_ID = 900
ALBUM_PREFIX = "sb://library/t5/album/900/"


def _patch_media_repo(video: dict):
    repo = MagicMock()
    repo.get_by_platform_id = AsyncMock(return_value=video)
    return patch("app.api.media_download_router.MediaRepository", return_value=repo)


@contextmanager
def _patch_db_fetch_one(row: dict | None):
    """Phase A raw-SQL-to-ORM migration (docs/decisions/2026-08-04-raw-sql-
    to-orm-full-migration.md): _resolve_album_location's direct query now
    runs as an ORM select() over app.db.session.read_scope() rather than
    app.db.engine.fetch_one. Both "no row" and a row with file_path=None
    collapse to the same None outcome, matching the legacy behaviour."""
    file_path = (row or {}).get("file_path") if row else None

    class _FakeScalars:
        def first(self):
            return file_path

    class _FakeResult:
        def scalars(self):
            return _FakeScalars()

    class _FakeSession:
        async def execute(self, _stmt):
            return _FakeResult()

    @asynccontextmanager
    async def _read_scope():
        yield _FakeSession()

    @asynccontextmanager
    async def _system_request_scope(reason: str):
        yield None

    with (
        patch("app.db.session.read_scope", new=_read_scope),
        patch("app.db.scope.system_request_scope", new=_system_request_scope),
    ):
        yield


async def _zip_names(streaming_response) -> list[str]:
    """Drain a StreamingResponse's BytesIO body and list zip member names."""
    body = streaming_response.body_iterator
    # Our body is a plain io.BytesIO passed to StreamingResponse — starlette
    # wraps it in an async iterator; read the underlying buffer directly
    # instead since the response was constructed from a BytesIO in-memory.
    chunks = []
    async for chunk in body:
        chunks.append(chunk)
    buf = BytesIO(b"".join(chunks))
    with zipfile.ZipFile(buf) as zf:
        return sorted(zf.namelist())


@pytest.mark.asyncio
async def test_gallery_zip_migrated_album_packs_slides_from_s3():
    from app.api.media_download_router import download_gallery_zip

    video = {"id": MEDIA_ID, "title": "gallery post", "download_path": ALBUM_PREFIX}
    keys = [
        "t5/album/900/slides/001.jpg",
        "t5/album/900/slides/002.jpg",
        "t5/album/900/cover.jpg",  # excluded (cover, not a slide)
        "t5/album/900/audio.mp3",  # excluded (not a slide extension)
    ]
    blobs = {
        "t5/album/900/slides/001.jpg": b"jpg-bytes-1",
        "t5/album/900/slides/002.jpg": b"jpg-bytes-2",
    }

    async def _get_bytes(key):
        return blobs[key]

    with (
        _patch_media_repo(video),
        _patch_db_fetch_one({"file_path": ALBUM_PREFIX}),
        patch(
            "app.services.library.media_storage.ObjectStore.list_prefix",
            new=AsyncMock(return_value=keys),
        ),
        patch(
            "app.services.library.media_storage.ObjectStore.get_bytes",
            new=AsyncMock(side_effect=_get_bytes),
        ),
    ):
        resp = await download_gallery_zip(PLATFORM_ID, auth=MagicMock())

    assert resp.media_type == "application/zip"
    assert "gallery post_gallery.zip" in resp.headers["Content-Disposition"]

    names = await _zip_names(resp)
    assert names == ["001.jpg", "002.jpg"]


@pytest.mark.asyncio
async def test_gallery_zip_migrated_album_prefers_slides_subdirectory():
    """slides/ 子目录存在时只取子目录内容,前缀根层的 cover/audio 不参与
    (与 list_slides 的分层规则一致)。"""
    from app.api.media_download_router import download_gallery_zip

    video = {"id": MEDIA_ID, "title": "gallery post", "download_path": ALBUM_PREFIX}
    keys = [
        "t5/album/900/cover.jpg",
        "t5/album/900/slides/002.jpg",
        "t5/album/900/slides/001.jpg",
    ]
    blobs = {
        "t5/album/900/slides/001.jpg": b"a",
        "t5/album/900/slides/002.jpg": b"b",
    }

    async def _get_bytes(key):
        return blobs[key]

    with (
        _patch_media_repo(video),
        _patch_db_fetch_one({"file_path": ALBUM_PREFIX}),
        patch(
            "app.services.library.media_storage.ObjectStore.list_prefix",
            new=AsyncMock(return_value=keys),
        ),
        patch(
            "app.services.library.media_storage.ObjectStore.get_bytes",
            new=AsyncMock(side_effect=_get_bytes),
        ),
    ):
        resp = await download_gallery_zip(PLATFORM_ID, auth=MagicMock())

    names = await _zip_names(resp)
    assert names == ["001.jpg", "002.jpg"]


@pytest.mark.asyncio
async def test_gallery_zip_migrated_album_skips_missing_object_but_keeps_others():
    """单个对象取不到(部分上传失败)时跳过它,而不是让整个 zip 失败。"""
    from app.api.media_download_router import download_gallery_zip

    video = {"id": MEDIA_ID, "title": "gallery post", "download_path": ALBUM_PREFIX}
    keys = [
        "t5/album/900/slides/001.jpg",
        "t5/album/900/slides/002.jpg",
    ]

    async def _get_bytes(key):
        if key.endswith("002.jpg"):
            raise RuntimeError("object not found")
        return b"jpg-bytes-1"

    with (
        _patch_media_repo(video),
        _patch_db_fetch_one({"file_path": ALBUM_PREFIX}),
        patch(
            "app.services.library.media_storage.ObjectStore.list_prefix",
            new=AsyncMock(return_value=keys),
        ),
        patch(
            "app.services.library.media_storage.ObjectStore.get_bytes",
            new=AsyncMock(side_effect=_get_bytes),
        ),
    ):
        resp = await download_gallery_zip(PLATFORM_ID, auth=MagicMock())

    names = await _zip_names(resp)
    assert names == ["001.jpg"]


@pytest.mark.asyncio
async def test_gallery_zip_migrated_album_all_objects_missing_is_404():
    from app.api.media_download_router import download_gallery_zip

    video = {"id": MEDIA_ID, "title": "gallery post", "download_path": ALBUM_PREFIX}
    keys = ["t5/album/900/slides/001.jpg"]

    async def _get_bytes(key):
        raise RuntimeError("object not found")

    with (
        _patch_media_repo(video),
        _patch_db_fetch_one({"file_path": ALBUM_PREFIX}),
        patch(
            "app.services.library.media_storage.ObjectStore.list_prefix",
            new=AsyncMock(return_value=keys),
        ),
        patch(
            "app.services.library.media_storage.ObjectStore.get_bytes",
            new=AsyncMock(side_effect=_get_bytes),
        ),
        pytest.raises(HTTPException) as exc_info,
    ):
        await download_gallery_zip(PLATFORM_ID, auth=MagicMock())

    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_gallery_zip_unmigrated_album_falls_back_to_filesystem(tmp_path):
    """未迁移图集(resource 不存在 / 无 sb:// 前缀)→ 原文件系统逻辑逐行不变。"""
    from app.api.media_download_router import download_gallery_zip

    download_path = "global/resources/web/douyin/901"
    slides_dir = tmp_path / download_path / "slides"
    slides_dir.mkdir(parents=True)
    (slides_dir / "001.jpg").write_bytes(b"fs-bytes-1")
    (slides_dir / "002.mp4").write_bytes(b"fs-bytes-2")
    (slides_dir / "notes.txt").write_bytes(b"skip-me")

    video = {"id": 901, "title": "fs gallery", "download_path": download_path}

    with (
        _patch_media_repo(video),
        _patch_db_fetch_one(None),
        patch(
            "app.api.media_download_router.Utils.get_download_base_path",
            return_value=str(tmp_path),
        ),
    ):
        resp = await download_gallery_zip(PLATFORM_ID, auth=MagicMock())

    names = await _zip_names(resp)
    assert names == ["001.jpg", "002.mp4"]


@pytest.mark.asyncio
async def test_gallery_zip_unmigrated_no_files_is_404(tmp_path):
    from app.api.media_download_router import download_gallery_zip

    download_path = "global/resources/web/douyin/902"
    (tmp_path / download_path).mkdir(parents=True)

    video = {"id": 902, "title": "empty gallery", "download_path": download_path}

    with (
        _patch_media_repo(video),
        _patch_db_fetch_one(None),
        patch(
            "app.api.media_download_router.Utils.get_download_base_path",
            return_value=str(tmp_path),
        ),
        pytest.raises(HTTPException) as exc_info,
    ):
        await download_gallery_zip(PLATFORM_ID, auth=MagicMock())

    assert exc_info.value.status_code == 404
