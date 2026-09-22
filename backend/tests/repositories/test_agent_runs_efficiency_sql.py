"""``AgentRunsRepository.efficiency_groups`` — the SQL it actually emits.

Why a compile test and not just a behaviour test: ``FILTER`` is a clause of an
AGGREGATE, so it must hang off ``func.sum``/``func.count``. Hanging it off the
inner ``func.extract`` instead raises ``AttributeError`` while the statement is
still being BUILT — before any database is involved — and a repository that
caught that exception broadly would turn a whole aggregate into an empty result
with a log line nobody reads. A stubbed session cannot see any of this, because
the statement never gets built far enough to be handed over.

So this file asserts on the compiled string: the statement builds, and the
``FILTER`` lands on the aggregates rather than on the ``EXTRACT`` inside one.
"""

from __future__ import annotations

import contextlib
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from sqlalchemy.dialects import postgresql

from app.repositories.agent_runs_repository import AgentRunsRepository

pytestmark = pytest.mark.unit


class _Session:
    """Captures every statement; serves the two result shapes the method reads
    (``.mappings().all()`` for the group rows, ``.all()`` for the reason pairs)."""

    def __init__(self) -> None:
        self.stmts: list = []

    async def execute(self, stmt):
        self.stmts.append(stmt)

        class _R:
            def mappings(self):
                return self

            def all(self):
                return []

        return _R()


def _scope(sess: _Session):
    @contextlib.asynccontextmanager
    async def _cm():
        yield sess

    return _cm


def _sql(stmt) -> str:
    return str(stmt.compile(dialect=postgresql.dialect()))


async def _run(**kw) -> _Session:
    sess = _Session()
    to = datetime.now(timezone.utc)
    with patch("app.repositories.agent_runs_repository.read_scope", _scope(sess)):
        await AgentRunsRepository().efficiency_groups(
            frm=to - timedelta(days=30), to=to, **kw
        )
    return sess


async def test_the_statement_compiles_and_filters_the_aggregates_not_the_extract():
    """The whole point: ``FILTER`` on ``sum``/``count``, never on ``EXTRACT``."""
    sess = await _run(user_id=None, team_id=7, group_by="model")
    sql = _sql(sess.stmts[0])

    # Three filtered aggregates: the timed-run count, the duration sum, and
    # the root-only money column (see the cost test below).
    assert sql.count("FILTER (WHERE") == 3
    assert "count(*) FILTER (WHERE" in sql
    assert "sum(EXTRACT(epoch FROM" in sql
    # The bad form would read ``EXTRACT(...) FILTER (WHERE ...)`` — the FILTER
    # glued to the inner function instead of the aggregate wrapping it.
    assert "started_at) FILTER (WHERE" not in sql
    assert ") FILTER (WHERE public.agent_runs.started_at IS NOT NULL" in sql


async def test_the_bad_form_this_test_exists_for_really_does_explode():
    """Negative control. If SQLAlchemy ever started accepting ``.filter()`` on
    ``extract``, the assertions above would stop meaning anything, and this
    test is what would tell us."""
    from sqlalchemy import func

    from app.models import AgentRuns

    with pytest.raises(AttributeError):
        func.extract("epoch", AgentRuns.ended_at - AgentRuns.started_at).filter(
            AgentRuns.started_at.isnot(None)
        )


async def test_each_scope_argument_lands_in_the_where_clause():
    """``agent_runs`` has no SQL-layer scope backstop, so a scope argument that
    silently failed to narrow would be a cross-tenant read, not a slow query."""
    sess = await _run(team_id=7, project_id=9, group_by="model")
    binds = dict(sess.stmts[0].compile().params)
    assert 7 in binds.values() and 9 in binds.values()
    sql = _sql(sess.stmts[0])
    assert "agent_runs.team_id =" in sql and "agent_runs.project_id =" in sql
    # The window is half-open on both statements.
    assert "agent_runs.created_at >=" in sql and "agent_runs.created_at <" in sql


async def test_grouping_by_agent_switches_the_key_column():
    by_model = _sql((await _run(team_id=7, group_by="model")).stmts[0])
    by_agent = _sql((await _run(team_id=7, group_by="agent")).stmts[0])
    assert "GROUP BY public.agent_runs.model" in by_model
    assert "GROUP BY public.agent_runs.agent_id" in by_agent


async def test_the_reason_query_carries_the_same_scope():
    """The turn_end histogram must be narrowed by the same window and scope as
    the rows — a reason mix drawn from a wider population would not describe the
    table it is shown next to."""
    sess = await _run(team_id=7, group_by="model")
    rows_sql, reasons_sql = _sql(sess.stmts[0]), _sql(sess.stmts[1])
    for fragment in ("agent_runs.team_id =", "agent_runs.created_at >="):
        assert fragment in rows_sql and fragment in reasons_sql
    assert "turn_end_reason IS NOT NULL" in reasons_sql


async def test_cost_rows_for_ids_compiles_and_asks_nothing_for_an_empty_batch():
    sess = _Session()
    with patch("app.repositories.agent_runs_repository.read_scope", _scope(sess)):
        repo = AgentRunsRepository()
        assert await repo.cost_rows_for_ids([]) == []
        assert sess.stmts == []
        await repo.cost_rows_for_ids([1, 2])
    sql = _sql(sess.stmts[0])
    assert "agent_runs.id IN" in sql and "cost_cents" in sql


async def test_a_read_failure_raises_instead_of_returning_an_empty_aggregate():
    """The reason a build-time ``AttributeError`` cannot become a silent wrong
    answer here: this method re-raises. A broad ``except`` that returned
    ``([], {})`` would render as "nothing ran and nothing was spent in this
    window", which is a claim, not a gap — and the route would have no way to
    tell it apart from a genuinely quiet month."""

    @contextlib.asynccontextmanager
    async def _boom():
        raise RuntimeError("connection reset")
        yield  # pragma: no cover — unreachable, keeps this a generator

    to = datetime.now(timezone.utc)
    with patch("app.repositories.agent_runs_repository.read_scope", _boom):
        with pytest.raises(RuntimeError):
            await AgentRunsRepository().efficiency_groups(
                frm=to - timedelta(days=1), to=to, team_id=7
            )

    with patch("app.repositories.agent_runs_repository.read_scope", _boom):
        with pytest.raises(RuntimeError):
            await AgentRunsRepository().cost_rows_for_ids([1])


async def test_only_the_money_column_is_root_filtered():
    """两种粒度的分界线，在唯一能便宜检查的地方。

    钱按**树**算并记在 root 所在的组：子查询按 ``COALESCE(root_run_id, id)`` 把
    ``own_cost_cents`` 预聚合，主查询左联回来后只在 root 行上求和。旧写法
    ``sum(cost_cents) FILTER (root)`` 读的是行上那笔折叠额，子 run 没报回父行时低报
    （3d 第 0 票，与 ``issue_totals`` / ``spent_cents_for_issue`` 同批迁）。

    The counters are the opposite: ``tool_calls`` / ``tool_errors`` /
    ``deliverables`` count what a run did ITSELF and never roll up, so filtering
    them to roots would silently drop every child's work.
    """
    sql = _sql((await _run(team_id=7, group_by="model")).stmts[0])

    assert (
        "sum(tree_cost.cents) FILTER "
        "(WHERE public.agent_runs.parent_run_id IS NULL)" in sql
    )
    # 旧列一个引用都不许剩（``AS cost_cents`` 是对外字段名，不是列引用）。
    assert "public.agent_runs.cost_cents" not in sql
    assert "own_cost_cents" in sql
    for own_metric in ("tool_calls", "tool_errors", "deliverables"):
        assert f"sum(public.agent_runs.{own_metric}) FILTER" not in sql
    # 主查询这一层，root 谓词只属于钱那一列（其余全是每个 run 自身的量）。
    assert sql.count("FILTER (WHERE public.agent_runs.parent_run_id IS NULL)") == 1
    # 另一处 root 谓词在 ``in_scope_roots`` 这个 CTE 里（裁定 7）—— 那是「哪些树算数」，
    # 不是「哪一列要过滤」，两者是两回事。成员判定写成 ``root_run_id IN (…) OR id IN (…)``
    # （让 planner 用得上 idx_agent_runs_root_tree），两条臂**引用同一个 CTE**，所以那段
    # select 只渲染一份 —— 全语句 2 次而不是 3 次。曾经是 3：两条臂各内联一份，PG 于是
    # 按同一套窗口 + scope 扫两遍 agent_runs（改用 CTE 的原因，见
    # ``test_tree_cost_cents.py::test_efficiency_in_scope_roots_is_a_cte_rendered_once``）。
    assert sql.count("parent_run_id IS NULL") == 2


async def test_the_failed_run_count_coalesces_like_its_neighbours():
    """``sum(CASE ...)`` over zero rows is NULL, not 0 — every other counter in
    this SELECT is wrapped, and an unwrapped one hands the route a None that
    only shows up on an empty window."""
    sql = _sql((await _run(team_id=7, group_by="model")).stmts[0])
    assert "coalesce(sum(CASE WHEN" in sql
