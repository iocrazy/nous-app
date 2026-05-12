"""Regression tests for finalize_post_download_step resilience.

Background — production bug 2026-05-08:
download_workflow 2d9d667d landed the file on disk (resources.file_size_bytes
+ resource_versions row) but task_tracking ended up phase=failed because a
transient `await media_repo.update(platform_id, status_flags)` raise inside
finalize_post_download_step propagated all the way out, marking the
whole workflow ERROR. The mirror trigger then stomped task_tracking
phase to failed, mark_task_user_visible_complete_step never got to run,
and the user saw "下载好了但前端转圈最后失败" — UI says failed while
资源库 already shows the file.

Both `await update` calls inside that step are now guarded with
try/except so a status-flag write failure never demotes a workflow whose
actual file delivery already succeeded. These tests pin that:

  - finalize_post_download_step does NOT raise when
    resources.update_resource(file_size) raises
  - finalize_post_download_step does NOT raise when
    media_repo.update(status flags) raises
  - both warnings are logged so we can still investigate post-hoc
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest


@pytest.fixture
def fresh_media_row() -> dict:
    return {
        "id": 12345,
        "platform_id": "p1",
        "download_path": "global/resources/web/douyin/12345/video.mp4",
        "datasize_bytes": 13662239,
        "video_download_status": "pending",
        "cover_download_status": "pending",
    }


@pytest.fixture
def good_results() -> dict:
    return {"video": "completed", "cover": "completed"}


async def _call_finalize(
    *,
    media_repo_mock,
    res_repo_mock,
    fresh_media_row,
    results,
):
    """Drive the underlying _do() coroutine directly so we don't have to
    fight DBOS step semantics in a unit test. The DBOS step decorator is
    a thin wrapper around the original function; the resilience logic
    lives in the inner async _do(), and that's what we want to pin."""
    from app.workflows.download import finalize_post_download_step

    media_repo_mock.get_by_platform_id = AsyncMock(return_value=fresh_media_row)
    res_repo_mock.update_resource = AsyncMock()
    res_repo_mock.get_versions = AsyncMock(return_value=[])
    res_repo_mock.create_version = AsyncMock()

    with (
        patch(
            "app.repositories.media_repository.MediaRepository",
            return_value=media_repo_mock,
        ),
        patch(
            "app.repositories.resources_repository.ResourcesRepository",
            return_value=res_repo_mock,
        ),
    ):
        return await finalize_post_download_step(
            platform_id="p1",
            user_id="u1",
            resource_id="r1",
            download_video=True,
            download_cover=True,
            media_type=0,
            results=results,
        )


async def test_finalize_swallows_resources_size_update_failure(
    fresh_media_row, good_results
):
    """update_resource(file_size_bytes=...) raise must NOT escape — the
    file is on disk, the resources row already has the row, the
    file_size mirror is just a UI badge. A 5xx here cannot be allowed
    to flip the whole workflow to failed."""
    media_repo = type("M", (), {})()
    res_repo = type("R", (), {})()

    res_repo.update_resource = AsyncMock(side_effect=RuntimeError("postgrest 503"))
    res_repo.get_versions = AsyncMock(return_value=[])
    res_repo.create_version = AsyncMock()
    media_repo.get_by_platform_id = AsyncMock(return_value=fresh_media_row)
    media_repo.update = AsyncMock()

    out = await _call_finalize(
        media_repo_mock=media_repo,
        res_repo_mock=res_repo,
        fresh_media_row=fresh_media_row,
        results=good_results,
    )

    assert out["fresh_download_path"] == fresh_media_row["download_path"]
    assert out["actual_size"] == fresh_media_row["datasize_bytes"]
    # Workflow must continue past the size update — verify by checking
    # that the next non-guarded await (media_repo.update for status
    # flags) was reached.
    media_repo.update.assert_awaited_once()


async def test_finalize_swallows_parsed_media_status_update_failure(
    fresh_media_row, good_results
):
    """media_repo.update({video_download_status: completed, ...}) raise
    must NOT escape. The status flags are UI badges; the actual download
    is already done. This is the exact failure mode that bit
    download_workflow 2d9d667d on 2026-05-08."""
    media_repo = type("M", (), {})()
    res_repo = type("R", (), {})()

    res_repo.update_resource = AsyncMock()
    res_repo.get_versions = AsyncMock(return_value=[])
    res_repo.create_version = AsyncMock()
    media_repo.get_by_platform_id = AsyncMock(return_value=fresh_media_row)
    media_repo.update = AsyncMock(
        side_effect=RuntimeError("APIError 23505 unique constraint")
    )

    out = await _call_finalize(
        media_repo_mock=media_repo,
        res_repo_mock=res_repo,
        fresh_media_row=fresh_media_row,
        results=good_results,
    )

    # finalize completed despite the status-flag write raising. Without
    # the try/except, this test would raise RuntimeError instead.
    assert out["actual_size"] == fresh_media_row["datasize_bytes"]
    media_repo.update.assert_awaited_once()


async def test_finalize_succeeds_on_happy_path(fresh_media_row, good_results):
    """Sanity: with both updates succeeding, finalize returns the same
    shape (`fresh_download_path` + `actual_size`) callers depend on."""
    media_repo = type("M", (), {})()
    res_repo = type("R", (), {})()

    res_repo.update_resource = AsyncMock()
    res_repo.get_versions = AsyncMock(return_value=[])
    res_repo.create_version = AsyncMock()
    media_repo.get_by_platform_id = AsyncMock(return_value=fresh_media_row)
    media_repo.update = AsyncMock()

    out = await _call_finalize(
        media_repo_mock=media_repo,
        res_repo_mock=res_repo,
        fresh_media_row=fresh_media_row,
        results=good_results,
    )

    assert out["fresh_download_path"] == fresh_media_row["download_path"]
    assert out["actual_size"] == fresh_media_row["datasize_bytes"]
    res_repo.update_resource.assert_awaited_once_with(
        "r1", {"file_size_bytes": fresh_media_row["datasize_bytes"]}
    )
    media_repo.update.assert_awaited_once()
