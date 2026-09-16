"""3c §4.1：回合一拿到 ``agent_runs`` 行就把 run id 交给调用方。

``chat_stream`` 的 ``start`` 帧是从这里出来的。测的是**真的 run_session_turn**
（``_chat_env`` 只桩掉 LLM 栈），不是 chat_stream 里那个假 chat——那条测试证明的是
帧的形状，这条证明的是那个值真的来自 recorder 的 run 行。

时序才是重点：回调必须在**任何正文**之前落地。生产的 ``chunk_callback`` 回合走
``stream_turn`` 的缓冲回退分支，整段正文一次交付；run id 要是等到那时候才出来，
状态行整场回合都不画——而「有回调」这件事照样是绿的。

⚠️ **为什么这里不需要真的 ``_NoStreamAdapter``**（``tests/runner/test_turn_end_reasons.py``
那套）：那条纪律针对的是**穿过 ``stream_turn`` 的返回值**——给 ``run_turn`` 结果加的
新标志必须证明它穿得过缓冲回退那层重包。本 Task 的两个新字段都不走那条路：

* ``run_id`` 在 ``RunRecorder`` 一进 context 时就发了，**早于**任何 runner 调用，
  所以 adapter 有没有 ``stream`` 与它无关；
* ``cost_cents`` / ``charged_points`` 是回合**结束后**去库里读的，不经 runner。

所以这里用一个「单终止 chunk 吐完全部正文」的 runner 复刻缓冲回退的**交付形状**
（那才是能证伪时序的东西），而不是去复刻它的内部分支。真跑那条分支的覆盖在
``test_turn_end_reasons.py``，本 Task 未改动它。
"""

from unittest.mock import patch
from uuid import uuid4

import pytest

from app.services.ai.adapters.base import StreamChunk
from app.services.ai.chat import ai_library_chat_service as svc_mod
from tests.test_parity_gap_coverage import (
    _chat_env,
    _FakeStore,
    _RunRecorderCM,
    _session_row,
)

pytestmark = pytest.mark.unit


async def _turn_with_stream(order: list) -> object:
    """一个真回合，runner 按缓冲回退分支的形状出货：**一个**终止 chunk 把整段
    正文一次吐完（`AgentRunner.stream_turn` 在 adapter 没有 ``stream`` 时就是这样
    重包 ``run_turn`` 的）。"""
    user_id, agent_id = uuid4(), uuid4()
    store = _FakeStore(_session_row(user_id, agent_id))

    async def _chunk_cb(text: str) -> None:
        order.append(("text", text))

    async def _run_started(run_id: str) -> None:
        order.append(("run", run_id))

    with _chat_env(
        run_turn_result={"content": "ok", "tool_calls": []}, agent_id=agent_id
    ) as recorder:

        async def _stream_turn(*a, **kw):
            yield StreamChunk(
                delta_text="the whole answer in one lump",
                finish_reason="stop",
                usage={},
                tool_call_trace=[],
            )

        svc_mod.build_agent_runner_stack.return_value.runner.stream_turn = _stream_turn
        with patch.object(
            svc_mod, "RunRecorder", side_effect=lambda **kw: _RunRecorderCM(recorder)
        ):
            await svc_mod.AILibraryChatService(store=store).run_session_turn(
                uuid4(),
                user_id=user_id,
                content="go",
                chunk_callback=_chunk_cb,
                run_started_callback=_run_started,
            )
        return recorder


async def test_the_run_id_is_handed_over_before_any_text():
    order: list = []
    recorder = await _turn_with_stream(order)

    assert order[0] == ("run", str(recorder.run_id))
    assert ("text", "the whole answer in one lump") in order


async def test_a_turn_without_the_callback_still_runs():
    """负向对照：这个参数是可选的。议题链路、``/chat``、workforce 都不传它，
    多一个必填参数就是把它们全部打断。"""
    user_id, agent_id = uuid4(), uuid4()
    store = _FakeStore(_session_row(user_id, agent_id))
    with _chat_env(
        run_turn_result={"content": "ok", "tool_calls": []}, agent_id=agent_id
    ) as recorder:
        with patch.object(
            svc_mod, "RunRecorder", side_effect=lambda **kw: _RunRecorderCM(recorder)
        ):
            out = await svc_mod.AILibraryChatService(store=store).run_session_turn(
                uuid4(), user_id=user_id, content="go"
            )
    assert out["run_id"] == str(recorder.run_id)


async def test_a_raising_callback_does_not_kill_the_turn():
    """回调是调用方给的。它抛异常只该丢掉一次「开始了」的通知，不该把已经开销了
    的一整个回合连同它的 agent_runs 行一起拖垮（同 ``chunk_callback`` 的规矩）。"""
    user_id, agent_id = uuid4(), uuid4()
    store = _FakeStore(_session_row(user_id, agent_id))

    async def _boom(_run_id: str) -> None:
        raise RuntimeError("consumer went away")

    with _chat_env(
        run_turn_result={"content": "ok", "tool_calls": []}, agent_id=agent_id
    ) as recorder:
        with patch.object(
            svc_mod, "RunRecorder", side_effect=lambda **kw: _RunRecorderCM(recorder)
        ):
            out = await svc_mod.AILibraryChatService(store=store).run_session_turn(
                uuid4(),
                user_id=user_id,
                content="go",
                run_started_callback=_boom,
            )
    assert out["run_id"] == str(recorder.run_id)
