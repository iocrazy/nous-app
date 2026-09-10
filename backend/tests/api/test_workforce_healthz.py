"""GET /workforce/healthz must report on the runtime that actually exists.

It used to read ``app.state.workforce_scheduler``, which nothing has set since
PR-D8 Phase 3 moved dispatch onto DBOS-scheduled workflows. Nothing errored —
the probe simply answered ``status='down'`` on every call, forever, which is
the worst failure mode a probe has: unconditional and therefore uninformative.
Phase 2b-2 T3 deleted the scheduler outright and repointed the probe at DBOS.
"""

from __future__ import annotations

import importlib
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ``app.api`` re-exports the router object under this very name, shadowing the
# submodule attribute — import it explicitly, as the ORM compile suites do.
mod = importlib.import_module("app.api.workforce_router")

pytestmark = pytest.mark.unit


class _Result:
    def __init__(self, value: Any) -> None:
        self._value = value

    def scalar(self) -> Any:
        return self._value


class _Session:
    """Answers the three counts of the throughput section in order:
    persistent agents, rows processed recently, pending queue depth."""

    def __init__(self, values: list[Any]) -> None:
        self._values = list(values)

    async def execute(self, stmt: Any) -> _Result:
        return _Result(self._values.pop(0) if self._values else 0)


class _ScopeCM:
    def __init__(self, session: _Session) -> None:
        self._session = session

    async def __aenter__(self) -> _Session:
        return self._session

    async def __aexit__(self, *exc: Any) -> bool:
        return False


def _patch(
    monkeypatch,
    *,
    launched: bool,
    inflight: int,
    counts: list[int],
    oldest_undispatched_age_s: float | None = None,
) -> None:
    """``counts`` answers the inbox throughput selects in order; the dispatch
    probe is stubbed separately because it reads task_tracking, not agent_inbox."""
    monkeypatch.setattr(mod, "read_scope", lambda: _ScopeCM(_Session(counts)))
    monkeypatch.setattr(mod, "dbos_is_launched", lambda: launched)
    pool = MagicMock()
    pool.inflight_count = AsyncMock(return_value=inflight)
    monkeypatch.setattr(mod, "DbosAgentWorkforcePool", lambda: pool)

    async def _oldest():
        return oldest_undispatched_age_s

    monkeypatch.setattr(mod, "_oldest_undispatched_age_s", _oldest)


@pytest.mark.asyncio
async def test_healthy_when_dbos_is_launched_and_the_queue_is_draining(monkeypatch):
    _patch(monkeypatch, launched=True, inflight=2, counts=[3, 5, 1])

    out = await mod.workforce_healthz()

    assert out["status"] == "healthy"
    assert out["issues"] == []
    assert out["dispatcher"]["launched"] is True
    assert out["dispatcher"]["inflight_agent_tasks"] == 2
    # The dead key must be gone, not merely always-false.
    assert "scheduler" not in out


@pytest.mark.asyncio
async def test_down_when_dbos_is_not_launched(monkeypatch):
    _patch(monkeypatch, launched=False, inflight=0, counts=[0, 0, 0])

    out = await mod.workforce_healthz()

    assert out["status"] == "down"
    assert any("dbos" in i.lower() for i in out["issues"])


@pytest.mark.asyncio
async def test_stuck_queue_is_still_caught_while_dbos_reports_launched(monkeypatch):
    """``launched`` says this process can reach DBOS — NOT that the scheduled
    tick is firing on the worker. The throughput signal is what catches a
    stalled dispatcher, so it must survive the rewrite."""
    # 4 persistent agents, 0 rows processed in the window, 7 still pending.
    _patch(monkeypatch, launched=True, inflight=0, counts=[4, 0, 7])

    out = await mod.workforce_healthz()

    assert out["status"] == "degraded"
    assert any("queue stuck" in i for i in out["issues"])


@pytest.mark.asyncio
async def test_inflight_read_failure_degrades_instead_of_crashing(monkeypatch):
    """A gauge that cannot be read is a missing number, not a dead service —
    and the endpoint is unauthenticated, so it must never 500."""
    monkeypatch.setattr(mod, "read_scope", lambda: _ScopeCM(_Session([0, 0, 0])))
    monkeypatch.setattr(mod, "dbos_is_launched", lambda: True)
    pool = MagicMock()
    pool.inflight_count = AsyncMock(side_effect=RuntimeError("pg down"))
    monkeypatch.setattr(mod, "DbosAgentWorkforcePool", lambda: pool)

    out = await mod.workforce_healthz()

    assert out["dispatcher"]["inflight_agent_tasks"] is None
    assert out["status"] != "healthy"


def test_router_no_longer_references_the_deleted_scheduler():
    """Source guard: the attribute was never set, so a reader could not tell
    from behaviour alone that it was dead. Docstrings are stripped first — the
    prose that explains why it went is allowed to name it; executable code is
    not."""
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(mod))
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef)) or isinstance(
            node, ast.AsyncFunctionDef
        ):
            if node.body and isinstance(node.body[0], ast.Expr):
                first = node.body[0].value
                if isinstance(first, ast.Constant) and isinstance(first.value, str):
                    node.body.pop(0)
    code = ast.unparse(tree)
    assert "workforce_scheduler" not in code
    assert "health_snapshot" not in code


@pytest.mark.asyncio
async def test_db_unreachable_is_still_down(monkeypatch):
    def _boom():
        raise RuntimeError("no route to host")

    monkeypatch.setattr(mod, "read_scope", _boom)
    monkeypatch.setattr(mod, "dbos_is_launched", lambda: True)
    pool = MagicMock()
    pool.inflight_count = AsyncMock(return_value=0)
    monkeypatch.setattr(mod, "DbosAgentWorkforcePool", lambda: pool)

    with patch.object(mod, "logger", MagicMock()):
        out = await mod.workforce_healthz()

    assert out["status"] == "down"
    assert out["supabase"]["reachable"] is False


# ─── the dispatch probe (Important-4) ─────────────────────────────────


@pytest.mark.asyncio
async def test_a_stalled_dispatch_loop_is_reported_even_when_the_inbox_is_fine(
    monkeypatch,
):
    """The signal this endpoint was missing.

    The inbox throughput check reads agent_inbox: messages turning into task
    rows. That whole stage can be healthy while the NEXT stage — the workflow
    body's enqueue loop — throws on every order and swallows it per-order.
    Inbox counters look perfect, tasks pile up at queued, and the only number
    that moves is a gauge nothing thresholds. So the probe reads task_tracking
    directly: a queued, never-dispatched row older than a few ticks is a
    dispatch that is not happening."""
    _patch(
        monkeypatch,
        launched=True,
        inflight=12,
        counts=[2, 5, 0],  # agents exist, inbox draining, nothing pending
        oldest_undispatched_age_s=mod._DISPATCH_STALE_AFTER_S + 1,
    )

    out = await mod.workforce_healthz()

    assert out["status"] == "degraded"
    assert any("dispatch" in i.lower() for i in out["issues"])
    assert out["dispatcher"]["oldest_undispatched_age_s"] is not None


@pytest.mark.asyncio
async def test_a_freshly_queued_task_is_not_a_stall(monkeypatch):
    """The other direction. A row queued moments ago has not had its tick yet;
    calling that degraded would make the probe cry wolf every 10 seconds."""
    _patch(
        monkeypatch,
        launched=True,
        inflight=1,
        counts=[2, 5, 0],
        oldest_undispatched_age_s=1.0,
    )

    out = await mod.workforce_healthz()

    assert out["status"] == "healthy"
    assert out["issues"] == []


@pytest.mark.asyncio
async def test_no_undispatched_rows_at_all_is_healthy(monkeypatch):
    """None means the query found nothing to worry about — distinct from a
    number that happens to be small, and distinct from a failed read."""
    _patch(monkeypatch, launched=True, inflight=0, counts=[2, 5, 0])

    out = await mod.workforce_healthz()

    assert out["status"] == "healthy"
    assert out["dispatcher"]["oldest_undispatched_age_s"] is None


@pytest.mark.asyncio
async def test_dispatch_probe_failure_degrades_rather_than_reporting_healthy(
    monkeypatch,
):
    """A probe that cannot read must not answer 'fine'."""
    _patch(monkeypatch, launched=True, inflight=0, counts=[2, 5, 0])

    async def _boom():
        raise RuntimeError("pg gone")

    monkeypatch.setattr(mod, "_oldest_undispatched_age_s", _boom)

    out = await mod.workforce_healthz()

    assert out["status"] != "healthy"
    assert any("dispatch" in i.lower() for i in out["issues"])


@pytest.mark.asyncio
async def test_zero_persistent_agents_is_a_typed_degraded_reason(monkeypatch):
    """Task 7a defect 4: with no persistent agent every ``Delegate`` call is
    refused, and this endpoint — the one place that counts them — used to
    short-circuit its only alert on exactly that number and report healthy.
    A capability that cannot work must not read as fine."""
    _patch(monkeypatch, launched=True, inflight=0, counts=[0, 0, 0])
    out = await mod.workforce_healthz()
    assert out["supabase"]["persistent_agents"] == 0
    assert out["status"] == "degraded"
    assert any(str(i).startswith("no_persistent_agents") for i in out["issues"]), out[
        "issues"
    ]


@pytest.mark.asyncio
async def test_a_persistent_agent_clears_the_reason(monkeypatch):
    _patch(monkeypatch, launched=True, inflight=0, counts=[2, 0, 0])
    out = await mod.workforce_healthz()
    assert not any(str(i).startswith("no_persistent_agents") for i in out["issues"])
    assert out["status"] == "healthy"
