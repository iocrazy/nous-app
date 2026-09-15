"""Entry-point "already in my library" short circuit.

`check_global_cache_step` has always known when the bytes are already on
shared storage — but it runs INSIDE `download_workflow`, i.e. after
`dedup_and_dispatch` has created the `task_tracking` row and enqueued the
workflow. Net effect for the user: re-submitting a video they downloaded a
week ago still pops a "Download" card in Task Center, which then completes in
well under a second with a "(cache hit)" subtitle. Correct bookkeeping,
wrong user-visible behaviour — they asked for detect-then-skip.

These tests pin the predicate (`already_in_user_library`) and the one
property that matters at the dispatch site: when it says yes, NOTHING is
created — no dedup registration, no task row, no workflow.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = pytest.mark.unit

PLATFORM_ID = "bilibili_BV1nUtj6QEuS"
USER_ID = "8e1584e3-9c29-4a5b-90fe-125b74259f7f"
RESOURCE_ID = "347189469292635"


def _patch_cache(hit: bool):
    return patch(
        "app.services.media.download_cache.global_cache_hit",
        new=AsyncMock(return_value=hit),
    )


def _patch_versions(versions: list[dict]):
    repo = MagicMock()
    repo.get_versions = AsyncMock(return_value=versions)
    return patch(
        "app.repositories.resources_repository.get_resources_repository",
        lambda: repo,
    )


# ── predicate ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_no_user_resource_means_dispatch():
    """No resources row for this user — the download links their copy."""
    from app.services.media.download_cache import already_in_user_library

    with _patch_cache(True):
        assert not await already_in_user_library(
            platform_id=PLATFORM_ID,
            media_type=0,
            download_video=True,
            download_cover=False,
            resource_id=None,
            user_id=USER_ID,
        )


@pytest.mark.asyncio
async def test_cache_miss_means_dispatch():
    from app.services.media.download_cache import already_in_user_library

    with _patch_cache(False), _patch_versions([{"id": "1"}]):
        assert not await already_in_user_library(
            platform_id=PLATFORM_ID,
            media_type=0,
            download_video=True,
            download_cover=False,
            resource_id=RESOURCE_ID,
            user_id=USER_ID,
        )


@pytest.mark.asyncio
async def test_cached_but_unlinked_copy_means_dispatch():
    """Shared bytes exist but this user's resource has no version row — an
    empty shell. Linking it is real work the workflow's cache-hit branch
    does, so the dispatch must still happen."""
    from app.services.media.download_cache import already_in_user_library

    with _patch_cache(True), _patch_versions([]):
        assert not await already_in_user_library(
            platform_id=PLATFORM_ID,
            media_type=0,
            download_video=True,
            download_cover=False,
            resource_id=RESOURCE_ID,
            user_id=USER_ID,
        )


@pytest.mark.asyncio
async def test_cached_and_linked_means_skip():
    from app.services.media.download_cache import already_in_user_library

    with _patch_cache(True), _patch_versions([{"id": "1", "version_number": 1}]):
        assert await already_in_user_library(
            platform_id=PLATFORM_ID,
            media_type=0,
            download_video=True,
            download_cover=False,
            resource_id=RESOURCE_ID,
            user_id=USER_ID,
        )


@pytest.mark.asyncio
async def test_version_probe_failure_fails_closed():
    """A broken probe must never silently swallow a download."""
    from app.services.media.download_cache import user_copy_linked

    boom = MagicMock()
    boom.get_versions = AsyncMock(side_effect=RuntimeError("pg down"))
    with patch(
        "app.repositories.resources_repository.get_resources_repository",
        lambda: boom,
    ):
        assert not await user_copy_linked(resource_id=RESOURCE_ID, user_id=USER_ID)


# ── dispatch site ────────────────────────────────────────────────────────


def _orchestrator_spy():
    orch = MagicMock()
    orch.acquire_or_subscribe = AsyncMock(return_value={"action": "created"})
    orch.create_flow = AsyncMock(return_value="flow-1")
    orch.create = AsyncMock(return_value="task-1")
    return orch


@pytest.mark.asyncio
async def test_dispatch_creates_nothing_when_already_owned():
    from app.api.media_fetch_helpers import dedup_and_dispatch

    orch = _orchestrator_spy()
    start = AsyncMock()

    with (
        patch(
            "app.services.media.download_cache.already_in_user_library",
            new=AsyncMock(return_value=True),
        ),
        patch(
            "app.services.infra.unified_task_manager.get_task_manager",
            lambda: orch,
        ),
        patch("app.services.infra.dbos_orchestrator.start_workflow_routed", new=start),
    ):
        result = await dedup_and_dispatch(
            platform_id=PLATFORM_ID,
            user_id=USER_ID,
            resource_id=RESOURCE_ID,
            media_type=0,
            video_title="Krea2",
            download_video=True,
            download_cover=True,
        )

    assert result["already_in_library"] is True
    assert result["task_id"] is None
    assert result["types_submitted"] == []
    assert sorted(result["types_skipped"]) == ["cover", "video"]
    # The point of the fix: no dedup registration, no task row, no workflow.
    orch.acquire_or_subscribe.assert_not_awaited()
    orch.create.assert_not_awaited()
    orch.create_flow.assert_not_awaited()
    start.assert_not_awaited()


@pytest.mark.asyncio
async def test_dispatch_proceeds_when_precheck_raises():
    """The probe is an optimisation, never a gate: if it blows up, the
    download still goes out."""
    from app.api.media_fetch_helpers import dedup_and_dispatch

    orch = _orchestrator_spy()
    start = AsyncMock()

    with (
        patch(
            "app.services.media.download_cache.already_in_user_library",
            new=AsyncMock(side_effect=RuntimeError("probe exploded")),
        ),
        patch(
            "app.services.infra.unified_task_manager.get_task_manager",
            lambda: orch,
        ),
        patch("app.services.infra.dbos_orchestrator.start_workflow_routed", new=start),
    ):
        result = await dedup_and_dispatch(
            platform_id=PLATFORM_ID,
            user_id=USER_ID,
            resource_id=RESOURCE_ID,
            media_type=0,
            video_title="Krea2",
            download_video=True,
            download_cover=False,
        )

    assert result["already_in_library"] is False
    assert result["types_submitted"] == ["video"]
    start.assert_awaited_once()
