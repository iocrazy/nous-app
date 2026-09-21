"""崩溃的后台子 run 向父级报多少钱。

``_run_subagent_task`` 的崩溃分支自己造一个 envelope
（``{"status": "failed", "error": ..., "summary": ""}``），里面**没有**
``cost_cents`` 键 —— 而下游是 ``envelope.get("cost_cents") or 0``。于是一个跑了
十轮工具调用、烧掉真金白银才挂掉的子 run，在父级的卡片与 ``subagent_done`` 上
都是 0。报 0 的恰恰是最值得注意的那些 run。

与 Task 7b defect A 同族（那次是 ``_build_envelope`` 漏了这个键，成功路径全员
报 0），修法也同族：让读方在键缺席时去问子 run 行，而不是把缺席读成 0。

⚠️ 这里读的是 ``agent_runs.cost_cents`` —— **老列**，展示语义（自身 + 已报到的
后代），与 ``subagent_done.cost_cents`` 一直以来的口径一致。聚合读面（议题预算、
效率账、树总额）自 mig 479 起读 ``own_cost_cents``，不经过这条路径。
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from app.services.workforce.agent_worker import run_one_task

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]

PARENT_RUN_ID = "900"
CHILD_RUN_ID = "77"


def _task(**payload_kw):
    payload = {
        "kind": "subagent",
        "parent_run_id": PARENT_RUN_ID,
        "caller_agent_id": str(uuid4()),
        "subagent_type": "librarian",
        "prompt": "dig",
        "description": "d",
        "child_run_id": None,
        "reply_to": {"target_kind": "issue", "target_id": 7},
        "user_id": str(uuid4()),
        "agent_depth": 0,
    }
    payload.update(payload_kw)
    task_id = str(uuid4())
    return {
        "id": task_id,
        "agent_id": str(uuid4()),
        "user_id": payload["user_id"],
        "lifecycle_status": "assigned",
        "payload": payload,
        "workforce_workflow_id": f"workforce-{task_id}-1",
    }


def _wire(*, envelope=None, crashes=False, rows=None, rows_raise=False):
    task_holder: dict = {}
    workforce = MagicMock()
    workforce.update_task_status = AsyncMock(return_value=True)
    workforce.enqueue_outbox = AsyncMock(return_value={"id": "ob-1"})

    async def _claim(task_id, *, workflow_id):
        return task_holder["task"]

    workforce.claim_task = AsyncMock(side_effect=_claim)

    run_bg = AsyncMock(
        return_value=envelope
        or {"status": "success", "summary": "s", "sub_run_id": "52", "tokens_used": 9}
    )
    if crashes:
        run_bg.side_effect = RuntimeError("provider exploded")

    runs_repo = MagicMock()
    runs_repo.cost_rows_for_ids = AsyncMock(
        side_effect=RuntimeError("pg is down") if rows_raise else None,
        return_value=rows if rows is not None else [],
    )

    agent_repo = MagicMock()
    agent_repo.get_by_id = AsyncMock(return_value=None)

    return SimpleNamespace(
        task_holder=task_holder,
        workforce=workforce,
        inbox_repo=SimpleNamespace(enqueue=AsyncMock(return_value={"id": 1})),
        writer=SimpleNamespace(append=AsyncMock()),
        for_run=None,
        run_bg=run_bg,
        runs_repo=runs_repo,
        agent_repo=agent_repo,
    )


async def _run(w, task):
    w.task_holder["task"] = task
    import app.repositories.agent_run_inbox_repository as inbox_mod
    import app.services.issues.inbox_or_dispatch as deliver_mod
    from app.services.ai.runner import run_recorder as recorder_mod

    w.for_run = AsyncMock(return_value=w.writer)
    stack = [
        patch(
            "app.services.workforce.agent_worker.get_agent_workforce_repository",
            return_value=w.workforce,
        ),
        patch(
            "app.services.workforce.agent_worker.get_agent_repository",
            return_value=w.agent_repo,
        ),
        patch(
            "app.repositories.agent_runs_repository.get_agent_runs_repository",
            return_value=w.runs_repo,
        ),
        patch.object(inbox_mod, "get_agent_run_inbox_repository", lambda: w.inbox_repo),
        patch.object(deliver_mod, "deliver_or_dispatch", AsyncMock()),
        patch.object(recorder_mod.RunEventWriter, "for_run", w.for_run),
        patch(
            "app.services.ai.runner.subagent_task_service.SubAgentTaskService."
            "run_background_task",
            w.run_bg,
        ),
    ]
    for p in stack:
        p.start()
    try:
        return await run_one_task(task)
    finally:
        for p in reversed(stack):
            p.stop()


def _done_event(w):
    event_type, payload = w.writer.append.await_args.args
    assert event_type == "subagent_done"
    return payload


def _inbox_content(w):
    return w.inbox_repo.enqueue.await_args.kwargs["content"]


async def test_a_crashed_child_reports_the_row_s_cost_not_zero():
    """行上写着 0.42 分，父级两处投影都必须是 0.42。"""
    w = _wire(crashes=True, rows=[{"id": 77, "cost_cents": 0.42}])

    out = await _run(w, _task(sub_run_id=CHILD_RUN_ID))

    assert out["status"] == "failed"
    assert w.runs_repo.cost_rows_for_ids.await_args.args[0] == [77]
    assert _done_event(w)["cost_cents"] == pytest.approx(0.42)
    assert _inbox_content(w)["cost_cents"] == pytest.approx(0.42)


async def test_an_unreadable_child_row_falls_back_to_zero():
    """读不到就报 0 —— 但崩溃分支的三件事（投递、done、收口）照常发生。

    这条路径上一次读库失败不该把 ``subagent_done`` 也带走：父的
    ``children.async_pending`` 只在 done 落地时减一。
    """
    w = _wire(crashes=True, rows_raise=True)

    out = await _run(w, _task(sub_run_id=CHILD_RUN_ID))

    assert out["status"] == "failed"
    assert _done_event(w)["cost_cents"] == 0
    w.inbox_repo.enqueue.assert_awaited_once()


async def test_a_child_row_that_is_not_there_reports_zero():
    w = _wire(crashes=True, rows=[])

    await _run(w, _task(sub_run_id=CHILD_RUN_ID))

    assert _done_event(w)["cost_cents"] == 0


async def test_no_child_id_never_touches_the_database():
    """没有子 run id 就没有可读的行；别拿别人的行凑数，也别白跑一次查询。"""
    w = _wire(crashes=True, rows=[{"id": 77, "cost_cents": 0.42}])

    await _run(w, _task())  # payload 里没有 sub_run_id

    w.runs_repo.cost_rows_for_ids.assert_not_awaited()
    assert _done_event(w)["cost_cents"] == 0


async def test_an_envelope_that_reports_its_cost_is_believed():
    """非崩溃路径一个字不改：envelope 带了 ``cost_cents`` 就用它，不查库。"""
    w = _wire(
        envelope={
            "status": "success",
            "summary": "s",
            "sub_run_id": "52",
            "cost_cents": 1.5,
            "tokens_used": 9,
        },
        rows=[{"id": 52, "cost_cents": 999.0}],
    )

    await _run(w, _task())

    w.runs_repo.cost_rows_for_ids.assert_not_awaited()
    assert _done_event(w)["cost_cents"] == pytest.approx(1.5)


async def test_a_zero_cost_envelope_is_believed_too():
    """``0.0`` 是一个答案，不是「没答案」—— 不许被回落覆盖掉。"""
    w = _wire(
        envelope={
            "status": "failed",
            "error": "boom",
            "summary": "",
            "sub_run_id": "52",
            "cost_cents": 0.0,
            "tokens_used": 0,
        },
        rows=[{"id": 52, "cost_cents": 999.0}],
    )

    await _run(w, _task())

    w.runs_repo.cost_rows_for_ids.assert_not_awaited()
    assert _done_event(w)["cost_cents"] == 0.0


# 源头那一半（``_spawn`` 崩溃时返回的 envelope 带不带花费）由
# ``tests/test_subagent_task_service.py`` 覆盖 —— 那里有能把真 ``_spawn`` 跑到
# 「已宣告 → run_turn 抛」的 harness（``_wire_sync_spawn``）。
