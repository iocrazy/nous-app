"""A1-2 逐轮进度：dispatch 循环每轮把 turn/turn_started_at merge 进 execution_state。

merge（``||``）而非覆盖——``set_status`` 的覆盖写在终态抹掉 turn 是预期；
运行中列表页靠 REST 拉取显示「第N轮」。
"""

from __future__ import annotations

from unittest.mock import AsyncMock

from app.workflows.issue_lifecycle import _run_dispatch_with_continuation


async def test_each_turn_marks_progress_before_running():
    marks: list[int] = []

    async def mark(issue_id, turn):
        marks.append(turn)

    async def run_turn(issue_row, agent_id, user_id, is_continuation=False):
        return {"content": "x", "outcome": "continue", "reason": None}

    await _run_dispatch_with_continuation(
        1,
        {"id": 1},
        "agent",
        "u1",
        run_turn=run_turn,
        set_status=AsyncMock(),
        load_issue=AsyncMock(return_value={"status": "in_progress"}),
        max_continuations=2,
        mark_turn=mark,
    )
    # 1 初始 + 2 continuation = 3 轮，每轮跑前标记
    assert marks == [1, 2, 3]


async def test_mark_turn_absent_keeps_behavior():
    """不注入 mark_turn（默认 None）→ 行为与现状一致，零调用。"""

    async def run_turn(issue_row, agent_id, user_id, is_continuation=False):
        return {"content": "x", "outcome": "completed", "reason": "done"}

    res = await _run_dispatch_with_continuation(
        1,
        {"id": 1},
        "agent",
        "u1",
        run_turn=run_turn,
        set_status=AsyncMock(),
        load_issue=AsyncMock(return_value={"status": "in_progress"}),
    )
    assert res["outcome"] == "completed"


async def test_mark_turn_failure_never_breaks_dispatch():
    """mark 抛错被吞——进度装饰不允许影响执行。"""

    async def mark(issue_id, turn):
        raise RuntimeError("db down")

    async def run_turn(issue_row, agent_id, user_id, is_continuation=False):
        return {"content": "x", "outcome": "completed", "reason": None}

    res = await _run_dispatch_with_continuation(
        1,
        {"id": 1},
        "agent",
        "u1",
        run_turn=run_turn,
        set_status=AsyncMock(),
        load_issue=AsyncMock(return_value={"status": "in_progress"}),
        mark_turn=mark,
    )
    assert res["outcome"] == "completed"
