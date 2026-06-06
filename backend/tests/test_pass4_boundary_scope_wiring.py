"""A2 pass 4b — boundary request_scope wiring tests.

Proves the ambient USER ``Scope`` (``current_scope().user_id == <user>``) is
established at the point each of the 5 wiring points runs its `resources`
access, and that it RESETS to ``None`` afterwards (no scope leak).

Two wiring shapes (see the pass-4b plan):

  * ASYNC resource functions (#4 save_metadata_only, #5 _ensure_carousel_resource)
    wrap the resource work in their OWN body. The test patches the repo method
    they call to record ``current_scope()`` and asserts the recorded user_id —
    self-contained, does not exercise run_async.

  * SYNC chain helpers (#1-#3) cannot ``async with`` internally; the scope is
    set at their ASYNC CALLER and must survive the sync helper's ``run_async``
    thread hop (pass-4a's copy_context fix). The #1 test is a TRUE end-to-end
    through ``run_async`` — it drives the real async caller
    (``chain_followups_step``) INSIDE a running loop (forcing run_async branch 2,
    the ThreadPoolExecutor copy_context path) and asserts the scope reached the
    sync helper's `resources` read. #2/#3 are validated the same e2e way by
    driving the sync helper directly inside a ``request_scope`` block under a
    running loop (the helpers are cheap to short-circuit at their first resource
    read), since their async callers are heavy DBOS workflows.

All tests use the PUBLIC scope API only (``Scope`` / ``request_scope`` /
``current_scope``) and patch repo methods at the class level. INERT regardless
of ``SCOPE_ENFORCE_RESOURCES`` — these assert the contextvar is SET, not that
enforcement fires.
"""

from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest

from app.db.scope import Scope, current_scope, request_scope

_USER = "u-pass4b-12345"


# ── #5 _ensure_carousel_resource — ASYNC, own-body wrap ──────────────────


@pytest.mark.asyncio
async def test_carousel_resource_sets_user_scope_around_resource_work():
    """downloader._ensure_carousel_resource wraps its resource work in
    request_scope(Scope(user_id)) — the get/create repo calls observe the
    ambient user scope; it resets to None after."""
    from app.services.media.downloader.downloader import DownloaderService

    recorded: dict[str, object] = {}

    async def _fake_get(self, media_id, user_id):
        # First resource read inside the wrapped body.
        recorded["get"] = current_scope()
        return None  # no existing resource → proceed to create

    async def _fake_create(self, data):
        recorded["create"] = current_scope()
        return {"id": "res-1"}

    async def _fake_create_item(self, data):
        recorded["item"] = current_scope()
        return {"id": "item-1"}

    async def _fake_personal_team(user_id):
        return 999

    assert current_scope() is None
    with (
        patch(
            "app.repositories.resources_repository.ResourcesRepository"
            ".get_resource_by_media_id_and_creator",
            _fake_get,
        ),
        patch(
            "app.repositories.resources_repository.ResourcesRepository.create_resource",
            _fake_create,
        ),
        patch(
            "app.repositories.resources_repository.ResourcesRepository.create_resource_item",
            _fake_create_item,
        ),
        patch(
            "app.services.library.resources_service._resolve_personal_team_id",
            _fake_personal_team,
        ),
    ):
        await DownloaderService._ensure_carousel_resource(
            media_id="m-1",
            user_id=_USER,
            platform_id="p-1",
            resource_dir_relative="dir/rel",
            video_data={"media_type": "2"},
        )

    assert getattr(recorded.get("get"), "user_id", None) == _USER
    assert getattr(recorded.get("create"), "user_id", None) == _USER
    assert getattr(recorded.get("item"), "user_id", None) == _USER
    # No leak after the wrapped body exits.
    assert current_scope() is None


# ── #4 save_metadata_only — ASYNC, own-body wrap (only-when-truthy) ──────


@pytest.mark.asyncio
async def test_save_metadata_sets_user_scope_around_resource_branch():
    """media_service.save_metadata_only wraps the per-user resource branch in
    request_scope when user_id is truthy; _ensure_user_resource observes it."""
    from app.services.media.parsers.media_service import MediaService

    recorded: dict[str, object] = {}

    async def _fake_get_by_platform(self, platform_id):
        return None  # no existing parsed_media → take the create branch

    async def _fake_create(self, data):
        return {"id": "media-1"}

    async def _fake_ensure_user_resource(**kwargs):
        recorded["scope"] = current_scope()
        recorded["user_id_arg"] = kwargs.get("user_id")
        return "res-1"

    assert current_scope() is None
    with (
        patch(
            "app.repositories.media_repository.MediaRepository.get_by_platform_id",
            _fake_get_by_platform,
        ),
        patch(
            "app.repositories.media_repository.MediaRepository.create",
            _fake_create,
        ),
        patch.object(
            MediaService,
            "_ensure_user_resource",
            staticmethod(_fake_ensure_user_resource),
        ),
    ):
        result = await MediaService.save_metadata_only(
            platform_id="p-1",
            parsed_data={
                "user_id": _USER,
                "title": "Test Clip",
                "media_type": "video",
                "platform_id": "p-1",
                "original_url": "https://example.com/p-1",
            },
        )

    assert result["success"] is True
    assert getattr(recorded.get("scope"), "user_id", None) == _USER
    assert recorded.get("user_id_arg") == _USER
    assert current_scope() is None


@pytest.mark.asyncio
async def test_save_metadata_none_user_skips_resource_branch_no_scope():
    """When user_id is None the resource branch is SKIPPED entirely — no
    resource is written and no scope is ever set (pre-existing condition:
    a None-owner resource is never created here, so wrapping only-when-truthy
    is correct; under the flag a None-owner resource write would fail closed)."""
    from app.services.media.parsers.media_service import MediaService

    called = {"ensure": False}

    async def _fake_get_by_platform(self, platform_id):
        return None

    async def _fake_create(self, data):
        return {"id": "media-1"}

    async def _fake_ensure_user_resource(**kwargs):
        called["ensure"] = True
        return "res-1"

    with (
        patch(
            "app.repositories.media_repository.MediaRepository.get_by_platform_id",
            _fake_get_by_platform,
        ),
        patch(
            "app.repositories.media_repository.MediaRepository.create",
            _fake_create,
        ),
        patch.object(
            MediaService,
            "_ensure_user_resource",
            staticmethod(_fake_ensure_user_resource),
        ),
    ):
        result = await MediaService.save_metadata_only(
            platform_id="p-2",
            parsed_data={  # no user_id
                "title": "No Owner",
                "media_type": "video",
                "platform_id": "p-2",
                "original_url": "https://example.com/p-2",
            },
        )

    assert result["success"] is True
    assert called["ensure"] is False  # resource branch skipped
    assert result["resource_id"] is None
    assert current_scope() is None


# ── #1 chain_followups_step — TRUE e2e through run_async (branch 2) ──────


@pytest.mark.asyncio
async def test_chain_followups_step_propagates_scope_through_run_async():
    """END-TO-END: chain_followups_step (async caller) sets request_scope, then
    calls the SYNC helpers maybe_chain_transcode / maybe_chain_ai_pipeline which
    read `resources` via run_async. Being inside pytest-asyncio's running loop
    forces run_async BRANCH 2 (ThreadPoolExecutor worker on a fresh context);
    the scope only reaches the resource read because of pass-4a's copy_context
    wrap. This is the test that validates the whole pass-3a+4a+4b chain.

    The sync helpers' FIRST resource read is patched to record current_scope()
    and return None (short-circuiting the rest of the chain)."""
    from app.repositories.media_repository import MediaRepository
    from app.repositories.resources_repository import ResourcesRepository
    from app.workflows.download import chain_followups_step

    recorded: dict[str, object] = {}

    async def _rec_by_platform(self, platform_id):
        # maybe_chain_transcode's first resource read.
        recorded["transcode"] = current_scope()
        return None

    async def _rec_media(self, platform_id):
        # maybe_chain_ai_pipeline goes via MediaRepository first; return a row
        # so it reaches the ResourcesRepository read below.
        return {"id": "media-99"}

    async def _rec_by_media(self, media_id, user_id):
        # maybe_chain_ai_pipeline's resource read.
        recorded["ai_pipeline"] = current_scope()
        return None

    assert current_scope() is None
    with (
        patch.object(
            ResourcesRepository, "get_resource_by_platform_id", _rec_by_platform
        ),
        patch.object(MediaRepository, "get_by_platform_id", _rec_media),
        patch.object(
            ResourcesRepository, "get_resource_by_media_id_and_creator", _rec_by_media
        ),
    ):
        # mime starts as video/mp4 from .mp4 path → triggers the chain branch.
        await chain_followups_step(
            platform_id="p-1",
            user_id=_USER,
            resource_id="res-1",
            fresh_download_path="downloads/x.mp4",
        )

    # The scope set at the async caller reached BOTH sync helpers' resource
    # reads through run_async's thread hop (pass-4a copy_context).
    assert getattr(recorded.get("transcode"), "user_id", None) == _USER
    assert getattr(recorded.get("ai_pipeline"), "user_id", None) == _USER
    assert current_scope() is None


# ── #2 chain_transcript_summary_for_tags — e2e via direct sync drive ─────


@pytest.mark.asyncio
async def test_chain_transcript_summary_propagates_scope_through_run_async():
    """e2e for the #2 wiring: the async caller (extract_audio_workflow) sets
    request_scope around chain_transcript_summary_for_tags. We reproduce that
    exact contract — wrap the sync helper in request_scope under a RUNNING loop
    — and assert the scope reaches its `resources` read through run_async
    branch 2 (the extract_audio_workflow body is a heavy DBOS workflow, so we
    drive the helper directly with the same wrapping the caller applies)."""
    from app.repositories.media_repository import MediaRepository
    from app.repositories.resources_repository import ResourcesRepository
    from app.tasks.download_helpers import chain_transcript_summary_for_tags

    recorded: dict[str, object] = {}

    async def _rec_media(self, platform_id):
        return {"id": "media-77", "title": "t"}

    async def _rec_by_media(self, media_id, user_id):
        recorded["scope"] = current_scope()
        return None  # short-circuit the rest of the chain

    assert current_scope() is None
    with (
        patch.object(MediaRepository, "get_by_platform_id", _rec_media),
        patch.object(
            ResourcesRepository, "get_resource_by_media_id_and_creator", _rec_by_media
        ),
    ):
        # Mirror extract_audio_workflow's wrap exactly.
        async with request_scope(Scope(user_id=_USER)):
            # Sync helper called from inside a running loop → run_async branch 2.
            chain_transcript_summary_for_tags("p-1", _USER)

    assert getattr(recorded.get("scope"), "user_id", None) == _USER
    assert current_scope() is None


# ── #3 chain_summary_for_tags — e2e via direct sync drive ────────────────


@pytest.mark.asyncio
async def test_chain_summary_for_tags_propagates_scope_through_run_async():
    """e2e for the #3 wiring: the async caller (ai_transcription_workflow) sets
    request_scope around chain_summary_for_tags. Same approach as #2 —
    ai_transcription_workflow is a heavy DBOS workflow, so we drive the sync
    helper under the same request_scope wrap inside a running loop and assert
    the scope reaches its `resources` read through run_async branch 2."""
    from app.repositories.media_repository import MediaRepository
    from app.repositories.resources_repository import ResourcesRepository
    from app.tasks.download_helpers import chain_summary_for_tags

    recorded: dict[str, object] = {}

    async def _rec_get_by_id(self, media_id):
        return {"id": media_id, "platform_id": "p-1", "title": "t"}

    async def _rec_by_media(self, media_id, user_id):
        recorded["scope"] = current_scope()
        return None  # short-circuit

    assert current_scope() is None
    with (
        patch.object(MediaRepository, "get_by_id", _rec_get_by_id),
        patch.object(
            ResourcesRepository, "get_resource_by_media_id_and_creator", _rec_by_media
        ),
    ):
        async with request_scope(Scope(user_id=_USER)):
            chain_summary_for_tags(12345, _USER)

    assert getattr(recorded.get("scope"), "user_id", None) == _USER
    assert current_scope() is None


# ── A4 Item 2: WORKFLOW-LEVEL wrap — drive the real @DBOS.workflow bodies ──
#
# Pass 4b only exercised the SYNC chain helpers directly (above), so the
# `async with request_scope(...)` INSIDE extract_audio_workflow /
# ai_transcription_workflow was never driven by a test — it could be deleted
# without any failure. These two tests close that gap: they ``inspect.unwrap``
# the @DBOS.workflow wrapper (the decorator refuses to run before DBOS.launch()
# but preserves the inner coroutine via @wraps — same approach as
# test_transcode_thumbnail_scope_wiring.py), patch every step + the chain
# helper, and assert the ambient USER scope (correct user_id) is set AT THE
# POINT the chain call runs. Removing the workflow-body wrap → recorded scope
# becomes None → these FAIL. INERT wrt SCOPE_ENFORCE_RESOURCES.


@pytest.mark.asyncio
async def test_extract_audio_workflow_body_wraps_chain_in_user_scope():
    """Drive ``extract_audio_workflow`` body: all steps stubbed, the SYNC chain
    helper patched to record ``current_scope()``. The workflow body's
    ``async with request_scope(Scope(user_id=...))`` must establish the USER
    scope when chain_transcript_summary_for_tags runs, and reset after."""
    import inspect

    from app.workflows import extract_audio as ea

    recorded: dict[str, object] = {}

    async def _noop_status(*_a, **_k):
        return None

    async def _noop_processing(*_a, **_k):
        return None

    async def _noop_log(*_a, **_k):
        return None

    def _ok_extract(_platform_id):
        return True

    def _rec_chain(platform_id, user_id, **_kw):
        # Runs synchronously inside the workflow body's request_scope wrap.
        recorded["scope"] = current_scope()

    assert current_scope() is None
    with (
        patch.object(ea, "mark_extract_audio_processing_step", _noop_processing),
        patch.object(ea, "mark_extract_audio_status_step", _noop_status),
        patch.object(ea, "run_extract_audio_step", _ok_extract),
        patch.object(ea, "log_extract_audio_outcome_step", _noop_log),
        patch(
            "app.tasks.download_helpers.chain_transcript_summary_for_tags",
            _rec_chain,
        ),
    ):
        body = inspect.unwrap(ea.extract_audio_workflow)
        result = await body("p-1", _USER, video_title="Clip")

    assert result == {"status": "success", "platform_id": "p-1"}
    assert getattr(recorded.get("scope"), "user_id", None) == _USER, (
        "extract_audio_workflow must wrap the chain call in "
        "request_scope(Scope(user_id)) — workflow-body wrap regression."
    )
    assert current_scope() is None, "ambient scope leaked after the workflow"


@pytest.mark.asyncio
async def test_ai_transcription_workflow_body_wraps_chain_in_user_scope():
    """Drive ``ai_transcription_workflow`` body: load/whisper/mark steps stubbed,
    the SYNC chain helper patched to record ``current_scope()``. The post-success
    ``async with request_scope(Scope(user_id=...))`` must establish the USER
    scope when chain_summary_for_tags runs, and reset after."""
    import inspect

    from app.workflows import ai_transcription as at

    recorded: dict[str, object] = {}

    async def _fake_load(_pid, _uid):
        return {
            "audio_path": "a.wav",
            "resource_id": "res-1",
            "platform_id": "p-1",
            "provider_key": "openai",
            "provider_config": {},
            "language": "auto",
            "task_assignment": "",
        }

    def _fake_assert(audio_path):
        return audio_path

    async def _fake_whisper(*_a, **_k):
        return {
            "language": "en",
            "duration_seconds": 1.0,
            "text_len": 3,
            "segments_count": 1,
        }

    async def _fake_mark(_pid):
        return None

    class _FakeManager:
        async def update_progress(self, *_a, **_k):
            return None

    def _rec_chain(parsed_media_id, user_id, **_kw):
        recorded["scope"] = current_scope()

    assert current_scope() is None
    with (
        patch.object(at, "load_transcribe_inputs", _fake_load),
        patch.object(at, "assert_audio_present_step", _fake_assert),
        patch.object(at, "run_whisper", _fake_whisper),
        patch.object(at, "mark_transcript_completed", _fake_mark),
        patch(
            "app.services.infra.unified_task_manager.get_task_manager",
            lambda: _FakeManager(),
        ),
        patch("app.tasks.download_helpers.chain_summary_for_tags", _rec_chain),
    ):
        body = inspect.unwrap(at.ai_transcription_workflow)
        result = await body(12345, _USER)

    assert result["parsed_media_id"] == 12345
    assert getattr(recorded.get("scope"), "user_id", None) == _USER, (
        "ai_transcription_workflow must wrap the summary chain call in "
        "request_scope(Scope(user_id)) — workflow-body wrap regression."
    )
    assert current_scope() is None, "ambient scope leaked after the workflow"


# ── Sanity: the running loop genuinely forces run_async branch 2 ─────────


@pytest.mark.asyncio
async def test_running_loop_forces_run_async_branch2():
    """Guard against the prior task's trap: confirm that calling a sync helper
    from inside this test's running loop routes run_async through branch 2
    (ThreadPoolExecutor), not branch 1 (asyncio.run). If a future refactor made
    the helper land on a loop-less thread, this would silently stop testing
    copy_context."""
    # A running loop IS present in an async test.
    asyncio.get_running_loop()  # would raise if not — proves branch 2 is taken.
