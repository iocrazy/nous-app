"""带工具调用的那一步，模型先说的话（三期 3c §4.1）。

⚠️ import 写成 ``from ...events import emit``（不是 ``emit as emit_event``）：
``tests/runner/test_events_single_entry.py::test_no_second_best_effort_emit_helper_is_defined``
扫所有 ``emit_*`` 定义，要求函数体里出现字面量 ``emit(``；``emit_event(`` 不含这个子
串，改名即触发守卫。
"""

from __future__ import annotations

from typing import Any

from app.services.ai.runner.events import emit

NARRATION_EVENT_TYPE = "assistant"


async def emit_partial_narration(recorder: Any, text: str, *, step: int) -> None:
    """把模型在**调用工具之前**说的那段话写上 transcript。

    契约（`agent_runner` 的两条路径与 Task 20 的折叠器共同遵守）：

    - 事件类型是 ``assistant``，payload ``{"content", "partial": True, "step"}``，
      坐标列 ``turn=1, step=step``（与 ``_step_started`` / ``_step_ended`` 同口径）。
    - **最终回答那条 ``assistant`` 事件不带 ``partial`` 键**——不是
      ``partial: False``，是**根本没有这个键**。折叠器按 ``p.partial === true``
      分流，消费方不要写 ``p.partial === false``。
    - 叙述必须排在**这一步的 ``tool_call`` 之前**：顺序就是语义，叙述先于动作。
      所以调用点在工具派发之前，不在 ``emit_tool_call`` 的计时窗口里。
    - ``text`` 为空（含只有空白）时是 no-op：模型在纯工具轮把 ``content`` 发成
      null，空事件在时间线上是个不说话的气泡。
    - 传进来的应当是**用户实际看到的**文本：非流式走 ``strip_reasoning``，流式累
      ``ReasoningStreamFilter`` 过滤后的 delta，这样 transcript 与气泡逐字相同。

    回放不收这条事件 —— 它是时间线材料，不是 cross-turn history。见
    ``replay.py::messages_from_events``。
    """
    narration = (text or "").strip()
    if not narration:
        return
    await emit(
        recorder,
        NARRATION_EVENT_TYPE,
        {"content": narration, "partial": True, "step": step},
        turn=1,
        step=step,
    )


__all__ = ["NARRATION_EVENT_TYPE", "emit_partial_narration"]
