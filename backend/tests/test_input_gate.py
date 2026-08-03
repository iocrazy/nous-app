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
