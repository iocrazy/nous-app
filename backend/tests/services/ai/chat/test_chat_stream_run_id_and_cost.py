"""3c §4.1/§4.2：SSE 流的两头。

开头——回合**开始**时就把 run id 交出来。临时气泡没有 ``metadata_json``，在此之前
聊天面板整场回合都没有可读的 run，于是「Step N · 4s」那一行永远不画。

结尾——``done`` 帧带上这一轮的花费与已扣积分。两个键**恒定存在**、读不到为 null：
缺席会被消费方读成 0，而 0 在钱上是另一个答案（「这次免费」）。
"""

from uuid import uuid4

import pytest

from app.services.ai.chat import ai_library_chat_service as svc_mod

pytestmark = pytest.mark.unit


async def _drain(svc, **kw):
    return [
        e async for e in svc.chat_stream("3107", user_id=uuid4(), content="go", **kw)
    ]


def _svc(fake_chat):
    svc = svc_mod.AILibraryChatService(store=object())
    svc.chat = fake_chat  # type: ignore[method-assign]
    return svc


def _answer(**extra):
    return {"assistant_message": {"id": "a1", "content": "hi"}, "usage": {}, **extra}


async def test_the_stream_opens_with_the_run_id_before_any_text(monkeypatch):
    """生产走的是 ``stream_turn`` 的缓冲回退分支（chat wiring 给的
    ``LLMFallbackChain`` 没有 ``stream``），那条分支把整段正文**一次**交给
    ``chunk_callback``。所以 run id 绝不能挂在「第一个 delta」上——那等于整场回合
    结束前都没有 run id，状态行还是不画。这里的假 chat 就是那个形状：先报 run，
    再一次性吐完正文。"""

    async def _chat(
        session_id, *, run_started_callback=None, chunk_callback=None, **kw
    ):
        await run_started_callback("701")
        await chunk_callback("the whole answer in one lump")
        return _answer(run_id="701")

    monkeypatch.setattr(svc_mod, "_run_cost", lambda _rid: _none_cost())
    events = await _drain(_svc(_chat))

    assert events[0] == {"type": "start", "data": {"run_id": "701"}}
    assert events[1]["type"] == "delta"


async def test_a_turn_that_never_reports_a_run_id_still_streams(monkeypatch):
    """负向对照：没人报 run 就没有 start 帧——不该编一个 null run 出来，
    消费方会把它当成一个可查的 run。"""

    async def _chat(
        session_id, *, run_started_callback=None, chunk_callback=None, **kw
    ):
        await chunk_callback("hi")
        return _answer(run_id=None)

    monkeypatch.setattr(svc_mod, "_run_cost", lambda _rid: _none_cost())
    events = await _drain(_svc(_chat))

    assert [e["type"] for e in events] == ["delta", "done"]


async def test_done_frame_carries_the_cost_and_points(monkeypatch):
    async def _chat(
        session_id, *, run_started_callback=None, chunk_callback=None, **kw
    ):
        return _answer(run_id="701")

    seen: list = []

    async def _cost(run_id):
        seen.append(run_id)
        return {"cost_cents": 0.82, "charged_points": 0.5}

    monkeypatch.setattr(svc_mod, "_run_cost", _cost)
    events = await _drain(_svc(_chat))

    assert seen == ["701"]
    assert events[-1]["data"]["cost_cents"] == 0.82
    assert events[-1]["data"]["charged_points"] == 0.5


async def test_done_frame_keeps_the_two_keys_when_the_money_read_fails(monkeypatch):
    """真的走 ``_run_cost``（不是桩），仓库炸掉时两个键仍在、值是 null。
    键消失才是坏的：消费方 ``?? 0`` 一下就把一次读失败说成了免费。"""
    import app.repositories.agent_runs_repository as runs_mod

    class _Boom:
        async def cost_rows_for_ids(self, ids):
            raise RuntimeError("db down")

    monkeypatch.setattr(runs_mod, "get_agent_runs_repository", lambda: _Boom())

    async def _chat(
        session_id, *, run_started_callback=None, chunk_callback=None, **kw
    ):
        return _answer(run_id="701")

    events = await _drain(_svc(_chat))

    assert events[-1]["data"]["cost_cents"] is None
    assert events[-1]["data"]["charged_points"] is None


async def _none_cost():
    return {"cost_cents": None, "charged_points": None}
