"""``tool_call_trace`` 必须带 ``error_code``（3d batch1 Task 4 修复轮）。

transcript 的 ``tool_call`` 事件从 3c §3.2 起就带这个归一化失败码，但**聊天气泡读
的不是 transcript**——它读的是折进助手消息的这条 trace（`ChatToolCall`）。两条路
少了一条，代价是真实的：一个排在 AskUser 后面、**根本没执行**的 ``CreateShot``，在
气泡上渲染成成功，还被 ``summarizeWrites`` 计进「wrote N cards」，用户去画布上找
一张不存在的卡。

所以两处必须同源：`tool_error_code(result)` 每次派发只算一次，trace 与 transcript
共用那一个值。下面对 ``run_turn`` / ``stream_turn`` 两条路径各钉一遍——2026-09-08
的教训是生产走的那条路径的单测才算数。
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock
from uuid import UUID

import pytest

from app.schemas.ai_library import ComposedSystemPrompt
from app.services.ai.adapters.base import StreamChunk
from app.services.ai.runner.agent_runner import AgentRunner
from app.services.ai.tools.finish_issue_tool import (
    FINISH_ISSUE_OUTCOMES,
    finish_issue_handler,
)

pytestmark = pytest.mark.unit


def _composed():
    return ComposedSystemPrompt(
        agent_id=UUID(int=1),
        agent_slug="t",
        model="unit-model",
        temperature=0.0,
        max_tokens=64,
        system_message="sys",
        tools=[],
        skill_manifest=[],
        cache_fingerprint="fp",
    )


class _Recorder:
    def __init__(self):
        self.events = []

    async def record_event(self, event_type, payload, *, turn=None, step=None):
        self.events.append((event_type, payload))

    def record_usage(self, **kw):
        return None

    def record_skill(self, slug):
        return None

    def cost_of(self, prompt, completion, cached=0):
        return 0.0

    async def heartbeat(self):
        return None

    async def check_cancelled(self):
        return False


class _SkillTool:
    """``result`` 由用例给定 —— 这里要覆盖的正是那些**不带 ``ok`` 键**的真实形状。"""

    def __init__(self, result):
        self.result = result

    async def execute(self, args):
        return self.result


def _transcript_codes(rec):
    return [p["error_code"] for t, p in rec.events if t == "tool_call"]


def _buffered_adapter():
    adapter = AsyncMock()
    adapter.call.side_effect = [
        {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "id": "tc1",
                                "function": {
                                    "name": "Skill",
                                    "arguments": json.dumps({"skill": "s"}),
                                },
                            }
                        ],
                    }
                }
            ]
        },
        {"choices": [{"message": {"content": "FINAL"}}]},
    ]
    return adapter


class _StreamingAdapter:
    """生产的聊天路径。注意它**有** ``stream``，走的是真流式分支。"""

    def __init__(self):
        self.iter = 0

    async def call(self, composed, messages):
        return {"choices": [{"message": {"content": "fallback"}}]}

    async def stream(self, composed, messages):
        self.iter += 1
        if self.iter == 1:
            yield StreamChunk(
                tool_call_delta={
                    "tool_calls": [
                        {
                            "index": 0,
                            "id": "c1",
                            "function": {"name": "Skill", "arguments": ""},
                        }
                    ]
                }
            )
            yield StreamChunk(
                tool_call_delta={
                    "tool_calls": [
                        {"index": 0, "function": {"arguments": '{"skill":"s"}'}}
                    ]
                }
            )
            yield StreamChunk(finish_reason="tool_calls")
        else:
            yield StreamChunk(delta_text="FINAL", finish_reason="stop")


# 真实形状，不是理想化的：前者是 finish_issue / 被腰斩调用那一族（有 ``error``
# 或非 ok 的 ``outcome``，但**没有** ``ok`` 键），后者是干净成功。
FAILED_RESULT = {"error": "not executed: the turn parked on AskUser before this call"}
OK_RESULT = {"ok": True, "text": "done"}


@pytest.mark.parametrize(
    "result,expected",
    [(FAILED_RESULT, "tool_error"), (OK_RESULT, None)],
)
async def test_run_turn_trace_carries_error_code(result, expected):
    rec = _Recorder()
    runner = AgentRunner(adapter=_buffered_adapter(), skill_tool=_SkillTool(result))

    out = await runner.run_turn(
        _composed(), [{"role": "user", "content": "go"}], recorder=rec
    )

    assert out["content"] == "FINAL"
    (call,) = out["tool_calls"]
    assert call["error_code"] == expected
    # 同源：trace 与 transcript 报的是同一个值，不是各算各的。
    assert _transcript_codes(rec) == [expected]


@pytest.mark.parametrize(
    "result,expected",
    [(FAILED_RESULT, "tool_error"), (OK_RESULT, None)],
)
async def test_stream_turn_trace_carries_error_code(result, expected):
    rec = _Recorder()
    runner = AgentRunner(adapter=_StreamingAdapter(), skill_tool=_SkillTool(result))

    traces = []
    async for chunk in runner.stream_turn(
        _composed(), [{"role": "user", "content": "go"}], recorder=rec
    ):
        if chunk.tool_call_trace is not None:
            traces = chunk.tool_call_trace

    (call,) = traces
    assert call["error_code"] == expected
    assert _transcript_codes(rec) == [expected]


# ── FinishIssue：受理回执不是工具错误（2026-09-23 真栈 S1 缺陷 A）────────────
#
# FinishIssue 的回执是 ``{"acknowledged": True, "outcome": <声明的结局>, ...}``，
# ``outcome`` 是模型声明的 issue 结局（completed / needs_input / continue），不是
# 失败码。曾被 ``tool_error_code`` 的「非 ok outcome」规则读成错误，每次 FinishIssue
# 都给 ``agent_runs.tool_errors`` +1、气泡上也标成失败。三条路径都走真实的
# FinishIssue 分发与真实 handler，其中「adapter 无 ``stream``」是生产唯一走的
# 缓冲回退分支（CLAUDE.md 2026-09-08）。


def _finish_issue_call(outcome):
    return {
        "id": "fi1",
        "function": {
            "name": "FinishIssue",
            "arguments": json.dumps({"outcome": outcome, "reason": "r"}),
        },
    }


class _FinishIssueBufferedAdapter:
    """没有 ``stream`` 属性（故意不用 AsyncMock——它会凭空长出 ``stream``）。"""

    def __init__(self, outcome):
        self.outcome = outcome
        self.calls = 0

    async def call(self, composed, messages):
        self.calls += 1
        if self.calls == 1:
            return {
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": None,
                            "tool_calls": [_finish_issue_call(self.outcome)],
                        }
                    }
                ]
            }
        return {"choices": [{"message": {"content": "FINAL"}}]}


class _FinishIssueStreamingAdapter:
    def __init__(self, outcome):
        self.outcome = outcome
        self.iter = 0

    async def call(self, composed, messages):
        return {"choices": [{"message": {"content": "fallback"}}]}

    async def stream(self, composed, messages):
        self.iter += 1
        if self.iter == 1:
            yield StreamChunk(
                tool_call_delta={
                    "tool_calls": [
                        {
                            "index": 0,
                            "id": "fi1",
                            "function": {"name": "FinishIssue", "arguments": ""},
                        }
                    ]
                }
            )
            yield StreamChunk(
                tool_call_delta={
                    "tool_calls": [
                        {
                            "index": 0,
                            "function": {
                                "arguments": json.dumps(
                                    {"outcome": self.outcome, "reason": "r"}
                                )
                            },
                        }
                    ]
                }
            )
            yield StreamChunk(finish_reason="tool_calls")
        else:
            yield StreamChunk(delta_text="FINAL", finish_reason="stop")


def _finish_issue_runner(adapter):
    runner = AgentRunner(adapter=adapter, skill_tool=_SkillTool(OK_RESULT))
    runner.finish_issue_handler = finish_issue_handler
    return runner


def _assert_acknowledged_without_error_code(call, rec, outcome):
    assert call["name"] == "FinishIssue"
    assert call["result"]["acknowledged"] is True
    assert call["result"]["outcome"] == outcome
    assert call["error_code"] is None
    assert _transcript_codes(rec) == [None]


@pytest.mark.parametrize("outcome", FINISH_ISSUE_OUTCOMES)
async def test_run_turn_finish_issue_is_not_a_tool_error(outcome):
    rec = _Recorder()
    runner = _finish_issue_runner(_FinishIssueBufferedAdapter(outcome))

    out = await runner.run_turn(
        _composed(), [{"role": "user", "content": "go"}], recorder=rec
    )

    (call,) = out["tool_calls"]
    _assert_acknowledged_without_error_code(call, rec, outcome)


@pytest.mark.parametrize("outcome", FINISH_ISSUE_OUTCOMES)
async def test_stream_turn_finish_issue_is_not_a_tool_error(outcome):
    rec = _Recorder()
    runner = _finish_issue_runner(_FinishIssueStreamingAdapter(outcome))

    traces = []
    async for chunk in runner.stream_turn(
        _composed(), [{"role": "user", "content": "go"}], recorder=rec
    ):
        if chunk.tool_call_trace is not None:
            traces = chunk.tool_call_trace

    (call,) = traces
    _assert_acknowledged_without_error_code(call, rec, outcome)


@pytest.mark.parametrize("outcome", FINISH_ISSUE_OUTCOMES)
async def test_stream_turn_buffered_fallback_finish_issue_is_not_a_tool_error(
    outcome,
):
    rec = _Recorder()
    adapter = _FinishIssueBufferedAdapter(outcome)
    assert not hasattr(adapter, "stream")
    runner = _finish_issue_runner(adapter)

    traces = []
    async for chunk in runner.stream_turn(
        _composed(), [{"role": "user", "content": "go"}], recorder=rec
    ):
        if chunk.tool_call_trace is not None:
            traces = chunk.tool_call_trace

    (call,) = traces
    _assert_acknowledged_without_error_code(call, rec, outcome)


async def test_rejected_finish_issue_is_still_a_tool_error():
    """非法 outcome 被 handler 拒绝 —— 那才是真的工具错误，不能被一起放过。"""
    rec = _Recorder()
    runner = _finish_issue_runner(_FinishIssueBufferedAdapter("bogus"))

    out = await runner.run_turn(
        _composed(), [{"role": "user", "content": "go"}], recorder=rec
    )

    (call,) = out["tool_calls"]
    assert "acknowledged" not in call["result"]
    assert call["error_code"] == "tool_error"
    assert _transcript_codes(rec) == ["tool_error"]
