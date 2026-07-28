# backend/tests/workflows/test_caption_asset_workflow.py

"""caption_asset_workflow — three-format upgrade (2026-07-28).

Drives the real workflow body (``inspect.unwrap`` past @DBOS.workflow —
same approach as test_caption_classify_materialize.py) with materialize
and the @DBOS.step calls stubbed, so these tests pin the seam that
changed: CaptionService's richer result dict flows through to
``gen_prompt`` / ``gen_prompt_zh`` / ``gen_prompt_json`` and to the
shared ``write_ai_tags`` helper (mocked here — its own sequence is
covered by test_caption_classify_materialize.py's classify case and by
being literally the same code classify_asset already exercised).
"""

from __future__ import annotations

import inspect
import json
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = pytest.mark.asyncio

_USER = "11111111-1111-1111-1111-111111111111"
_RID = "9000000000000000001"
_FS_PATH = "library/t42/ab/cd/abcdef1234.png"


def _make_manager() -> MagicMock:
    mgr = MagicMock()
    mgr.update_progress = AsyncMock(return_value=None)
    return mgr


def _fake_materialize(local: Path):
    @asynccontextmanager
    async def _materialize(file_path: str):
        yield local

    return _materialize


def _resource_repo(resource: dict) -> MagicMock:
    repo = MagicMock()
    repo.get_resource_by_id = AsyncMock(return_value=resource)
    repo.update_resource = AsyncMock(return_value=dict(resource))
    return repo


async def _run_workflow(*, call_result: dict, tags_attached: int = 0, tmp_path=None):
    from app.workflows import caption_asset as m

    local = tmp_path / "materialized.png"
    local.write_bytes(b"png-bytes")
    resource = {"id": _RID, "file_path": _FS_PATH, "file_type": "image"}
    repo = _resource_repo(resource)
    manager = _make_manager()
    call = AsyncMock(return_value=call_result)
    write_tags = AsyncMock(return_value=tags_attached)

    with (
        patch.object(m, "materialize", _fake_materialize(local)),
        patch.object(
            m,
            "resolve_caption_provider",
            AsyncMock(
                return_value={
                    "provider_key": "qwen",
                    "provider_config": {},
                    "agent_model": "m",
                    "agent_slug": "caption",
                }
            ),
        ),
        patch.object(m, "call_caption", call),
        patch(
            "app.repositories.resources_repository.ResourcesRepository",
            return_value=repo,
        ),
        patch(
            "app.services.infra.unified_task_manager.get_task_manager",
            return_value=manager,
        ),
        patch("app.workflows._ai_tags.write_ai_tags", write_tags),
    ):
        result = await inspect.unwrap(m.caption_asset_workflow)(
            resource_id=_RID, user_id=_USER
        )

    return result, repo, manager, write_tags


class TestCaptionAssetWorkflowStructuredResult:
    async def test_writes_three_formats_and_tags(self, tmp_path):
        call_result = {
            "en": "a cat, masterpiece",
            "zh": "一只猫, masterpiece",
            "prompt_json": {
                "subject": "a cat",
                "style": "photorealistic",
                "aspect_ratio": "1:1",
            },
            "tags": [
                {"en": "cat", "zh": "猫"},
                {"en": "studio", "zh": "影棚"},
            ],
            "category": "Photography",
        }
        result, repo, manager, write_tags = await _run_workflow(
            call_result=call_result, tags_attached=2, tmp_path=tmp_path
        )

        assert result["status"] == "ok"
        assert result["tags_added"] == 2

        repo.update_resource.assert_awaited_once()
        written = repo.update_resource.await_args.args[1]
        assert written["gen_prompt"] == "a cat, masterpiece"
        assert written["gen_prompt_zh"] == "一只猫, masterpiece"
        assert json.loads(written["gen_prompt_json"]) == call_result["prompt_json"]

        write_tags.assert_awaited_once()
        kwargs = write_tags.await_args.kwargs
        assert kwargs["resource_id"] == _RID
        assert kwargs["user_id"] == _USER
        assert kwargs["scope_name"] == "ai-caption-tags"
        assert kwargs["log_prefix"] == "[CaptionAsset]"
        entries = list(kwargs["entries"])
        assert entries == [
            {"group": "Photography", "en": "cat", "zh": "猫"},
            {"group": "Photography", "en": "studio", "zh": "影棚"},
        ]

    async def test_progress_stages_in_order(self, tmp_path):
        call_result = {"en": "x", "zh": "y"}
        _, _, manager, _ = await _run_workflow(
            call_result=call_result, tags_attached=0, tmp_path=tmp_path
        )

        stages = [
            (args[1], kwargs.get("subtitle"))
            for args, kwargs in (
                (c.args, c.kwargs) for c in manager.update_progress.await_args_list
            )
        ]
        assert stages == [
            (10, "Resolving provider"),
            (30, "Analyzing image..."),
            (70, "Parsing result"),
            (85, "Saving prompt & tags"),
            (100, "Prompt & tags generated"),
        ]

    async def test_category_defaults_to_prompt_group_when_absent(self, tmp_path):
        call_result = {"en": "x", "tags": [{"en": "solo-tag"}]}
        _, _, _, write_tags = await _run_workflow(
            call_result=call_result, tags_attached=1, tmp_path=tmp_path
        )
        entries = list(write_tags.await_args.kwargs["entries"])
        assert entries == [{"group": "Prompt", "en": "solo-tag", "zh": ""}]


class TestCaptionAssetWorkflowLegacyResult:
    async def test_legacy_two_field_result_skips_json_and_tags(self, tmp_path):
        call_result = {"en": "a fox", "zh": "一只狐狸"}
        result, repo, _, write_tags = await _run_workflow(
            call_result=call_result, tmp_path=tmp_path
        )

        assert result["status"] == "ok"
        assert result["tags_added"] == 0
        written = repo.update_resource.await_args.args[1]
        assert written == {"gen_prompt": "a fox", "gen_prompt_zh": "一只狐狸"}
        write_tags.assert_not_awaited()

    async def test_no_tags_key_skips_tag_write(self, tmp_path):
        call_result = {"en": "x", "zh": "y", "tags": []}
        _, _, _, write_tags = await _run_workflow(
            call_result=call_result, tmp_path=tmp_path
        )
        write_tags.assert_not_awaited()

    async def test_empty_result_raises_and_workflow_records_failure(self, tmp_path):
        # call_caption itself raises when the service returns None (per the
        # step's own contract); simulate that failure path here to confirm
        # the workflow's tail-catch still fires with the richer body.
        from app.workflows import caption_asset as m

        local = tmp_path / "materialized.png"
        local.write_bytes(b"png-bytes")
        resource = {"id": _RID, "file_path": _FS_PATH, "file_type": "image"}
        repo = _resource_repo(resource)
        manager = _make_manager()
        call = AsyncMock(side_effect=RuntimeError("provider unreachable"))
        record_failure = AsyncMock(return_value={"status": "failed"})

        with (
            patch.object(m, "materialize", _fake_materialize(local)),
            patch.object(
                m,
                "resolve_caption_provider",
                AsyncMock(
                    return_value={
                        "provider_key": "qwen",
                        "provider_config": {},
                        "agent_model": "m",
                        "agent_slug": "caption",
                    }
                ),
            ),
            patch.object(m, "call_caption", call),
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
            result = await inspect.unwrap(m.caption_asset_workflow)(
                resource_id=_RID, user_id=_USER
            )

        assert result == {"status": "failed"}
        record_failure.assert_awaited_once()
        repo.update_resource.assert_not_awaited()
