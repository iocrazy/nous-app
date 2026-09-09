"""input_gate.release_parked_workflow — the reaper's recipe, now callable by
the fork endpoint: marker, cancel, lock, in that order."""

from unittest.mock import AsyncMock, patch

import pytest

from app.agent_framework import input_gate as g

pytestmark = pytest.mark.unit


async def test_release_clears_marker_cancels_then_unlocks_in_order():
    order = []

    async def _clear(*, workflow_id):
        order.append(("clear", workflow_id))

    async def _cancel(wf):
        order.append(("cancel", wf))

    async def _unlock(wf):
        order.append(("unlock", wf))

    with (
        patch.object(g, "clear_awaiting_input", _clear),
        patch.object(g, "_cancel_workflow", _cancel),
        patch.object(g, "_clear_issue_lock", _unlock),
    ):
        await g.release_parked_workflow("wf-1")
    assert order == [("clear", "wf-1"), ("cancel", "wf-1"), ("unlock", "wf-1")]


async def test_reaper_goes_through_the_same_helper():
    rel = AsyncMock()
    rows = [
        {"dbos_workflow_id": "wf-old", "application_version": "v1"},
        {"dbos_workflow_id": "wf-new", "application_version": "v2"},
    ]
    with (
        patch.object(g, "_fetch_awaiting_rows", AsyncMock(return_value=rows)),
        patch.object(g, "release_parked_workflow", rel),
    ):
        assert await g.reap_stale_input_waits(current_version="v2") == 1
    rel.assert_awaited_once_with("wf-old")
