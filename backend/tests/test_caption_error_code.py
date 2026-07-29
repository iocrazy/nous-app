"""caption_asset's failure path records an error_catalog code.

The user-visible bug this pins: a provider 401 inside the caption step
surfaces to the UI as ``task_tracking.error_msg`` = "Step … exceeded its
maximum of N retries", which is trigger-owned and can't be rewritten. The
workflow instead writes ``metadata.error_code`` so the frontend can show
actionable copy.

Drives the real workflow body (``inspect.unwrap`` past @DBOS.workflow,
same approach as test_caption_classify_materialize.py) with the steps
stubbed to raise.
"""

from __future__ import annotations

import inspect
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.ai.error_catalog import PROVIDER_AUTH

pytestmark = pytest.mark.asyncio

_USER = "11111111-1111-1111-1111-111111111111"
_RID = "9000000000000000001"


class _FakeMaxRetries(Exception):
    """Same surface as dbos.DBOSMaxStepRetriesExceeded."""

    def __init__(self, errors):
        self.errors = errors
        super().__init__("Step call_caption has exceeded its maximum of 2 retries")


async def _run_failing_caption(step_error: Exception):
    """Run the workflow with provider resolution raising ``step_error``.

    Returns ``(result, manager)`` so callers can assert on both the
    uniform failure dict and what was written to task_tracking.
    """
    from app.workflows import caption_asset as m

    manager = MagicMock()
    manager.update_progress = AsyncMock(return_value=None)
    manager.patch_metadata = AsyncMock(return_value=None)
    manager.fail = AsyncMock(return_value=None)

    repo = MagicMock()
    repo.get_resource_by_id = AsyncMock(
        return_value={"id": _RID, "file_path": "/data/x.png", "file_type": "image"}
    )
    repo.update_resource = AsyncMock(return_value=None)

    with (
        patch.object(m, "resolve_caption_provider", AsyncMock(side_effect=step_error)),
        patch(
            "app.repositories.resources_repository.ResourcesRepository",
            return_value=repo,
        ),
        patch(
            "app.services.infra.unified_task_manager.get_task_manager",
            return_value=manager,
        ),
        patch.object(m.DBOS, "workflow_id", "wf-caption-1", create=True),
    ):
        result = await inspect.unwrap(m.caption_asset_workflow)(
            resource_id=_RID, user_id=_USER
        )
    return result, manager


async def test_provider_401_behind_dbos_retry_wrapper_writes_error_code():
    exc = _FakeMaxRetries(
        [
            Exception(
                "Error code: 401 - {'error': {'message': 'Incorrect API key "
                "provided', 'code': 'invalid_api_key'}}"
            )
        ]
    )
    result, manager = await _run_failing_caption(exc)

    assert result["status"] == "failed"
    manager.patch_metadata.assert_awaited_once_with(
        "wf-caption-1", {"error_code": PROVIDER_AUTH}
    )
    # error_msg / phase stay trigger-owned — the workflow only reaches them
    # through the existing manager.fail() tail-catch, never a direct PATCH.
    patched_keys = set(manager.patch_metadata.await_args.args[1])
    assert patched_keys == {"error_code"}


async def test_unclassifiable_failure_writes_no_code():
    """Unknown shapes leave metadata alone so the UI falls back to the raw
    error rather than a generic catch-all that hides information."""
    result, manager = await _run_failing_caption(RuntimeError("something novel"))

    assert result["status"] == "failed"
    manager.patch_metadata.assert_not_awaited()


async def test_metadata_write_failure_does_not_change_the_failure_path():
    """Recording the code runs on an already-failing path; if it blows up
    the workflow must still report the original failure."""
    from app.workflows import caption_asset as m

    manager = MagicMock()
    manager.update_progress = AsyncMock(return_value=None)
    manager.patch_metadata = AsyncMock(side_effect=RuntimeError("db down"))
    manager.fail = AsyncMock(return_value=None)

    repo = MagicMock()
    repo.get_resource_by_id = AsyncMock(
        return_value={"id": _RID, "file_path": "/data/x.png", "file_type": "image"}
    )

    with (
        patch.object(
            m,
            "resolve_caption_provider",
            AsyncMock(side_effect=Exception("Error code: 401 - unauthorized")),
        ),
        patch(
            "app.repositories.resources_repository.ResourcesRepository",
            return_value=repo,
        ),
        patch(
            "app.services.infra.unified_task_manager.get_task_manager",
            return_value=manager,
        ),
        patch.object(m.DBOS, "workflow_id", "wf-caption-2", create=True),
    ):
        result = await inspect.unwrap(m.caption_asset_workflow)(
            resource_id=_RID, user_id=_USER
        )

    assert result["status"] == "failed"
    assert "401" in result["error"]
