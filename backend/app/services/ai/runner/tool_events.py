"""``tool_call`` 的唯一发射点（三期 3c §3.2）。

两个新字段：``duration_ms``（单调钟包住**工具执行本身**，不含 PreToolUse 钩子与结果
裁剪）、``error_code``（类型化失败码，成功是 None）。

⚠️ import 写成 ``from ...events import emit``（不是 ``emit as emit_event``）：
``tests/runner/test_events_single_entry.py::test_no_second_best_effort_emit_helper_is_defined``
扫所有 ``emit_*`` 定义，要求函数体里出现字面量 ``emit(``；``emit_event(`` 不含这个子
串，改名即触发守卫。
"""

from __future__ import annotations

from typing import Any, Optional

from app.services.ai.runner.events import emit

TOOL_CALL_EVENT_TYPE = "tool_call"


def tool_error_code(result: Any) -> Optional[str]:
    """优先级：处理方给的 ``error_code`` > 非 ``ok`` 的 ``outcome`` > 存在 ``error``
    键（退化成通用 ``tool_error``）。非 dict 一律 None——「读不懂」不等于「失败」，
    算成错误会让工具错误率虚高。"""
    if not isinstance(result, dict):
        return None
    code = result.get("error_code")
    if isinstance(code, str) and code:
        return code
    outcome = result.get("outcome")
    if isinstance(outcome, str) and outcome and outcome != "ok":
        return outcome
    return "tool_error" if "error" in result else None


async def emit_tool_call(
    recorder: Any,
    *,
    tool: str,
    args: Any,
    result: Any,
    iteration: int,
    duration_ms: int,
    error_code: Optional[str],
) -> None:
    """把一次已执行（或已判定为未执行）的工具调用写上 transcript。"""
    await emit(
        recorder,
        TOOL_CALL_EVENT_TYPE,
        {
            "tool": tool,
            "args": args,
            "result": result,
            "iteration": iteration,
            "duration_ms": duration_ms,
            "error_code": error_code,
        },
    )


__all__ = ["TOOL_CALL_EVENT_TYPE", "emit_tool_call", "tool_error_code"]
