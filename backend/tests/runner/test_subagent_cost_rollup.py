"""Review I3: the view and the column must name the same number.

``issue_rollup._run_cents`` reads ``metadata_json.cost.spent_cents`` while a
run is running and ``agent_runs.cost_cents`` once it has finished. Before this
fix a sub-agent's spend lived only in the view, so an issue's ``spent_cents``
DROPPED by the children's cost the moment the parent run completed — a spend
gauge that walks backwards, feeding the budget gate.
"""

from __future__ import annotations

import pytest

from app.services.ai.runner import run_projection as rp
from app.services.issues.issue_rollup import _run_cents

pytestmark = pytest.mark.unit


def _views_with(own: float, children: dict[str, float]):
    views = rp.empty_views()
    if own:
        views = rp.apply(
            views,
            "step_end",
            {"turn": 1, "step": 1, "cost_cents": own, "model": "m"},
        )
    for cid, cents in children.items():
        views = rp.apply(
            views,
            "subagent_done",
            {
                "child_run_id": cid,
                "mode": "async",
                "status": "success",
                "cost_cents": cents,
            },
        )
    return views


def test_spent_is_own_plus_children():
    views = _views_with(10.0, {"51": 3.0, "52": 2.0})
    assert views["cost"]["own_cents"] == 10.0
    assert views["cost"]["by_child"] == {"51": 3.0, "52": 2.0}
    assert views["cost"]["spent_cents"] == 15.0


def test_a_repeated_done_for_the_same_child_does_not_double_add():
    """The worker writes ``subagent_done`` onto the parent run, and a DBOS
    step can replay. Adding on every arrival would inflate the parent's cost
    once per retry."""
    views = _views_with(10.0, {"51": 3.0})
    again = rp.apply(
        views,
        "subagent_done",
        {"child_run_id": "51", "mode": "async", "status": "success", "cost_cents": 3.0},
    )
    assert again["cost"]["by_child"] == {"51": 3.0}
    assert again["cost"]["spent_cents"] == 13.0


def test_own_cost_still_drives_the_budget_percentage():
    views = rp.empty_views()
    views["cost"]["budget_cents"] = 100.0
    views = rp.apply(
        views, "step_end", {"turn": 1, "step": 1, "cost_cents": 10.0, "model": "m"}
    )
    views = rp.apply(
        views,
        "subagent_done",
        {"child_run_id": "51", "mode": "async", "status": "ok", "cost_cents": 5.0},
    )
    assert views["cost"]["spent_cents"] == 15.0
    assert views["cost"]["pct"] == 15


def test_the_rollup_reads_the_same_number_before_and_after_completion():
    """The whole point of I3, asserted through the real consumer."""
    views = _views_with(10.0, {"51": 3.0, "52": 2.0})
    running = {"status": "running", "metadata_json": {"cost": views["cost"]}}
    # What RunRecorder._finish must now write into the column.
    finished = {
        "status": "completed",
        "metadata_json": {"cost": views["cost"]},
        "cost_cents": views["cost"]["spent_cents"],
    }
    assert _run_cents(running) == _run_cents(finished) == 15.0


# ─── the column side ─────────────────────────────────────────────────


async def test_finish_writes_own_plus_children_into_the_column(monkeypatch):
    """``compute_cost_cents`` only ever knew this run's own tokens. A parent
    that spent 10 and whose children spent 5 used to store 10 and show 15."""
    import contextlib

    from app.db import session as dbs
    from app.services.ai.runner import run_recorder as rr

    captured: dict = {}

    class _S:
        async def execute(self, stmt, *a, **k):
            compiled = stmt.compile()
            captured.setdefault("values", {}).update(
                {str(k): v for k, v in (compiled.params or {}).items()}
            )
            return None

    @contextlib.asynccontextmanager
    async def _ws():
        yield _S()

    monkeypatch.setattr(dbs, "write_scope", _ws)

    rec = rr.RunRecorder(agent_id=None, user_id=None, trigger="t")
    rec.run_id = "7"
    rec._prompt_rate = 1.0
    rec._completion_rate = 1.0
    rec.record_usage(prompt_tokens=5000, completion_tokens=5000)  # → 10.0 cents
    writer = rec._writer()
    writer.views["cost"]["by_child"] = {"51": 3.0, "52": 2.0}
    writer._mirror = _noop_mirror()

    await rec._finish(status="completed")

    assert captured["values"]["cost_cents"] == 15.0
    # The view must land on the same number, or the rollup still steps at
    # completion — just in the other direction.
    assert writer.views["cost"]["spent_cents"] == 15.0
    assert writer.views["cost"]["own_cents"] == 10.0


def _noop_mirror():
    from unittest.mock import AsyncMock

    return AsyncMock()


async def test_a_late_child_done_rewrites_the_finished_parents_column(monkeypatch):
    """The background ``subagent_done`` lands on a parent that finished turns
    ago. Folding it into a view nobody reads any more is not enough — the
    column is what the rollup reads for an ended run."""
    import contextlib
    from unittest.mock import AsyncMock

    from app.db import session as dbs
    from app.services.ai.runner import run_recorder as rr

    seen: list = []

    class _S:
        async def execute(self, stmt, *a, **k):
            seen.append(stmt)
            return None

    @contextlib.asynccontextmanager
    async def _ws():
        yield _S()

    monkeypatch.setattr(dbs, "write_scope", _ws)

    writer = rr.RunEventWriter(7, seq_start=3)
    writer.views["cost"]["own_cents"] = 10.0
    writer._mirror = AsyncMock()

    await writer.append(
        "subagent_done",
        {"child_run_id": "51", "mode": "async", "status": "success", "cost_cents": 4.0},
    )

    assert writer.views["cost"]["spent_cents"] == 14.0
    cost_updates = [s for s in seen if "cost_cents" in str(s.compile().params or {})]
    assert cost_updates, "no UPDATE carried the recomputed cost_cents"
    assert float(cost_updates[-1].compile().params["cost_cents"]) == 14.0


async def test_for_run_seeds_own_cents_from_a_pre_change_runs_total(monkeypatch):
    """A run finished before ``own_cents`` existed stored a ``spent_cents``
    that WAS its own (no fold could add a child then). Reading it as 0 would
    let the next late child done overwrite the column with children only."""
    import contextlib

    from app.db import session as dbs
    from app.services.ai.runner import run_recorder as rr

    class _Res:
        def __init__(self, v):
            self._v = v

        def scalar_one_or_none(self):
            return self._v

        def scalar(self):
            return self._v

    class _S:
        async def execute(self, stmt, *a, **k):
            if "max(" in str(stmt.compile()).lower():
                return _Res(41)
            return _Res({"view": {}, "cost": {"spent_cents": 12.5}})

    @contextlib.asynccontextmanager
    async def _rs():
        yield _S()

    monkeypatch.setattr(dbs, "read_scope", _rs)
    w = await rr.RunEventWriter.for_run(7)
    assert w.views["cost"]["own_cents"] == 12.5
    assert w.views["cost"]["spent_cents"] == 12.5


def test_the_issue_queries_still_exclude_child_runs():
    """Load-bearing since review I2 gave background children an ``issue_id``.

    A child's spend reaches the issue exactly once — through its parent's
    ``by_child`` and so through the parent's ``cost_cents``. If either of
    these two queries stopped filtering to root runs, the child's own row
    would be summed a second time and every fan-out would double-bill.
    """
    import inspect

    from app.repositories import agent_runs_repository as repo

    for fn in (
        repo.AgentRunsRepository.list_for_issue,
        repo.AgentRunsRepository.spent_cents_for_issue,
    ):
        src = inspect.getsource(fn)
        assert "parent_run_id.is_(None)" in src, fn.__name__
