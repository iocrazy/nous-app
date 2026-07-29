"""DELETE /media/{platform_id} — object-storage-aware file deletion (Task C5).

Before this fix, ``delete_video`` treated ``download_path`` /
``cover_download_path`` unconditionally as local filesystem paths
(``Path(download_path).unlink()``). After the sb:// storage migration, a
video's file can live in the object store — the local-path branch is a
no-op for it, so deleting a video leaves its S3 object behind forever
(storage cost leak).

This also covers a pre-existing bug in the filesystem branch: the old code
never joined ``Utils.get_download_base_path()`` onto ``download_path``
(unlike ``download_video_file`` which does ``Path(base_path) / rel``), so it
almost certainly deleted the wrong relative path unless CWD happened to
equal DOWNLOAD_PATH.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import BackgroundTasks

from app.api.media_router import delete_video
from app.core.deps import AuthContext

pytestmark = pytest.mark.unit

PLATFORM_ID = "7123456789"
SB_VIDEO_PATH = "sb://library/331438215859255/ab/cd/deadbeef1234.mp4"


def _auth() -> AuthContext:
    return AuthContext(user_id="u1", auth_type="jwt")


def _video(**over):
    base = {
        "id": "900",
        "title": "My Clip",
        "download_path": "",
        "cover_download_path": "",
    }
    base.update(over)
    return base


def _patch_repo(video, *, delete_result=True):
    repo = MagicMock()
    repo.get_by_platform_id = AsyncMock(return_value=video)
    repo.get_by_id = AsyncMock(return_value=video)
    delete = AsyncMock(return_value=delete_result)
    repo.delete = delete
    return patch("app.api.media_router.MediaRepository", return_value=repo), repo


@pytest.mark.asyncio
async def test_sb_download_path_deletes_object_store_object_not_local_path():
    """download_path is sb:// — ObjectStore.remove(key) is called (not local
    Path().unlink()), and the DB row is still deleted."""
    video = _video(download_path=SB_VIDEO_PATH)
    repo_patch, repo = _patch_repo(video)
    remove = AsyncMock()

    with (
        repo_patch,
        patch("app.services.library.media_storage.ObjectStore.remove", new=remove),
    ):
        result = await delete_video(
            PLATFORM_ID, BackgroundTasks(), _auth(), delete_files=True
        )

    remove.assert_awaited_once_with("331438215859255/ab/cd/deadbeef1234.mp4")
    repo.delete.assert_awaited_once_with(PLATFORM_ID)
    assert result["success"] is True
    assert any(
        f == "object: 331438215859255/ab/cd/deadbeef1234.mp4"
        for f in result["files_deleted"]
    )


@pytest.mark.asyncio
async def test_filesystem_download_path_joins_base_path_before_unlink(tmp_path):
    """A plain filesystem-relative download_path must be resolved against
    Utils.get_download_base_path() before unlinking — covers the pre-existing
    base_path-join bug (old code deleted `Path(download_path)` directly)."""
    rel = "global/videos/7123456789/clip.mp4"
    real_file = tmp_path / rel
    real_file.parent.mkdir(parents=True, exist_ok=True)
    real_file.write_bytes(b"fake-mp4")

    video = _video(download_path=rel)
    repo_patch, repo = _patch_repo(video)

    with (
        repo_patch,
        patch(
            "app.api.media_router.Utils.get_download_base_path",
            return_value=str(tmp_path),
        ),
    ):
        result = await delete_video(
            PLATFORM_ID, BackgroundTasks(), _auth(), delete_files=True
        )

    assert not real_file.exists()
    repo.delete.assert_awaited_once_with(PLATFORM_ID)
    assert any(f.endswith("clip.mp4") for f in result["files_deleted"])


@pytest.mark.asyncio
async def test_object_store_remove_failure_does_not_500_db_delete_still_runs():
    """ObjectStore.remove raising must not bubble into a 500 — the delete
    endpoint should log a warning and still delete the DB record (object
    leaks are recoverable via retry; a stuck DB row is worse)."""
    video = _video(download_path=SB_VIDEO_PATH)
    repo_patch, repo = _patch_repo(video)
    remove = AsyncMock(side_effect=RuntimeError("storage-api unreachable"))

    with (
        repo_patch,
        patch("app.services.library.media_storage.ObjectStore.remove", new=remove),
    ):
        result = await delete_video(
            PLATFORM_ID, BackgroundTasks(), _auth(), delete_files=True
        )

    remove.assert_awaited_once()
    repo.delete.assert_awaited_once_with(PLATFORM_ID)
    assert result["success"] is True
    # Failed object delete must not be recorded as if it succeeded.
    assert result["files_deleted"] == []


@pytest.mark.asyncio
async def test_sb_cover_path_deletes_object_store_object():
    """cover_download_path is sb:// too — same object-store dispatch applies
    to the cover, not just the main download_path."""
    sb_cover = "sb://library/331438215859255/ab/cd/cover.jpg"
    video = _video(cover_download_path=sb_cover)
    repo_patch, repo = _patch_repo(video)
    remove = AsyncMock()

    with (
        repo_patch,
        patch("app.services.library.media_storage.ObjectStore.remove", new=remove),
    ):
        result = await delete_video(
            PLATFORM_ID, BackgroundTasks(), _auth(), delete_files=True
        )

    remove.assert_awaited_once_with("331438215859255/ab/cd/cover.jpg")
    repo.delete.assert_awaited_once_with(PLATFORM_ID)
    assert any(
        f == "object: 331438215859255/ab/cd/cover.jpg" for f in result["files_deleted"]
    )
