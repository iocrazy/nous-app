"""input_gate —— needs_input 挂起/唤醒原语（仿 approval_gate 的测试口径）。"""

from unittest.mock import AsyncMock, patch

from app.agent_framework import input_gate


async def test_await_user_input_returns_payload():
    with patch.object(
        input_gate,
        "_recv_async",
        new=AsyncMock(
            return_value={
                "reply_text": "摊牌",
                "user_id": "u1",
                "attachments": None,
            }
        ),
    ) as recv:
        got = await input_gate.await_user_input(123, ttl_seconds=60)
    assert got == {"reply_text": "摊牌", "user_id": "u1", "attachments": None}
    recv.assert_awaited_once_with("needs_input:123", timeout_seconds=60)


async def test_await_user_input_timeout_returns_none():
    with patch.object(input_gate, "_recv_async", new=AsyncMock(return_value=None)):
        assert await input_gate.await_user_input(123, ttl_seconds=1) is None


async def test_await_user_input_malformed_payload_returns_none():
    """畸形 payload 不炸 workflow —— 当超时处理，降级旧路径。"""
    with patch.object(
        input_gate, "_recv_async", new=AsyncMock(return_value="not-a-dict")
    ):
        assert await input_gate.await_user_input(123, ttl_seconds=1) is None


async def test_await_user_input_missing_reply_text_returns_none():
    with patch.object(
        input_gate, "_recv_async", new=AsyncMock(return_value={"user_id": "u1"})
    ):
        assert await input_gate.await_user_input(123, ttl_seconds=1) is None


async def test_signal_user_reply_sends_on_topic():
    with patch.object(input_gate, "_send_async", new=AsyncMock()) as send:
        ok = await input_gate.signal_user_reply(
            workflow_id="wf-1",
            issue_id=123,
            reply_text="hi",
            user_id="u1",
            attachments=None,
        )
    assert ok is True
    send.assert_awaited_once_with(
        "wf-1",
        {"reply_text": "hi", "user_id": "u1", "attachments": None},
        topic="needs_input:123",
    )


async def test_send_async_prefers_gateway_client():
    """回复端点跑在 GATEWAY（enqueue-only DBOSClient,无 DBOS 单例）——
    _send_async 必须优先走 client,否则唤醒在生产恒失败降级旧路径
    （2026-08-03 E2E 实测 'No DBOS was created yet'）。"""
    fake_client = AsyncMock()
    with patch(
        "app.services.infra.dbos_orchestrator.get_dbos_client",
        return_value=fake_client,
    ):
        await input_gate._send_async(
            "wf-1", {"reply_text": "hi"}, topic="needs_input:1"
        )
    fake_client.send_async.assert_awaited_once_with(
        "wf-1", {"reply_text": "hi"}, topic="needs_input:1"
    )


async def test_send_async_falls_back_to_singleton_without_client():
    """worker / combined 角色没有 client → 走 DBOS 单例分支。"""
    with (
        patch(
            "app.services.infra.dbos_orchestrator.get_dbos_client",
            return_value=None,
        ),
        patch("dbos.DBOS.send_async", new=AsyncMock()) as singleton_send,
    ):
        await input_gate._send_async(
            "wf-1", {"reply_text": "hi"}, topic="needs_input:1"
        )
    singleton_send.assert_awaited_once_with(
        "wf-1", {"reply_text": "hi"}, topic="needs_input:1"
    )


def test_awaiting_marker_authoritative_home_is_issues_table():
    """标记权威位钉在 issues.execution_state —— issue dispatch 没有
    task_tracking 行（2026-08-03 E2E 实测），写回 task_tracking 独家会让
    回复分流永远查不到 marker、降级旧路径撞 dispatch 自己的执行锁。"""
    import inspect

    mark_src = inspect.getsource(input_gate.mark_awaiting_input)
    assert "UPDATE public.issues" in mark_src
    clear_src = inspect.getsource(input_gate.clear_awaiting_input)
    assert "UPDATE public.issues" in clear_src
    fetch_src = inspect.getsource(input_gate._fetch_awaiting_rows)
    assert "FROM public.issues" in fetch_src

    # app.api.__init__ 的同名 APIRouter 变量会遮蔽模块名，走 importlib 直取。
    import importlib

    router_mod = importlib.import_module("app.api.issue_messages_router")
    load_src = inspect.getsource(router_mod._load_awaiting_marker)
    assert "FROM public.issues" in load_src


async def test_signal_user_reply_swallow_errors_returns_false():
    """send 失败不是致命——调用方会走旧路径兜底。"""
    with patch.object(
        input_gate, "_send_async", new=AsyncMock(side_effect=RuntimeError("gone"))
    ):
        ok = await input_gate.signal_user_reply(
            workflow_id="wf-1",
            issue_id=123,
            reply_text="hi",
            user_id="u1",
            attachments=None,
        )
    assert ok is False
