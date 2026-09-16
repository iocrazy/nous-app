"""spec 3c §4.1：带工具调用的那一步，模型先说的话也要进 transcript。

在这之前 ``assistant`` 只在「这一步没有工具调用」时写（``_run_turn_inner`` 的
``if not tool_calls:`` 分支），所以「先交代现状、再动手」的文本从没进过时间线。
第三条路径（adapter 无 ``stream`` → ``stream_turn`` 委托 ``run_turn``）是生产上
chunk_callback 回合的唯一路径，缺它就会「前两条全绿而生产一条没写出来」
（2026-09-08 stop_reason 事故同形）。
"""

import pytest

from app.services.ai.adapters.base import StreamChunk

pytestmark = pytest.mark.unit


class _Rec:
    """``test_turn_end_reasons.py`` 的 ``_Rec`` 照抄，只改两处：``record_event``
    多记自增 seq 与 turn/step 关键字；多一个 ``of()``。其余方法原样。"""

    def __init__(self):
        self.events = []
        self._seq = 0
        self.views = {
            "view": {
                "question": {
                    "id": "q:1:1",
                    "kind": "user",
                    "prompt": "Which?",
                    "options": [{"label": "A", "description": None}],
                    "allow_free_text": False,
                    "asked_at": "2026-09-07T00:00:00Z",
                }
            }
        }

    async def record_event(self, event_type, payload, *, turn=None, step=None):
        self._seq += 1
        self.events.append(
            (self._seq, event_type, {**payload, "_turn": turn, "_step": step})
        )

    def record_usage(self, **k):
        pass

    async def heartbeat(self):
        pass

    async def check_cancelled(self):
        return False

    def record_skill(self, s):
        pass

    def of(self, event_type):
        return [(seq, p) for seq, t, p in self.events if t == event_type]


def _composed():
    from uuid import UUID

    from app.schemas.ai_library import ComposedSystemPrompt

    return ComposedSystemPrompt(
        agent_id=UUID(int=1),
        agent_slug="t",
        model="m",
        temperature=0.0,
        max_tokens=16,
        system_message="sys",
        tools=[],
        skill_manifest=[],
        cache_fingerprint="f",
    )


def _runner(adapter):
    from app.services.ai.runner.agent_runner import AgentRunner

    class _Tool:
        recorder = None

        async def execute(self, args):
            return {}

    return AgentRunner(adapter=adapter, skill_tool=_Tool())


NARRATION = "Here is where things stand: 6403 unique frames.\n\nNow I will list them."
_TOOL_CALLS = [
    {
        "id": "call_1",
        "type": "function",
        "function": {"name": "Skill", "arguments": '{"skill":"script-outline"}'},
    }
]


def _resp(content, tool_calls=None, finish="stop"):
    """真实 wire 形状：provider 一次返回 content + tool_calls（OpenAI / Anthropic
    都这么发）。"""
    msg = {"role": "assistant", "content": content}
    if tool_calls:
        msg["tool_calls"] = tool_calls
    return {
        "choices": [{"message": msg, "finish_reason": finish}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 4},
    }


def _narrations(rec):
    return [(seq, p) for seq, p in rec.of("assistant") if p.get("partial") is True]


async def _drain(adapter, rec):
    """走 stream_turn（两个流式用例共用）。"""
    async for _ch in _runner(adapter).stream_turn(
        _composed(),
        [{"role": "user", "content": "go"}],
        recorder=rec,
        auto_recorder=False,
    ):
        pass


async def _buffered(adapter, rec):
    """走 run_turn（两个非流式用例共用）。"""
    await _runner(adapter).run_turn(
        _composed(), [{"role": "user", "content": "go"}], recorder=rec
    )


async def test_run_turn_writes_the_narration_before_the_tool_call():
    from unittest.mock import AsyncMock

    adapter = AsyncMock()
    adapter.call = AsyncMock(
        side_effect=[_resp(NARRATION, _TOOL_CALLS, "tool_calls"), _resp("Done.")]
    )
    rec = _Rec()
    await _buffered(adapter, rec)
    rows = _narrations(rec)
    assert len(rows) == 1, rec.events
    seq, payload = rows[0]
    assert payload["content"] == NARRATION
    assert payload["step"] == 1 and payload["_step"] == 1 and payload["_turn"] == 1
    assert seq < rec.of("tool_call")[0][0], rec.events  # 顺序就是语义：叙述在动作之前
    # 最终回答那条不带 partial——折叠器靠这个键分流。
    finals = [p for _s, p in rec.of("assistant") if "partial" not in p]
    assert len(finals) == 1 and finals[0]["content"] == "Done."


async def test_run_turn_writes_nothing_when_the_step_had_no_text():
    """Claude 在纯工具轮把 content 发成 null；空事件在时间线上是个不说话的气泡。"""
    from unittest.mock import AsyncMock

    adapter = AsyncMock()
    adapter.call = AsyncMock(
        side_effect=[_resp(None, _TOOL_CALLS, "tool_calls"), _resp("Done.")]
    )
    rec = _Rec()
    await _buffered(adapter, rec)
    assert _narrations(rec) == []


class _StreamAdapter:
    def __init__(self):
        self.rounds = 0

    async def call(self, *a, **k):
        raise AssertionError("must not fall back to call()")

    async def stream(self, composed, messages, **kw):
        self.rounds += 1
        if self.rounds == 1:
            yield StreamChunk(
                delta_text="Here is where things stand: 6403 unique frames."
            )
            yield StreamChunk(delta_text="\n\nNow I will list them.")
            yield StreamChunk(
                tool_call_delta={"tool_calls": [{"index": 0, **_TOOL_CALLS[0]}]}
            )
            yield StreamChunk(
                finish_reason="tool_calls",
                usage={"prompt_tokens": 10, "completion_tokens": 4},
            )
        else:
            yield StreamChunk(delta_text="Done.")
            yield StreamChunk(
                finish_reason="stop",
                usage={"prompt_tokens": 12, "completion_tokens": 2},
            )


STEP_A = "First I check the frames."
STEP_B = "Now I write the outline."


def _tool_call(call_id, skill):
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": "Skill", "arguments": '{"skill":"%s"}' % skill},
    }


class _TwoStepStreamAdapter:
    """两步都带工具调用，各自说一句不同的话。用来钉住 ``step_text`` 每步清零。"""

    def __init__(self):
        self.rounds = 0

    async def call(self, *a, **k):
        raise AssertionError("must not fall back to call()")

    async def stream(self, composed, messages, **kw):
        self.rounds += 1
        if self.rounds == 1:
            yield StreamChunk(delta_text=STEP_A)
            yield StreamChunk(
                tool_call_delta={
                    "tool_calls": [
                        {"index": 0, **_tool_call("call_1", "script-outline")}
                    ]
                }
            )
            yield StreamChunk(
                finish_reason="tool_calls",
                usage={"prompt_tokens": 10, "completion_tokens": 4},
            )
        elif self.rounds == 2:
            yield StreamChunk(delta_text=STEP_B)
            yield StreamChunk(
                tool_call_delta={
                    "tool_calls": [
                        {"index": 0, **_tool_call("call_2", "script-expand")}
                    ]
                }
            )
            yield StreamChunk(
                finish_reason="tool_calls",
                usage={"prompt_tokens": 12, "completion_tokens": 4},
            )
        else:
            yield StreamChunk(delta_text="Done.")
            yield StreamChunk(
                finish_reason="stop",
                usage={"prompt_tokens": 14, "completion_tokens": 2},
            )


class _NoStreamAdapter:
    """没有 ``stream`` 属性——生产上 chunk_callback 回合走的就是它，stream_turn
    委托 run_turn。"""

    def __init__(self):
        self._resps = [_resp(NARRATION, _TOOL_CALLS, "tool_calls"), _resp("Done.")]

    async def call(self, *a, **k):
        return self._resps.pop(0)


async def test_stream_turn_writes_the_narration_accumulated_from_deltas():
    rec = _Rec()
    await _drain(_StreamAdapter(), rec)
    rows = _narrations(rec)
    assert len(rows) == 1, rec.events
    assert rows[0][1]["content"] == NARRATION  # 两段 delta 拼回一整段
    assert rows[0][1]["step"] == 1 and rows[0][1]["_step"] == 1
    assert rows[0][0] < rec.of("tool_call")[0][0], rec.events


async def test_each_streamed_step_narrates_only_its_own_text():
    """``step_text`` 在 per-iteration 块里声明，每步清零。把声明提到 ``while`` 外，
    第二条叙述会带上第一步的文本——只有这个用例会红。"""
    rec = _Rec()
    await _drain(_TwoStepStreamAdapter(), rec)
    rows = _narrations(rec)
    assert len(rows) == 2, rec.events
    assert rows[0][1]["content"] == STEP_A
    assert rows[0][1]["step"] == 1 and rows[0][1]["_step"] == 1
    # 第二条恰好是 B，不含 A —— 不是 startswith/in，是相等。
    assert rows[1][1]["content"] == STEP_B, rows[1][1]["content"]
    assert rows[1][1]["step"] == 2 and rows[1][1]["_step"] == 2
    # 每条都排在它那一步的 tool_call 之前。
    tool_seqs = [seq for seq, _p in rec.of("tool_call")]
    assert len(tool_seqs) == 2, rec.events
    assert rows[0][0] < tool_seqs[0] < rows[1][0] < tool_seqs[1], rec.events


async def test_buffered_fallback_carries_the_narration_through_run_turn():
    rec = _Rec()
    await _drain(_NoStreamAdapter(), rec)
    rows = _narrations(rec)
    assert len(rows) == 1 and rows[0][1]["content"] == NARRATION, rec.events
    # 与另两条用例对齐：坐标列与 payload 的 step 都要落到位。
    assert rows[0][1]["step"] == 1
    assert rows[0][1]["_step"] == 1 and rows[0][1]["_turn"] == 1
    assert rows[0][0] < rec.of("tool_call")[0][0], rec.events
