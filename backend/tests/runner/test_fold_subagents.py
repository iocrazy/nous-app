"""``subagent_spawned`` / ``subagent_done`` → ``view.children`` + ``cost.by_child``."""

import pytest

from app.services.ai.runner import run_projection as rp

pytestmark = pytest.mark.unit


def _sp(mode, cid=None, tid=None):
    return {
        "child_run_id": cid,
        "task_id": tid,
        "mode": mode,
        "subagent_type": "librarian",
        "description": "d",
    }


def test_sync_spawn_counts_running_then_done_moves_it():
    v = rp.apply(rp.empty_views(), "subagent_spawned", _sp("sync", cid="51"))
    assert v["view"]["children"] == {
        "total": 1,
        "done": 0,
        "running": 1,
        "async_pending": 0,
        "last": {
            "child_run_id": "51",
            "subagent_type": "librarian",
            "status": "running",
        },
    }
    v = rp.apply(
        v,
        "subagent_done",
        {"child_run_id": "51", "mode": "sync", "status": "success", "cost_cents": 3.0},
    )
    assert v["view"]["children"]["done"] == 1
    assert v["view"]["children"]["running"] == 0
    assert v["cost"]["by_child"] == {"51": 3.0}
    assert v["cost"]["spent_cents"] == 3.0


def test_async_spawn_is_pending_until_done():
    v = rp.apply(rp.empty_views(), "subagent_spawned", _sp("async", tid="t1"))
    assert v["view"]["children"]["async_pending"] == 1
    assert v["view"]["children"]["running"] == 0
    assert v["view"]["children"]["last"]["status"] == "queued"
    v = rp.apply(
        v,
        "subagent_done",
        {
            "child_run_id": "52",
            "task_id": "t1",
            "mode": "async",
            "status": "failed",
        },
    )
    assert v["view"]["children"]["done"] == 1
    assert v["view"]["children"]["async_pending"] == 0
    assert v["view"]["children"]["last"]["status"] == "failed"


def test_a_done_with_no_spawned_still_counts():
    """The background ``done`` is written by the worker onto the PARENT run,
    which may have ended turns ago — its ``spawned`` is not in these views."""
    v = rp.apply(
        rp.empty_views(),
        "subagent_done",
        {"child_run_id": "52", "mode": "async", "status": "success"},
    )
    assert v["view"]["children"]["total"] == 1
    assert v["view"]["children"]["done"] == 1
    assert v["view"]["children"]["async_pending"] == 0


def test_bad_payload_changes_nothing():
    base = rp.empty_views()
    assert rp.apply(base, "subagent_done", {"mode": "sync"}) is base
    assert rp.apply(base, "subagent_spawned", {"mode": "whatever"}) is base


def test_empty_views_carry_the_five_child_keys_and_by_child():
    v = rp.empty_views()
    assert v["view"]["children"] == {
        "total": 0,
        "done": 0,
        "running": 0,
        "async_pending": 0,
        "last": None,
    }
    assert v["cost"]["by_child"] == {}
