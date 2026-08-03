"""跨版本假活挂起 reaper：old-version 挂起被清标记+取消,当前版本不动。"""

from unittest.mock import AsyncMock, patch

from app.agent_framework import input_gate


async def test_reaper_clears_stale_and_cancels():
    rows = [
        {"dbos_workflow_id": "wf-old", "application_version": "v1", "issue_id": 7},
    ]
    with (
        patch.object(
            input_gate, "_fetch_awaiting_rows", new=AsyncMock(return_value=rows)
        ),
        patch.object(input_gate, "clear_awaiting_input", new=AsyncMock()) as clear,
        patch.object(input_gate, "_cancel_workflow", new=AsyncMock()) as cancel,
        patch.object(input_gate, "_clear_issue_lock", new=AsyncMock()) as unlock,
    ):
        n = await input_gate.reap_stale_input_waits(current_version="v2")
    assert n == 1
    clear.assert_awaited_once_with(workflow_id="wf-old")
    cancel.assert_awaited_once_with("wf-old")
    # 被 reap 的 workflow 永远跑不到 execute_issue 的 finally: clear_lock ——
    # 不清 execution_locked_at 的话旧路径回复会对锁自旋 10 分钟后 defer。
    unlock.assert_awaited_once_with("wf-old")


async def test_reaper_skips_current_version():
    rows = [
        {"dbos_workflow_id": "wf-new", "application_version": "v2", "issue_id": 7}
    ]
    with (
        patch.object(
            input_gate, "_fetch_awaiting_rows", new=AsyncMock(return_value=rows)
        ),
        patch.object(input_gate, "clear_awaiting_input", new=AsyncMock()) as clear,
        patch.object(input_gate, "_cancel_workflow", new=AsyncMock()) as cancel,
    ):
        n = await input_gate.reap_stale_input_waits(current_version="v2")
    assert n == 0
    clear.assert_not_awaited()
    cancel.assert_not_awaited()


async def test_reaper_one_bad_row_does_not_kill_sweep():
    """单行失败只记日志,继续清其余行。"""
    rows = [
        {"dbos_workflow_id": "wf-a", "application_version": "v1", "issue_id": 1},
        {"dbos_workflow_id": "wf-b", "application_version": "v1", "issue_id": 2},
    ]
    with (
        patch.object(
            input_gate, "_fetch_awaiting_rows", new=AsyncMock(return_value=rows)
        ),
        patch.object(input_gate, "clear_awaiting_input", new=AsyncMock()),
        patch.object(
            input_gate,
            "_cancel_workflow",
            new=AsyncMock(side_effect=[RuntimeError("boom"), None]),
        ),
        patch.object(input_gate, "_clear_issue_lock", new=AsyncMock()),
    ):
        n = await input_gate.reap_stale_input_waits(current_version="v2")
    assert n == 1
