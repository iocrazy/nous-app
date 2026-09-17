# backend/tests/workflows/test_workflow_failure_raises.py

"""Route-C rule 4: a DBOS workflow failure must RAISE, not return a failed
dict. Returning a dict makes DBOS record SUCCESS while task_tracking says
failed — the split-brain observed live on 2026-08-08 (wf 5a872175 / 1e63f80b).

Drives the real workflow body via ``inspect.unwrap`` past ``@DBOS.workflow``
(same harness as test_caption_asset_workflow.py) with the first ``@DBOS.step``
call replaced by a raising stub, so the tail ``except`` block runs for real.

Scope note: caption_slide / classify_asset / caption_asset were found
during Step 1's consumer sweep to carry the exact same violation as the
three workflows above (same ``return await record_workflow_failure(...)``
shape) and were folded into this fix rather than left for a follow-up —
same bug, same fix, same file."""

from __future__ import annotations

import inspect
from unittest.mock import AsyncMock, patch

import pytest

pytestmark = pytest.mark.asyncio


def _make_manager() -> AsyncMock:
    mgr = AsyncMock()
    return mgr


class TestAiSummaryWorkflowReraises:
    async def test_reraises_after_recording(self):
        from app.workflows import ai_summary as m

        manager = _make_manager()
        record_failure = AsyncMock(return_value={"status": "failed"})

        with (
            patch.object(
                m,
                "load_summary_inputs",
                AsyncMock(side_effect=RuntimeError("provider 429")),
            ),
            patch(
                "app.services.infra.unified_task_manager.get_task_manager",
                return_value=manager,
            ),
            patch(
                "app.workflows._failure_handler.record_workflow_failure",
                record_failure,
            ),
        ):
            with pytest.raises(RuntimeError, match="provider 429"):
                await inspect.unwrap(m.ai_summary_workflow)(
                    parsed_media_id=1, user_id="u-1"
                )

        record_failure.assert_awaited_once()
        ctx = record_failure.await_args.kwargs["context"]
        assert ctx["workflow"] == "ai_summary"
        assert ctx["parsed_media_id"] == 1
        assert ctx["user_id"] == "u-1"


class TestAiTranscriptionWorkflowReraises:
    async def test_reraises_after_recording(self):
        from app.workflows import ai_transcription as m

        manager = _make_manager()
        record_failure = AsyncMock(return_value={"status": "failed"})
        mark_failed = AsyncMock(return_value=None)

        with (
            patch.object(
                m,
                "load_transcribe_inputs",
                AsyncMock(side_effect=RuntimeError("whisper 500")),
            ),
            patch.object(m, "mark_transcript_failed", mark_failed),
            patch(
                "app.services.infra.unified_task_manager.get_task_manager",
                return_value=manager,
            ),
            patch(
                "app.workflows._failure_handler.record_workflow_failure",
                record_failure,
            ),
        ):
            with pytest.raises(RuntimeError, match="whisper 500"):
                await inspect.unwrap(m.ai_transcription_workflow)(
                    parsed_media_id=1, user_id="u-1"
                )

        mark_failed.assert_awaited_once()
        record_failure.assert_awaited_once()
        ctx = record_failure.await_args.kwargs["context"]
        assert ctx["workflow"] == "ai_transcription"
        assert ctx["parsed_media_id"] == 1
        assert ctx["user_id"] == "u-1"


class TestAiSummaryWorkflowRecordsErrorCode:
    """final-review C3/⑤b: the workflow tail must classify the raised
    exception into a stable error_code (mirrors caption_asset.py /
    caption_slide.py's ``record_ai_error_code`` call, which ai_summary and
    analyze_l1 previously lacked)."""

    async def test_records_ai_error_code_with_the_raised_exception(self):
        from app.workflows import ai_summary as m

        manager = _make_manager()
        record_failure = AsyncMock(return_value={"status": "failed"})
        record_code = AsyncMock(return_value="PROVIDER_RATE_LIMIT")
        exc = RuntimeError("provider 429")

        with (
            patch.object(m, "load_summary_inputs", AsyncMock(side_effect=exc)),
            patch(
                "app.services.infra.unified_task_manager.get_task_manager",
                return_value=manager,
            ),
            patch(
                "app.workflows._failure_handler.record_workflow_failure",
                record_failure,
            ),
            patch(
                "app.services.ai.error_catalog.record_ai_error_code",
                record_code,
            ),
        ):
            with pytest.raises(RuntimeError, match="provider 429"):
                await inspect.unwrap(m.ai_summary_workflow)(
                    parsed_media_id=1, user_id="u-1"
                )

        record_code.assert_awaited_once()
        assert record_code.await_args.args[1] is exc
        record_failure.assert_awaited_once()


class TestAnalyzeL1WorkflowReraises:
    async def test_reraises_after_recording(self):
        from app.workflows import analyze_l1 as m

        manager = _make_manager()
        record_failure = AsyncMock(return_value={"status": "failed"})

        with (
            patch.object(
                m,
                "resolve_analyze_provider",
                AsyncMock(side_effect=RuntimeError("no provider configured")),
            ),
            patch(
                "app.services.infra.unified_task_manager.get_task_manager",
                return_value=manager,
            ),
            patch(
                "app.workflows._failure_handler.record_workflow_failure",
                record_failure,
            ),
        ):
            with pytest.raises(RuntimeError, match="no provider configured"):
                await inspect.unwrap(m.analyze_l1_workflow)(
                    media_id=1, cover_url="http://x", user_id="u-1"
                )

        record_failure.assert_awaited_once()
        ctx = record_failure.await_args.kwargs["context"]
        assert ctx["workflow"] == "analyze_l1"
        assert ctx["media_id"] == 1
        assert ctx["user_id"] == "u-1"


class TestAnalyzeL1WorkflowRecordsErrorCode:
    """final-review C3/⑤b — see TestAiSummaryWorkflowRecordsErrorCode."""

    async def test_records_ai_error_code_with_the_raised_exception(self):
        from app.workflows import analyze_l1 as m

        manager = _make_manager()
        record_failure = AsyncMock(return_value={"status": "failed"})
        record_code = AsyncMock(return_value="PROVIDER_RATE_LIMIT")
        exc = RuntimeError("no provider configured")

        with (
            patch.object(
                m,
                "resolve_analyze_provider",
                AsyncMock(side_effect=exc),
            ),
            patch(
                "app.services.infra.unified_task_manager.get_task_manager",
                return_value=manager,
            ),
            patch(
                "app.workflows._failure_handler.record_workflow_failure",
                record_failure,
            ),
            patch(
                "app.services.ai.error_catalog.record_ai_error_code",
                record_code,
            ),
        ):
            with pytest.raises(RuntimeError, match="no provider configured"):
                await inspect.unwrap(m.analyze_l1_workflow)(
                    media_id=1, cover_url="http://x", user_id="u-1"
                )

        record_code.assert_awaited_once()
        assert record_code.await_args.args[1] is exc
        record_failure.assert_awaited_once()


class TestCaptionSlideWorkflowReraises:
    async def test_reraises_after_recording(self):
        from app.workflows import caption_slide as m

        manager = _make_manager()
        record_failure = AsyncMock(return_value={"status": "failed"})
        repo = AsyncMock()
        repo.get_resource_by_id = AsyncMock(
            side_effect=RuntimeError("resource lookup failed")
        )

        with (
            patch(
                "app.repositories.resources_repository.ResourcesRepository",
                return_value=repo,
            ),
            patch(
                "app.services.infra.unified_task_manager.get_task_manager",
                return_value=manager,
            ),
            patch(
                "app.workflows._failure_handler.record_workflow_failure",
                record_failure,
            ),
        ):
            with pytest.raises(RuntimeError, match="resource lookup failed"):
                await inspect.unwrap(m.caption_slide_workflow)(
                    resource_id="9000000000000000001",
                    user_id="u-1",
                    media_id="42",
                    slide_name="002.jpg",
                )

        record_failure.assert_awaited_once()
        ctx = record_failure.await_args.kwargs["context"]
        assert ctx["workflow"] == "caption_slide"
        assert ctx["resource_id"] == "9000000000000000001"
        assert ctx["slide_name"] == "002.jpg"
        assert ctx["user_id"] == "u-1"


class TestClassifyAssetWorkflowReraises:
    async def test_reraises_after_recording(self):
        from app.workflows import classify_asset as m

        manager = _make_manager()
        record_failure = AsyncMock(return_value={"status": "failed"})
        repo = AsyncMock()
        repo.get_resource_by_id = AsyncMock(
            side_effect=RuntimeError("resource lookup failed")
        )

        with (
            patch(
                "app.repositories.resources_repository.ResourcesRepository",
                return_value=repo,
            ),
            patch(
                "app.services.infra.unified_task_manager.get_task_manager",
                return_value=manager,
            ),
            patch(
                "app.workflows._failure_handler.record_workflow_failure",
                record_failure,
            ),
        ):
            with pytest.raises(RuntimeError, match="resource lookup failed"):
                await inspect.unwrap(m.classify_asset_workflow)(
                    resource_id="9000000000000000001", user_id="u-1"
                )

        record_failure.assert_awaited_once()
        ctx = record_failure.await_args.kwargs["context"]
        assert ctx["workflow"] == "classify_asset"
        assert ctx["resource_id"] == "9000000000000000001"
        assert ctx["user_id"] == "u-1"


class TestCaptionAssetWorkflowReraises:
    async def test_reraises_after_recording(self):
        from app.workflows import caption_asset as m

        manager = _make_manager()
        record_failure = AsyncMock(return_value={"status": "failed"})
        repo = AsyncMock()
        repo.get_resource_by_id = AsyncMock(
            side_effect=RuntimeError("resource lookup failed")
        )

        with (
            patch(
                "app.repositories.resources_repository.ResourcesRepository",
                return_value=repo,
            ),
            patch(
                "app.services.infra.unified_task_manager.get_task_manager",
                return_value=manager,
            ),
            patch(
                "app.workflows._failure_handler.record_workflow_failure",
                record_failure,
            ),
        ):
            with pytest.raises(RuntimeError, match="resource lookup failed"):
                await inspect.unwrap(m.caption_asset_workflow)(
                    resource_id="9000000000000000001", user_id="u-1"
                )

        record_failure.assert_awaited_once()
        ctx = record_failure.await_args.kwargs["context"]
        assert ctx["workflow"] == "caption_asset"
        assert ctx["resource_id"] == "9000000000000000001"
        assert ctx["user_id"] == "u-1"


class TestAnalyzeL1WorkflowReportsTheEmbeddingOutcomeSeparately:
    """CLAUDE.md 防御模式 — orthogonal results are reported on their own.

    A run can succeed at the thing it was paid for (the VLM analysis) and
    still fail to land a vector. That is not a reason to fail the run, but it
    was never a reason to stay silent either: ``content_embedding`` sat at
    zero rows for months while every task said "Analysis complete". The
    reason now reaches Task Center as a subtitle AND as queryable metadata,
    while phase/status stay trigger-owned (Route-C rule 2).
    """

    async def test_embed_error_reaches_metadata_and_subtitle_but_not_the_status(self):
        from app.workflows import analyze_l1 as m

        cfg = {
            "provider_key": "pk",
            "provider_config": {},
            "agent_model": "mm",
            "agent_slug": "analyze",
            "fallback_models": [],
        }
        manager = _make_manager()

        with (
            patch.object(m, "resolve_analyze_provider", AsyncMock(return_value=cfg)),
            patch.object(
                m,
                "call_analyze_l1",
                AsyncMock(
                    return_value={
                        "status": "ok",
                        "embedded": False,
                        "embed_error": "provider_error: boom 503",
                    }
                ),
            ),
            patch(
                "app.services.infra.unified_task_manager.get_task_manager",
                return_value=manager,
            ),
        ):
            out = await inspect.unwrap(m.analyze_l1_workflow)(
                media_id=1, cover_url="http://x", user_id="u-1"
            )

        assert out["embed_error"] == "provider_error: boom 503"
        # ONE unthrottled write carries both halves: the subtitle is the
        # classified code (never provider text), metadata keeps the reason.
        manager.patch_metadata.assert_not_awaited()
        kw = manager.update_progress.await_args.kwargs
        assert kw["force"] is True
        assert kw["metadata_patch"] == {"embed_error": "provider_error"}
        assert "embedding failed: provider_error" in kw["subtitle"]
        assert "boom 503" not in kw["subtitle"]
        # phase/status belong to the mirror trigger, never to the workflow.
        manager.complete.assert_not_awaited()
        manager.fail.assert_not_awaited()

    async def test_a_clean_run_says_only_analysis_complete(self):
        from app.workflows import analyze_l1 as m

        cfg = {
            "provider_key": "pk",
            "provider_config": {},
            "agent_model": "mm",
            "agent_slug": "analyze",
            "fallback_models": [],
        }
        manager = _make_manager()

        with (
            patch.object(m, "resolve_analyze_provider", AsyncMock(return_value=cfg)),
            patch.object(
                m,
                "call_analyze_l1",
                AsyncMock(
                    return_value={"status": "ok", "embedded": True, "embed_error": None}
                ),
            ),
            patch(
                "app.services.infra.unified_task_manager.get_task_manager",
                return_value=manager,
            ),
        ):
            await inspect.unwrap(m.analyze_l1_workflow)(
                media_id=1, cover_url="http://x", user_id="u-1"
            )

        manager.patch_metadata.assert_not_awaited()
        assert (
            manager.update_progress.await_args.kwargs["subtitle"] == "Analysis complete"
        )
