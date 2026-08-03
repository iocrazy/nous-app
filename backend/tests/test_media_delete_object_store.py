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

2026-08-03 update (reference-safe deletion, spec
2026-08-03-reference-safe-object-deletion-design.md): a single (non-prefix)
sb:// object now goes through
``object_gc.delete_object_if_unreferenced`` instead of an unconditional
``ObjectStore.remove`` — a ``resources`` row can share the exact same S3
key (content addressing + the global download cache), and an unconditional
remove used to destroy that resource's file out from under it. Every test
here that expects an actual delete now patches
``app.services.library.object_gc.db_engine.fetch_one`` to return ``None``
(no other row references the key); ``test_sb_download_path_kept_when_still_referenced``
is the new regression guard for the opposite case.
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


def _patch_no_reference():
    """The reference-safe primitive's DB check: no other row references the
    key — the happy "go ahead and delete" path most tests below want."""
    return patch(
        "app.services.library.object_gc.db_engine.fetch_one",
        new=AsyncMock(return_value=None),
    )


@pytest.mark.asyncio
async def test_sb_download_path_deletes_object_store_object_not_local_path():
    """download_path is sb:// — ObjectStore.remove(key) is called (not local
    Path().unlink()), and the DB row is still deleted."""
    video = _video(download_path=SB_VIDEO_PATH)
    repo_patch, repo = _patch_repo(video)
    remove = AsyncMock()

    with (
        repo_patch,
        _patch_no_reference(),
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
async def test_sb_download_path_kept_when_still_referenced():
    """Regression guard for the 991-group over-deletion bug: a `resources`
    row still points at the same S3 key (measured 2026-08-03) — the object
    must NOT be removed even though the parsed_media row is being deleted.
    ObjectStore.remove must never be called; the DB row delete still runs."""
    video = _video(download_path=SB_VIDEO_PATH)
    repo_patch, repo = _patch_repo(video)
    remove = AsyncMock()
    # Another live row references the same raw sb:// value.
    fetch_one = AsyncMock(return_value={"?column?": 1})

    with (
        repo_patch,
        patch("app.services.library.object_gc.db_engine.fetch_one", new=fetch_one),
        patch("app.services.library.media_storage.ObjectStore.remove", new=remove),
    ):
        result = await delete_video(
            PLATFORM_ID, BackgroundTasks(), _auth(), delete_files=True
        )

    remove.assert_not_awaited()
    repo.delete.assert_awaited_once_with(PLATFORM_ID)
    assert result["success"] is True
    # Not recorded as deleted — the object survives for the referencing row.
    assert result["files_deleted"] == []


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
        _patch_no_reference(),
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
async def test_sb_album_prefix_deletes_via_remove_prefix_not_remove():
    """C2: an album's download_path is a PREFIX (trailing "/", e.g.
    ``t5/album/42/``) — the slides/audio/cover objects live UNDER it, not AT
    it. A plain ``remove(key)`` targets a key that was never PUT, so it
    "succeeds" while deleting nothing and the album stays on S3 forever
    (every migrated/newly-downloaded album silently survives a delete). The
    fix dispatches a prefix location through ``remove_prefix`` instead."""
    sb_album_path = "sb://library/331438215859255/album/42/"
    video = _video(download_path=sb_album_path)
    repo_patch, repo = _patch_repo(video)
    remove = AsyncMock()
    remove_prefix = AsyncMock(return_value=2)

    with (
        repo_patch,
        patch("app.services.library.media_storage.ObjectStore.remove", new=remove),
        patch(
            "app.services.library.media_storage.ObjectStore.remove_prefix",
            new=remove_prefix,
        ),
    ):
        result = await delete_video(
            PLATFORM_ID, BackgroundTasks(), _auth(), delete_files=True
        )

    remove_prefix.assert_awaited_once_with("331438215859255/album/42/")
    remove.assert_not_awaited()
    repo.delete.assert_awaited_once_with(PLATFORM_ID)
    assert any(
        f == "album prefix: 331438215859255/album/42/ (2 objects)"
        for f in result["files_deleted"]
    )


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
        _patch_no_reference(),
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
