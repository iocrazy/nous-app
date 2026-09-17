"""``agent_worker`` 写完 ``subagent_done`` 之后必须补一次树收口 —— 而且**顺序**要对。

为什么这条接线值得一个结构守卫：收口要求全树 ``view.children.async_pending == 0``，
而那个计数**只在 ``subagent_done`` 折进父视图时才减一**，比子 run 自己的 ``_finish``
晚。异步链上的时序因此恒为三拍 —— root 结束 ``pending=1`` 不收口、子 run 结束
``pending`` 仍是 1 还是不收口、事件落地归零 —— 而到那一步为止，全仓的收口调用点只有
``RunRecorder._finish`` 与三个崩溃类终态写方，**没有一个会再来**。

所以「事件之后那一次收口」是异步委派树唯一能**按时**收口的路径。删掉它不会有任何
测试因为「行为错了」而红：树照样会在清扫器 2 小时兜底时收口，只是每棵树都晚两小时、
外加一条与事实相反的 ``async child never materialised`` WARNING。这正是需要结构断言
的那一类缺口 —— 它的症状是延迟与噪音，不是错误。

⚠️ 断的是**次序**，不只是「调过了」：收口读的是库里的 ``metadata_json``，事件还没落库
时调它只会又得到一次 ``pending_children``。
"""

from __future__ import annotations

import ast
import inspect

import pytest

from app.services.workforce import agent_worker as aw

pytestmark = [pytest.mark.unit]


def _subagent_task_fn() -> ast.AsyncFunctionDef:
    """写 ``subagent_done`` 的那个函数（源码里唯一一处）。"""
    tree = ast.parse(inspect.getsource(aw))
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and any(
            isinstance(n, ast.Constant) and n.value == "subagent_done"
            for n in ast.walk(node)
        ):
            return node
    raise AssertionError("源码里找不到写 subagent_done 的函数")


def _line_of_subagent_done(fn) -> int:
    return min(
        n.lineno
        for n in ast.walk(fn)
        if isinstance(n, ast.Constant) and n.value == "subagent_done"
    )


def _lines_calling_settle(fn) -> list[int]:
    out = []
    for n in ast.walk(fn):
        if not isinstance(n, ast.Call):
            continue
        name = n.func.id if isinstance(n.func, ast.Name) else None
        if name is None and isinstance(n.func, ast.Attribute):
            name = n.func.attr
        if name == "settle_tree_if_closed":
            out.append(n.lineno)
    return out


def test_the_worker_settles_the_tree_after_it_writes_subagent_done():
    fn = _subagent_task_fn()
    calls = _lines_calling_settle(fn)
    assert calls, (
        "agent_worker 写完 subagent_done 后没有叫收口 —— 异步委派树会全部等 2 小时"
        "兜底，并各刷一条与事实相反的 WARNING"
    )
    assert min(calls) > _line_of_subagent_done(fn), (
        "收口调用排在 subagent_done 之前 —— 那时事件还没落库，"
        "async_pending 仍是 1，收口只会又得到一次 pending_children"
    )


def test_the_settle_call_is_guarded_so_billing_never_fails_the_delegation():
    """这次委派已经完成了，计费绝不该连坐它。"""
    fn = _subagent_task_fn()
    line = min(_lines_calling_settle(fn))
    guarded = [
        n
        for n in ast.walk(fn)
        if isinstance(n, ast.Try)
        and any(
            isinstance(c, ast.Call)
            and getattr(c.func, "attr", getattr(c.func, "id", None))
            == "settle_tree_if_closed"
            for c in ast.walk(n)
        )
    ]
    assert guarded, f"第 {line} 行那次收口没有包在 try 里"
    assert any(
        h.type is not None for t in guarded for h in t.handlers
    ), "收口的 except 必须是**有类型**的 —— 裸 except 会连 CancelledError 一起吞"
