"""upload_postprocess_workflow body test.

Drives the real ``upload_postprocess_workflow`` body (``inspect.unwrap``
past the @DBOS.workflow decorator — same approach as test_parse_workflow_async
/ the transcode-thumbnail scope tests, since the decorator refuses to run
before DBOS.launch() but preserves the inner coroutine via @wraps).

The two @DBOS.step probes (metadata / thumbnail) construct their service
internally, so we patch the underlying service methods rather than the
steps — exactly the seams a future router-dispatch wiring will rely on:

  - ResourcesService._extract_video_metadata / _extract_image_metadata
  - ThumbnailService.generate_thumbnail
  - ResourcesRepository.update_resource / update_version / get_version_by_number
  - ResourcesService._trigger_transcode_async
  - get_task_manager (lifecycle: create / start / complete)

Cases:
  1. audio resource → _extract_video_metadata called, update_resource called
     with the probed metadata, thumbnail persisted, manager.complete called.
  2. _extract_video_metadata raises → workflow still completes (metadata
     phase swallowed), thumbnail phase still attempted.
  3. generate_thumbnail raises → workflow still completes.
"""

from __future__ import annotations

import inspect
from contextlib import contextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = pytest.mark.asyncio

_USER = "11111111-1111-1111-1111-111111111111"
_RID = "9000000000000000001"
_META = {"duration_seconds": 12, "resolution": "1080x1920"}


def _make_manager() -> MagicMock:
    """A task manager whose lifecycle calls are all awaitable no-ops."""
    mgr = MagicMock()
    mgr.create = AsyncMock(return_value="task-1")
    mgr.start = AsyncMock(return_value=None)
    mgr.complete = AsyncMock(return_value=None)
    mgr.fail = AsyncMock(return_value=None)
    return mgr


@contextmanager
def _patches(
    *,
    extract_video: AsyncMock,
    generate_thumbnail: AsyncMock,
    update_resource: AsyncMock,
    update_version: AsyncMock,
    get_version: AsyncMock,
    trigger_transcode: AsyncMock,
    manager: MagicMock,
):
    from app.repositories.resources_repository import ResourcesRepository
    from app.services.library.resources_service import ResourcesService
    from app.services.media.render.thumbnail_service import ThumbnailService

    with (
        patch.object(ResourcesService, "_extract_video_metadata", extract_video),
        patch.object(
            ResourcesService, "_extract_image_metadata", AsyncMock(return_value={})
        ),
        patch.object(ResourcesService, "_trigger_transcode_async", trigger_transcode),
        patch.object(ThumbnailService, "generate_thumbnail", generate_thumbnail),
        patch.object(ResourcesRepository, "update_resource", update_resource),
        patch.object(ResourcesRepository, "update_version", update_version),
        patch.object(ResourcesRepository, "get_version_by_number", get_version),
        patch(
            "app.services.infra.unified_task_manager.get_task_manager",
            return_value=manager,
        ),
    ):
        yield


def _body():
    from app.workflows import upload_postprocess as m

    return inspect.unwrap(m.upload_postprocess_workflow)


async def test_audio_resource_runs_metadata_thumbnail_and_completes():
    extract_video = AsyncMock(return_value=dict(_META))
    generate_thumbnail = AsyncMock(return_value="path/thumb.jpg")
    update_resource = AsyncMock(return_value={"id": _RID})
    update_version = AsyncMock(return_value={"id": "v1"})
    get_version = AsyncMock(return_value={"id": "v1"})
    trigger_transcode = AsyncMock(return_value=None)
    manager = _make_manager()

    with _patches(
        extract_video=extract_video,
        generate_thumbnail=generate_thumbnail,
        update_resource=update_resource,
        update_version=update_version,
        get_version=get_version,
        trigger_transcode=trigger_transcode,
        manager=manager,
    ):
        result = await _body()(
            resource_id=_RID,
            file_path="teams/t1/uploads/r1/v1/song.mp3",
            file_type="audio",
            mime_type="audio/mpeg",
            user_id=_USER,
        )

    assert result["status"] == "success"
    assert result["resource_id"] == _RID
    # metadata probe used the video/audio path and persisted to the resource.
    extract_video.assert_awaited_once()
    update_resource.assert_any_await(_RID, _META)
    # V1 version row got the same metadata.
    update_version.assert_awaited_once_with("v1", _META)
    # thumbnail generated + persisted onto the resource.
    generate_thumbnail.assert_awaited_once()
    update_resource.assert_any_await(_RID, {"thumbnail_path": "path/thumb.jpg"})
    # audio → no transcode dispatch.
    trigger_transcode.assert_not_awaited()
    # lifecycle driven through the manager API only.
    manager.create.assert_awaited_once()
    manager.start.assert_awaited_once()
    manager.complete.assert_awaited_once()


async def test_metadata_probe_raises_workflow_still_completes():
    extract_video = AsyncMock(side_effect=RuntimeError("ffprobe boom"))
    generate_thumbnail = AsyncMock(return_value="path/thumb.jpg")
    update_resource = AsyncMock(return_value={"id": _RID})
    update_version = AsyncMock(return_value={"id": "v1"})
    get_version = AsyncMock(return_value={"id": "v1"})
    trigger_transcode = AsyncMock(return_value=None)
    manager = _make_manager()

    with _patches(
        extract_video=extract_video,
        generate_thumbnail=generate_thumbnail,
        update_resource=update_resource,
        update_version=update_version,
        get_version=get_version,
        trigger_transcode=trigger_transcode,
        manager=manager,
    ):
        result = await _body()(
            resource_id=_RID,
            file_path="teams/t1/uploads/r1/v1/song.mp3",
            file_type="audio",
            mime_type="audio/mpeg",
            user_id=_USER,
        )

    assert result["status"] == "success"
    extract_video.assert_awaited_once()
    # metadata write skipped (probe raised) — but thumbnail phase still ran.
    generate_thumbnail.assert_awaited_once()
    update_resource.assert_any_await(_RID, {"thumbnail_path": "path/thumb.jpg"})
    manager.complete.assert_awaited_once()


async def test_thumbnail_raises_workflow_still_completes():
    extract_video = AsyncMock(return_value=dict(_META))
    generate_thumbnail = AsyncMock(side_effect=RuntimeError("pillow boom"))
    update_resource = AsyncMock(return_value={"id": _RID})
    update_version = AsyncMock(return_value={"id": "v1"})
    get_version = AsyncMock(return_value={"id": "v1"})
    trigger_transcode = AsyncMock(return_value=None)
    manager = _make_manager()

    with _patches(
        extract_video=extract_video,
        generate_thumbnail=generate_thumbnail,
        update_resource=update_resource,
        update_version=update_version,
        get_version=get_version,
        trigger_transcode=trigger_transcode,
        manager=manager,
    ):
        result = await _body()(
            resource_id=_RID,
            file_path="teams/t1/uploads/r1/v1/clip.mp4",
            file_type="audio",
            mime_type="audio/mpeg",
            user_id=_USER,
        )

    assert result["status"] == "success"
    # metadata still persisted before the thumbnail phase blew up.
    update_resource.assert_any_await(_RID, _META)
    generate_thumbnail.assert_awaited_once()
    manager.complete.assert_awaited_once()
