"""崩溃类终态要进成功率的分母，也要进 ``failed_runs``（3c 终审 I4）。

三个终态写方不经过 ``RunRecorder._finish``：

| 写方 | ``turn_end_reason`` |
|---|---|
| ``liveness_scanner._mark_dead(reason="liveness_dead")`` | ``dead`` |
| ``liveness_scanner._mark_dead(reason="heartbeat_lost")`` | ``heartbeat_lost`` |
| ``agent_runs_repository.mark_heartbeat_lost_ids`` | ``heartbeat_lost`` |
| ``services/liveness/reconcile.reconcile_stranded_runs`` | ``stranded`` |

在此之前这三处两个数都不动：``ai_usage_hourly`` 那一行数不到它们（``_finish`` 是
全仓唯一写方），``turn_end_reason`` 留 NULL。而 UsagePage 的 Success 格分母正是
``sum(turn_end_reasons)`` —— **进程死掉、心跳丢失、重启被斩，这三类是用户最想从
成功率上看见的失败，而它们两个数都不动**，成功率只在「跑完了但结局不是 completed」
之间比较。

⚠️ 小时行与 ``_finish`` 那一行互斥，靠的是两边同一个 ``status='running'`` 守卫：
谁先翻了 status，另一边的 UPDATE 就匹配不到行（``_finish`` 侧读 rowcount，3c 终审
M2）。两边都写才是重复计数。
"""

from __future__ import annotations

import contextlib
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest

pytestmark = pytest.mark.unit

_DIMS = {
    "team_id": 7,
    "project_id": 11,
    "agent_id": "3f4a1b2c-0000-4000-8000-000000000001",
    "model": "doubao",
    "trigger": "chat",
    "attribution": "direct_human",
}


class _Row:
    """UPDATE ... RETURNING 回的行。按名字取维度，与真 Row 的用法一致。"""

    def __init__(self, run_id):
        self.id = run_id
        for k, v in _DIMS.items():
            setattr(self, k, v)


class _Session:
    def __init__(self, rows):
        self.values, self._rows = None, rows

    async def execute(self, stmt):
        self.values = dict(stmt.compile().params)
        rows = self._rows

        class _R:
            rowcount = len(rows)

            def fetchall(self):
                return rows

            def all(self):
                return rows

        return _R()


def _scope(sess):
    @contextlib.asynccontextmanager
    async def _cm():
        yield sess

    return _cm


# ───────────────────────── liveness_scanner._mark_dead ─────────────────────


@pytest.mark.parametrize(
    "reason,expected",
    [("liveness_dead", "dead"), ("heartbeat_lost", "heartbeat_lost")],
)
async def test_mark_dead_stamps_the_crash_class(reason, expected):
    from app.workflows import liveness_scanner as mod

    sess = _Session([_Row(701)])
    usage = AsyncMock()
    with (
        patch("app.db.session.write_scope", _scope(sess)),
        patch("app.services.ai_usage.record_usage", usage),
        patch("app.services.search.projection.project_run_id_best_effort", AsyncMock()),
    ):
        await mod._mark_dead(701, "stuck", reason=reason)
    assert sess.values["turn_end_reason"] == expected
    assert usage.await_count == 1
    kw = usage.await_args.kwargs
    assert (kw["failed_runs"], kw["run_count"], kw["cost_cents"]) == (1, 1, 0)
    assert kw["team_id"] == 7 and kw["model"] == "doubao"


async def test_mark_dead_writes_nothing_when_the_cas_missed():
    """没命中意味着别人已经把它收工了 —— 再写一行小时表就是重复计数。"""
    from app.workflows import liveness_scanner as mod

    usage = AsyncMock()
    with (
        patch("app.db.session.write_scope", _scope(_Session([]))),
        patch("app.services.ai_usage.record_usage", usage),
        patch("app.services.search.projection.project_run_id_best_effort", AsyncMock()),
    ):
        await mod._mark_dead(701, "stuck", reason="liveness_dead")
    assert usage.await_count == 0


# ──────────────────── reconcile_stranded_runs ─────────────────────────────


async def test_stranded_runs_are_stamped_projected_and_counted():
    """K8 同批：这一处此前连 ``search_docs`` 投影都没接。"""
    from app.services.liveness import reconcile as mod

    sess = _Session([_Row(801), _Row(802)])
    usage, project = AsyncMock(), AsyncMock()
    with (
        patch("app.db.engine.is_configured", lambda: True),
        patch("app.db.session.write_scope", _scope(sess)),
        patch("app.services.ai_usage.record_usage", usage),
        patch("app.services.search.projection.project_run_id_best_effort", project),
    ):
        out = await mod.reconcile_stranded_runs()
    assert out == {"reconciled": 2}
    assert sess.values["turn_end_reason"] == "stranded"
    assert usage.await_count == 2
    assert [c.args[0] for c in project.await_args_list] == [801, 802]


async def test_nothing_stranded_writes_nothing():
    from app.services.liveness import reconcile as mod

    usage, project = AsyncMock(), AsyncMock()
    with (
        patch("app.db.engine.is_configured", lambda: True),
        patch("app.db.session.write_scope", _scope(_Session([]))),
        patch("app.services.ai_usage.record_usage", usage),
        patch("app.services.search.projection.project_run_id_best_effort", project),
    ):
        assert await mod.reconcile_stranded_runs() == {"reconciled": 0}
    assert usage.await_count == 0 and project.await_count == 0


# ──────────────────── mark_heartbeat_lost_ids ─────────────────────────────


async def test_the_sweeper_stamps_heartbeat_lost_and_counts_it():
    """⚠️ 这一戳**先于** ``close_interrupted_runs``（同一 tick，仓库先跑），而后者
    的补写带 ``turn_end_reason IS NULL`` 守卫，所以列上是 ``heartbeat_lost`` 而
    transcript 事件仍是 ``turn_end{reason:interrupted, detail:heartbeat_lost}``。
    两者不冲突：事件说「这一轮被腰斩」，列多说了一句「因为心跳没了」——分布条要的
    正是后者这个粒度。"""
    from app.repositories.agent_runs_repository import AgentRunsRepository

    sess = _Session([_Row(901)])
    usage = AsyncMock()
    with (
        patch("app.repositories.agent_runs_repository.write_scope", _scope(sess)),
        patch("app.services.ai_usage.record_usage", usage),
        patch("app.services.search.projection.project_run_id_best_effort", AsyncMock()),
    ):
        swept = await AgentRunsRepository().mark_heartbeat_lost_ids(
            stale_before=datetime.now(timezone.utc)
        )
    assert swept == [901]
    assert sess.values["turn_end_reason"] == "heartbeat_lost"
    assert usage.await_count == 1
    assert usage.await_args.kwargs["failed_runs"] == 1


async def test_a_usage_write_failure_never_swallows_the_swept_ids():
    """调用方拿这些 id 去补 ``turn_end`` 事件。遥测失败不该把它们吞掉 ——
    同这个函数已有的投影失败纪律。"""
    from app.repositories.agent_runs_repository import AgentRunsRepository

    boom = AsyncMock(side_effect=RuntimeError("rollup down"))
    with (
        patch(
            "app.repositories.agent_runs_repository.write_scope",
            _scope(_Session([_Row(901)])),
        ),
        patch("app.services.ai_usage.record_usage", boom),
        patch("app.services.search.projection.project_run_id_best_effort", AsyncMock()),
    ):
        swept = await AgentRunsRepository().mark_heartbeat_lost_ids(
            stale_before=datetime.now(timezone.utc)
        )
    assert swept == [901]
