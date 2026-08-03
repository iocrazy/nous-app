"""回复分流：有等待标记 → send 唤醒；无标记/唤醒失败/workflow 已终态 → 旧路径。"""

# NOTE: app.api.__init__ 里同名变量是 APIRouter 实例，会遮蔽模块名（连
# `import a.b.c as r` 都会优先取父包属性，PEP 328 后语义）—— 只能走
# importlib 直取模块本身。
import importlib
from unittest.mock import AsyncMock, patch

r = importlib.import_module("app.api.issue_messages_router")


def _issue(wf="wf-1"):
    return {"id": 5, "dbos_workflow_id": wf}


async def test_no_marker_returns_false():
    with patch.object(r, "_load_awaiting_marker", new=AsyncMock(return_value=None)):
        assert await r._try_wake_waiting_workflow(_issue(), "u", "hi", None) is False


async def test_marker_and_live_workflow_wakes():
    with (
        patch.object(
            r, "_load_awaiting_marker", new=AsyncMock(return_value={"issue_id": 5})
        ),
        patch.object(r, "_workflow_is_terminal", new=AsyncMock(return_value=False)),
        patch.object(
            r.input_gate, "signal_user_reply", new=AsyncMock(return_value=True)
        ) as sig,
    ):
        assert await r._try_wake_waiting_workflow(_issue(), "u", "hi", None) is True
    sig.assert_awaited_once()


async def test_marker_but_terminal_workflow_falls_back():
    """send 与超时竞态：workflow 已终态 → 旧路径（返 False）。"""
    with (
        patch.object(
            r, "_load_awaiting_marker", new=AsyncMock(return_value={"issue_id": 5})
        ),
        patch.object(r, "_workflow_is_terminal", new=AsyncMock(return_value=True)),
        patch.object(
            r.input_gate, "signal_user_reply", new=AsyncMock(return_value=True)
        ),
    ):
        assert await r._try_wake_waiting_workflow(_issue(), "u", "hi", None) is False


async def test_send_failure_falls_back():
    with (
        patch.object(
            r, "_load_awaiting_marker", new=AsyncMock(return_value={"issue_id": 5})
        ),
        patch.object(r, "_workflow_is_terminal", new=AsyncMock(return_value=False)),
        patch.object(
            r.input_gate, "signal_user_reply", new=AsyncMock(return_value=False)
        ),
    ):
        assert await r._try_wake_waiting_workflow(_issue(), "u", "hi", None) is False


async def test_missing_workflow_id_falls_back():
    assert (
        await r._try_wake_waiting_workflow(
            {"id": 5, "dbos_workflow_id": None}, "u", "hi", None
        )
        is False
    )
