"""一个 ``tool_call`` 发射器（3c §3.2）。三处手搓同形 payload 的代价已经见过一次：
``stream_turn`` 的 trace 曾整个缺席，生产唯一路径上的 FinishIssue 声明静默消失。"""

import ast
from pathlib import Path

import pytest

from app.services.ai.runner.tool_events import emit_tool_call, tool_error_code

pytestmark = pytest.mark.unit
RUNNER = Path(__file__).resolve().parents[2] / "app" / "services" / "ai" / "runner"


def _literal_call_lines(path, literal):
    """调用的第二个位置参数是该字面量的行号。ast 而不是正则——注释里的字面量、
    跨行调用、以及它作为 dict 键出现都不该误判。"""
    tree = ast.parse(path.read_text())
    return [
        n.lineno
        for n in ast.walk(tree)
        if isinstance(n, ast.Call)
        and len(n.args) >= 2
        and isinstance(n.args[1], ast.Constant)
        and n.args[1].value == literal
    ]


def test_no_runner_module_but_the_emitter_passes_the_literal_to_emit():
    """守卫：第四处裸发发不出去。folds/ 里出现该字面量是读方，所以只扫顶层模块。"""
    offenders = [
        f"{p.name}:{ln}"
        for p in sorted(RUNNER.glob("*.py"))
        if p.name != "tool_events.py"
        for ln in _literal_call_lines(p, "tool_call")
    ]
    assert (
        offenders == []
    ), f"route these through tool_events.emit_tool_call: {offenders}"


@pytest.mark.parametrize(
    "result,expected",
    [
        ({"error_code": "tool_timeout"}, "tool_timeout"),
        ({"outcome": "denied"}, "denied"),
        ({"outcome": "ok"}, None),
        ({"error": "boom"}, "tool_error"),
        ({"ok": True}, None),
        ("a string result", None),
        # 「没有 error 键」是成功；「error 键是 None」是调用方在说这里该有个错。
        ({"error_code": None, "error": None}, "tool_error"),
    ],
)
def test_tool_error_code_reads_the_three_shapes(result, expected):
    assert tool_error_code(result) == expected


class _Rec:
    def __init__(self):
        self.events = []

    async def record_event(self, event_type, payload, *, turn=None, step=None):
        self.events.append((event_type, payload, turn, step))


async def test_emit_tool_call_writes_the_full_payload():
    rec = _Rec()
    await emit_tool_call(
        rec,
        tool="GenerateShotImage",
        args={"shot_id": 9},
        result={"outcome": "ok"},
        iteration=3,
        duration_ms=812,
        error_code=None,
    )
    assert rec.events == [
        (
            "tool_call",
            {
                "tool": "GenerateShotImage",
                "args": {"shot_id": 9},
                "result": {"outcome": "ok"},
                "iteration": 3,
                "duration_ms": 812,
                "error_code": None,
            },
            None,
            None,
        )
    ]
    # 遥测永不打断回合（events.emit 的既有契约，这里只是不绕过它）。
    await emit_tool_call(
        None, tool="X", args={}, result={}, iteration=1, duration_ms=0, error_code=None
    )
