"""Unit tests for usage_repository read queries (W3c). DB faked.

ORM (Phase B4): the raw fetch_one/fetch_all calls became
``select(...).where(...)`` through ``app.db.session.read_scope()`` — the
harness patches read_scope and inspects the compiled statement/binds
instead of raw SQL strings, mirroring
tests/test_write_memory_load_recent_messages.py.
"""

from __future__ import annotations

import datetime
from contextlib import asynccontextmanager
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy.dialects import postgresql

from app.repositories import usage_repository


def _compile(stmt: Any) -> tuple[str, dict[str, Any]]:
    compiled = stmt.compile(dialect=postgresql.dialect())
    return str(compiled), dict(compiled.params)


class _FakeResult:
    def __init__(self, rows: Any) -> None:
        self._rows = rows if isinstance(rows, list) else [rows]

    def mappings(self) -> "_FakeResult":  # noqa: D102
        return self

    def one(self) -> dict:  # noqa: D102
        return self._rows[0]

    def all(self) -> list[dict]:  # noqa: D102
        return list(self._rows)


class _RecordingSession:
    """Records every execute() call (compiled SQL + binds) and returns
    queued fake results in FIFO order."""

    def __init__(self, *results: Any) -> None:
        self._results = list(results)
        self._i = 0
        self.calls: list[tuple[str, dict]] = []

    async def execute(self, stmt: Any) -> Any:  # noqa: D102
        self.calls.append(_compile(stmt))
        result = self._results[self._i]
        self._i += 1
        return result


def _install(monkeypatch: pytest.MonkeyPatch, session: _RecordingSession) -> None:
    import app.db.session as db_session

    @asynccontextmanager
    async def fake_read_scope():
        yield session

    monkeypatch.setattr(db_session, "read_scope", fake_read_scope)


@pytest.mark.asyncio
async def test_summarize_shapes_totals_groups_daily(monkeypatch):
    total = {
        "prompt_tokens": 300,
        "completion_tokens": 120,
        "total_tokens": 420,
        "cached_input_tokens": 10,
        "cost_cents": Decimal("4.20"),
        "event_count": 7,
    }
    groups = [
        {
            "grp": "qwen-max",
            "prompt_tokens": 200,
            "completion_tokens": 80,
            "total_tokens": 280,
            "cost_cents": Decimal("3.00"),
            "event_count": 4,
        },
        {
            "grp": None,
            "prompt_tokens": 100,
            "completion_tokens": 40,
            "total_tokens": 140,
            "cost_cents": Decimal("1.20"),
            "event_count": 3,
        },
    ]
    daily = [
        {
            "day": "2026-07-17",
            "grp": "qwen-max",
            "total_tokens": 140,
            "cost_cents": Decimal("1.50"),
        },
        {
            "day": "2026-07-18",
            "grp": "qwen-max",
            "total_tokens": 140,
            "cost_cents": Decimal("1.50"),
        },
    ]

    # Order matters: summarize() issues total, then groups, then daily —
    # all three inside ONE read_scope() session.
    session = _RecordingSession(
        _FakeResult(total), _FakeResult(groups), _FakeResult(daily)
    )
    _install(monkeypatch, session)

    now = datetime.datetime(2026, 7, 18, tzinfo=datetime.timezone.utc)
    out = await usage_repository.summarize(
        team_id="900", frm=now - datetime.timedelta(days=7), to=now, group_by="model"
    )
    assert out["total"]["total_tokens"] == 420
    assert len(out["groups"]) == 2
    assert out["groups"][1]["grp"] is None  # null key preserved
    assert len(out["daily"]) == 2
    assert len(session.calls) == 3

    total_sql, total_binds = session.calls[0]
    assert "public.ai_usage_hourly" in total_sql
    assert total_binds["team_id_1"] == 900

    daily_sql, _daily_binds = session.calls[2]
    assert "date_trunc" in daily_sql
    assert "to_char" in daily_sql


@pytest.mark.asyncio
async def test_summarize_rejects_unknown_group_falls_back_to_model(monkeypatch):
    session = _RecordingSession(_FakeResult({}), _FakeResult([]), _FakeResult([]))
    _install(monkeypatch, session)

    now = datetime.datetime(2026, 7, 18, tzinfo=datetime.timezone.utc)
    await usage_repository.summarize(
        team_id="900", frm=now, to=now + datetime.timedelta(days=1), group_by="bogus"
    )
    # bogus → model key expr (a bare column reference, not a ::text cast)
    groups_sql, _ = session.calls[1]
    assert "public.ai_usage_hourly.model" in groups_sql


@pytest.mark.asyncio
async def test_issue_totals_coerces_and_shapes(monkeypatch):
    session = _RecordingSession(
        _FakeResult(
            {
                "prompt_tokens": 50,
                "completion_tokens": 20,
                "total_tokens": 70,
                "cost_cents": Decimal("0.70"),
                "run_count": 2,
            }
        )
    )
    _install(monkeypatch, session)

    out = await usage_repository.issue_totals("123456789012345")

    assert len(session.calls) == 1
    sql, binds = session.calls[0]
    assert "public.agent_runs" in sql
    assert binds["issue_id_1"] == 123456789012345
    assert out["total_tokens"] == 70 and out["run_count"] == 2


def test_valid_group_by_set():
    assert usage_repository.VALID_GROUP_BY == frozenset(
        {"agent", "model", "module", "project", "attribution"}
    )


# ── A2：钱按 root 算，token 按全树算 ──────────────────────────────────

_EMPTY_TOTALS = {
    "prompt_tokens": 0,
    "completion_tokens": 0,
    "total_tokens": 0,
    "cost_cents": Decimal("0"),
    "run_count": 0,
}

# issue_totals 的 SELECT 列表顺序（也是下面拆片段的切点顺序）。
_TOTALS_LABELS = (
    "prompt_tokens",
    "completion_tokens",
    "total_tokens",
    "cost_cents",
    "run_count",
)


def _select_fragments(sql: str) -> dict[str, str]:
    """把编译出的 SELECT 列表拆成 ``{输出别名: 该列的聚合表达式}``。

    切点是 ``AS <label>``，按 ``_TOTALS_LABELS`` 的顺序逐个向后找 —— 这样每个
    聚合的 ``FILTER (WHERE ...)`` 归属到底是哪一列就不会含糊。整串 SQL 里
    ``in`` 一下是不够的：一列带 root 谓词、另一列不带，也照样能让整串命中。
    """
    select_list = sql.split("\nFROM ")[0]
    out: dict[str, str] = {}
    cursor = len("SELECT ")
    for label in _TOTALS_LABELS:
        marker = f" AS {label}"
        end = select_list.index(marker, cursor)
        out[label] = select_list[cursor:end].lstrip(", ")
        cursor = end + len(marker)
    return out


@pytest.mark.asyncio
async def test_issue_totals_bills_every_run_but_counts_only_root_runs(monkeypatch):
    """同一个查询里两条口径。

    钱与 token 都按**该议题的全部 run** 求和：钱读 ``own_cost_cents``（3d 第 0 票，
    每行只记自身、不含后代，所以不双计），token 三列本来就不上滚（每行只记自己那
    一次调用）。``run_count`` 反过来只数 root —— 「跑了几次」问的是顶层运行数。
    共用一条 WHERE 必然错一边，所以 root 谓词只挂在计数那一列的 FILTER 上。
    """
    session = _RecordingSession(_FakeResult(_EMPTY_TOTALS))
    _install(monkeypatch, session)
    await usage_repository.issue_totals(900000000000001)

    sql, binds = session.calls[0]
    frags = _select_fragments(sql)

    # 钱：自身列、全部 run，一点 root 过滤都不许剩。
    assert "own_cost_cents" in frags["cost_cents"]
    assert "parent_run_id IS NULL" not in frags["cost_cents"]
    # 条数：root-only。
    assert "parent_run_id IS NULL" in frags["run_count"]
    # token 三列：该议题的全部 run。
    assert "parent_run_id IS NULL" not in frags["prompt_tokens"]
    assert "parent_run_id IS NULL" not in frags["completion_tokens"]
    assert "parent_run_id IS NULL" not in frags["total_tokens"]
    # 过滤必须留在聚合的 FILTER 里，不能爬进 WHERE —— 那会把 token 也砍掉。
    assert "parent_run_id IS NULL" not in sql.split("\nFROM ")[1]

    assert binds["issue_id_1"] == 900000000000001


@pytest.mark.asyncio
async def test_issue_totals_and_the_budget_gate_sum_the_same_expression(monkeypatch):
    """两处「这个议题花了多少钱」必须同一条表达式。比的是编译出的 SQL 片段，不是
    各自的注释 —— 注释不会在漂移时报错。"""
    from app.repositories import agent_runs_repository as gate_module

    session = _RecordingSession(_FakeResult(_EMPTY_TOTALS))
    _install(monkeypatch, session)
    await usage_repository.issue_totals(1)
    totals_sql = session.calls[0][0]

    class _Scalar:
        def scalar_one(self):
            return 0

    gate_session = _RecordingSession(_Scalar())

    @asynccontextmanager
    async def fake_read_scope():
        yield gate_session

    # agent_runs_repository 在 import 时就把 read_scope 绑成了模块属性，
    # 只 patch app.db.session 碰不到它 —— 那样它会去连真库。
    monkeypatch.setattr(gate_module, "read_scope", fake_read_scope)
    await gate_module.AgentRunsRepository().spent_cents_for_issue(issue_id=1)

    # 比的是**钱那一列**，不是整串 SQL：``run_count`` 现在刻意还带 root 谓词。
    # 绑定参数名（coalesce_N）两边不同，所以比的是列名与聚合形状。
    money = _select_fragments(totals_sql)["cost_cents"]
    gate_sql = gate_session.calls[0][0]
    assert "sum(coalesce(public.agent_runs.own_cost_cents" in money.lower()
    assert "sum(coalesce(public.agent_runs.own_cost_cents" in gate_sql.lower()
    assert "parent_run_id IS NULL" not in money
    assert "parent_run_id IS NULL" not in gate_sql
