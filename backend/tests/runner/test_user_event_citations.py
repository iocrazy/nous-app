"""引用随本轮的 ``user`` 事件落进 transcript（三期 3a T8c 缺陷 4）。

spec §5 稿二要求：消费了引用的那一轮，第 1 步下出现一行「引用 N 件」，注明
**按版本锁定，不随后续修订漂移**。真机验收实测那一行**从未实现** —— 前端既
没有事件分支也没有文案，而更根本的是**事件里没有数据**：引用只进了系统消息
的 ``<referenced_outputs>`` 框（模型看得见），transcript 里一个字都没有（人
看不见）。

所以后端这一半就是：把本轮解析好的坐标顺手写在 ``user`` 事件的 payload 上。

两条纪律：

- **只在非空时写这个键。** 没有引用的轮次必须与从前**逐字节相同** —— 同一
  条理由让全文提示词 pin 不产生 diff，也让旧 run 的渲染分毫不动。
- **不查库。** 坐标与标题是发帖口盖好的（``resolve_output_refs``），这里只
  是把它们顺着轮次带下去；重查一遍就是「同一个问题两处实现」。
"""

from __future__ import annotations

from uuid import UUID

import pytest

from app.schemas.ai_library import ComposedSystemPrompt
from app.services.ai.runner.agent_runner import AgentRunner
from app.services.ai.runner.step_hooks import StepHookChain

pytestmark = pytest.mark.unit

CITATIONS = [
    {
        "kind": "script_shot",
        "ref_id": "337650953731886",
        "version": 2,
        "title": "MEDIUM",
    },
    {"kind": "generated_media", "ref_id": "77", "version": 1, "title": "S3 · Shot #1"},
]


class _Recorder:
    def __init__(self):
        self.events: list[tuple] = []

    async def record_event(self, event_type, payload, *, turn=None, step=None):
        self.events.append((event_type, payload, turn, step))

    def record_usage(self, **kw):
        return None

    def cost_of(self, prompt, completion, cached=0):
        return 0.0

    async def heartbeat(self):
        return None

    async def check_cancelled(self):
        return False


def _composed(**over) -> ComposedSystemPrompt:
    return ComposedSystemPrompt(
        agent_id=UUID(int=1),
        agent_slug="t",
        model="unit-model",
        temperature=0.0,
        max_tokens=16,
        system_message="sys",
        tools=[],
        skill_manifest=[],
        cache_fingerprint="f",
        **over,
    )


class _Adapter:
    """No ``stream`` attribute — the production shape for the chat wiring, so
    ``stream_turn`` takes its buffered fallback (CLAUDE.md 2026-09-08)."""

    async def call(self, composed, messages):
        return {
            "choices": [
                {
                    "message": {"role": "assistant", "content": "ok"},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1},
        }


class _SkillTool:
    recorder = None

    async def execute(self, args):
        return {}


def _runner() -> AgentRunner:
    return AgentRunner(
        adapter=_Adapter(), skill_tool=_SkillTool(), step_hooks=StepHookChain([])
    )


def _user_payload(rec: _Recorder) -> dict:
    return next(e[1] for e in rec.events if e[0] == "user")


@pytest.mark.asyncio
async def test_run_turn_puts_the_turns_citations_on_the_user_event():
    rec = _Recorder()
    await _runner().run_turn(
        _composed(referenced_outputs=CITATIONS),
        [{"role": "user", "content": "revise this"}],
        recorder=rec,
    )

    payload = _user_payload(rec)
    assert payload["content"] == "revise this"
    assert payload["referenced_outputs"] == CITATIONS


@pytest.mark.asyncio
async def test_stream_turn_puts_them_there_too():
    """两条路都要写：聊天面板走 ``stream_turn``，issue 轮次走 ``run_turn``，
    只接一条就是「某一半的产品上没有这一行」。"""
    rec = _Recorder()
    async for _chunk in _runner().stream_turn(
        _composed(referenced_outputs=CITATIONS),
        [{"role": "user", "content": "revise this"}],
        recorder=rec,
    ):
        pass

    assert _user_payload(rec)["referenced_outputs"] == CITATIONS


@pytest.mark.asyncio
async def test_a_turn_without_citations_has_no_such_key_at_all():
    """键**缺席**，不是空表。旧 run 与不带引用的新 run 必须渲染成完全一样的
    东西，而「有这个键但是空的」会让读方多出一个需要判空的分支。"""
    rec = _Recorder()
    await _runner().run_turn(
        _composed(), [{"role": "user", "content": "hello"}], recorder=rec
    )

    assert "referenced_outputs" not in _user_payload(rec)
