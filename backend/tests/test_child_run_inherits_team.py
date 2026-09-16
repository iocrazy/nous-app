"""3c A3 修复轮 1：子 run 必须继承父 run 的 team_id。

`subagent_task_service` 与 `agent_worker` 两处都硬编码 `team_id=None`。父 run
在 A3 里从「树总额」改成「自身花费」之后，委派出去烧的钱**两边都不收**：父行
只付自己那份，子 run 在 `reconcile_run` 的 `if not team_id` 早退。

不止计费。`idx_agent_runs_billing` 是 `WHERE team_id IS NOT NULL` 的 partial
索引（mig 145），`team_id IS NULL` 的 run 对任何按团队的效率账等于不存在 ——
Task 6（A6）已经把「派发链的 run 必须带上 team_id」立成不变量。

顶层 workforce 派发没有父 run，继承不到团队，保持 None 是对的：一个用户可能
属于多个团队，`get_team_id_for_user` 猜出来的那个不等价。
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

import app.services.ai.scope.scope_binding as binding_mod
from app.services.ai.scope.scope_binding import team_of_run

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]

_PARENT_RUN = 800100000000000001
_TEAM = 42


class _Result:
    def __init__(self, row):
        self._row = row

    def first(self):
        return self._row


class _Session:
    def __init__(self, row):
        self._row = row

    async def execute(self, stmt):
        return _Result(self._row)


class _RaisingSession:
    async def execute(self, stmt):
        raise RuntimeError("db down")


class _Ctx:
    def __init__(self, session):
        self._session = session

    async def __aenter__(self):
        return self._session

    async def __aexit__(self, *exc):
        return False


# ────────────────────────── team_of_run ──────────────────────────


async def test_it_reads_the_parents_team():
    with patch.object(
        binding_mod,
        "read_scope",
        lambda: _Ctx(_Session(SimpleNamespace(team_id=_TEAM))),
    ):
        assert await team_of_run(_PARENT_RUN) == _TEAM


async def test_no_parent_run_means_no_team():
    """顶层派发（workforce 的第一跳）没有父 run —— 不查库，也不猜。"""
    assert await team_of_run(None) is None


async def test_a_missing_row_yields_none_not_a_raise():
    with patch.object(binding_mod, "read_scope", lambda: _Ctx(_Session(None))):
        assert await team_of_run(_PARENT_RUN) is None


async def test_a_lookup_failure_degrades_to_none():
    """与同模块其余查询同一条纪律：查失败降级为 None，绝不把派发弄挂。
    代价是那一次委派不计费，而抛出去会让整个子 run 起不来。"""
    with patch.object(binding_mod, "read_scope", lambda: _Ctx(_RaisingSession())):
        assert await team_of_run(_PARENT_RUN) is None


async def test_a_parent_with_no_team_stays_none():
    with patch.object(
        binding_mod, "read_scope", lambda: _Ctx(_Session(SimpleNamespace(team_id=None)))
    ):
        assert await team_of_run(_PARENT_RUN) is None


# ─────────────────── 两个派发站点的接线 ───────────────────

from tests.test_subagent_task_service import (  # noqa: E402
    _EventRecorder,
    _wire_sync_spawn,
)


def _caller_ctx() -> dict:
    return {
        "caller_agent_id": uuid4(),
        "caller_user_id": uuid4(),
        "parent_run_id": uuid4(),
        "agent_depth": 0,
    }


async def test_a_subagent_child_takes_the_team_off_the_running_parent(monkeypatch):
    """快路径：父 recorder 就在内存里，不用为了一个已知的值再往返一次 DB。"""
    from app.services.ai.runner.subagent_task_service import SubAgentTaskService

    wired = _wire_sync_spawn(monkeypatch)
    rec = _EventRecorder()
    rec.team_id = _TEAM
    monkeypatch.setattr(binding_mod, "team_of_run", AsyncMock(return_value=999))

    service = SubAgentTaskService(**_caller_ctx(), parent_recorder=rec)
    out = await service.spawn({"subagent_type": "librarian", "prompt": "dig"})

    assert out["status"] == "success"
    assert wired.recorders[0].kwargs["team_id"] == _TEAM
    binding_mod.team_of_run.assert_not_awaited()


async def test_a_subagent_child_falls_back_to_the_parents_row(monkeypatch):
    """没有父 recorder（CLI / batch spawn / workforce 重建）时按 run 读库。"""
    from app.services.ai.runner.subagent_task_service import SubAgentTaskService

    wired = _wire_sync_spawn(monkeypatch)
    monkeypatch.setattr(binding_mod, "team_of_run", AsyncMock(return_value=_TEAM))

    service = SubAgentTaskService(**_caller_ctx())
    out = await service.spawn({"subagent_type": "librarian", "prompt": "dig"})

    assert out["status"] == "success"
    assert wired.recorders[0].kwargs["team_id"] == _TEAM
    binding_mod.team_of_run.assert_awaited_once()


async def test_a_subagent_child_with_no_resolvable_team_stays_none(monkeypatch):
    """查不到就是 None —— 不计费好过记到别人头上。"""
    from app.services.ai.runner.subagent_task_service import SubAgentTaskService

    wired = _wire_sync_spawn(monkeypatch)
    monkeypatch.setattr(binding_mod, "team_of_run", AsyncMock(return_value=None))

    service = SubAgentTaskService(**_caller_ctx())
    await service.spawn({"subagent_type": "librarian", "prompt": "dig"})

    assert wired.recorders[0].kwargs["team_id"] is None


async def _drive_workforce_task(*, parent_run_id, team_of_run_result):
    """跑真的 run_one_task，回传 RunRecorder 实际收到的关键字。

    形状照抄 tests/test_agent_worker.py 的 patch 组，只把 RunRecorder 换成会
    记账的工厂 —— 断言打在真正消费这个值的构造调用上，不是源码文本。
    """
    import app.services.workforce.agent_worker as worker_mod
    from tests.test_agent_worker import (
        _build_runner_stack_mock,
        _persistent_agent,
        _run_recorder_cm,
        _task,
    )

    agent_id, user_id = uuid4(), uuid4()
    full = _task(agent_id=agent_id, user_id=user_id, parent_run_id=parent_run_id)

    workforce = MagicMock()
    workforce.INBOX_TABLE = "agent_inbox"
    workforce.claim_task = AsyncMock(
        return_value={**full, "lifecycle_status": "assigned"}
    )
    workforce.update_task_status = AsyncMock(return_value=True)
    workforce.enqueue_outbox = AsyncMock(return_value={"id": str(uuid4())})

    agent_repo = MagicMock()
    agent_repo.get_by_id = AsyncMock(return_value=_persistent_agent(agent_id=agent_id))

    cm, _rec = _run_recorder_cm(run_id=uuid4())
    seen: dict = {}

    def _factory(**kwargs):
        seen.update(kwargs)
        return cm

    with (
        patch(
            "app.services.workforce.agent_worker.get_agent_workforce_repository",
            return_value=workforce,
        ),
        patch(
            "app.services.workforce.agent_worker.get_agent_repository",
            return_value=agent_repo,
        ),
        patch(
            "app.services.workforce.agent_worker.get_skill_repository",
            return_value=MagicMock(list_for_agent=AsyncMock(return_value=[])),
        ),
        patch(
            "app.services.workforce.agent_worker.build_agent_runner_stack",
            AsyncMock(return_value=_build_runner_stack_mock(content="Done.")),
        ),
        patch("app.services.workforce.agent_worker.PromptComposer") as PC,
        patch(
            "app.services.workforce.agent_worker._lookup_inbox_message",
            AsyncMock(return_value=None),
        ),
        patch("app.services.workforce.agent_worker.RunRecorder", _factory),
        patch(
            "app.services.workforce.agent_worker.resolve_dispatch_scope",
            AsyncMock(return_value=SimpleNamespace(as_recorder_kwargs=lambda: {})),
        ),
        patch.object(worker_mod, "_attach_to_parent_run", AsyncMock()),
        patch.object(
            worker_mod, "team_of_run", AsyncMock(return_value=team_of_run_result)
        ),
    ):
        composer = MagicMock()
        composer.compose = AsyncMock(
            return_value=MagicMock(
                agent_id=agent_id,
                agent_slug="summarize",
                model="doubao-seed-2-0-pro-260215",
            )
        )
        PC.return_value = composer
        await worker_mod.run_one_task(
            {"id": full["id"], "workforce_workflow_id": "workforce-t4-1"}
        )
    return seen


async def test_a_workforce_child_inherits_its_dispatchers_team():
    """派发出去的活记在派发者的团队头上 —— 否则委派花费没人付，而且整条
    委派链落在 idx_agent_runs_billing 之外。"""
    seen = await _drive_workforce_task(parent_run_id=uuid4(), team_of_run_result=_TEAM)
    assert seen["team_id"] == _TEAM


async def test_a_top_level_workforce_dispatch_has_no_team_to_inherit():
    """顶层派发没有父 run。保持 None，不回落到 get_team_id_for_user ——
    一个用户可能属于多个团队，猜错比不记更糟。"""
    seen = await _drive_workforce_task(parent_run_id=None, team_of_run_result=None)
    assert seen["team_id"] is None
