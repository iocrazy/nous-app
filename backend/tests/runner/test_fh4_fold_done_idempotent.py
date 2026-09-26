"""fh4 T2 (E2d): a second ``subagent_done`` for a child already settled is a
no-op for the counters.

A replayed DBOS step, the reaper racing the worker, or a re-fold of stored
views can each deliver one child's ``done`` twice. Before fh4 ``fold_done``
raised ``done`` and ``total`` on every arrival, so one child read as two.
"""

import pytest

from app.services.ai.runner import run_projection as rp

pytestmark = pytest.mark.unit


def _done(**kw):
    base = {"mode": "async", "status": "success", "cost_cents": 2.0}
    base.update(kw)
    return base


def test_async_done_twice_for_one_task_counts_once():
    v = rp.apply(
        rp.empty_views(),
        "subagent_spawned",
        {"child_run_id": None, "task_id": "t1", "mode": "async"},
    )
    v = rp.apply(v, "subagent_done", _done(child_run_id="52", task_id="t1"))
    once = dict(v["view"]["children"])
    # The replayed step re-ran the child: same task, a DIFFERENT run id.
    v = rp.apply(
        v, "subagent_done", _done(child_run_id="53", task_id="t1", status="failed")
    )
    again = v["view"]["children"]
    for key in ("total", "done", "running", "async_pending"):
        assert again[key] == once[key], key
    assert again["last"] == once["last"]


def test_sync_done_twice_for_one_child_counts_once():
    v = rp.apply(
        rp.empty_views(),
        "subagent_spawned",
        {"child_run_id": "51", "task_id": None, "mode": "sync"},
    )
    v = rp.apply(v, "subagent_done", _done(child_run_id="51", mode="sync"))
    v = rp.apply(v, "subagent_done", _done(child_run_id="51", mode="sync"))
    c = v["view"]["children"]
    assert (c["total"], c["done"], c["running"]) == (1, 1, 0)


def test_two_different_children_still_count_twice():
    """Negative control: dedup is per child, not per event type."""
    v = rp.empty_views()
    v = rp.apply(v, "subagent_done", _done(child_run_id="52", task_id="t1"))
    v = rp.apply(v, "subagent_done", _done(child_run_id="53", task_id="t2"))
    assert v["view"]["children"]["done"] == 2
    assert v["view"]["children"]["total"] == 2
