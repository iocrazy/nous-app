"""Spec-2 slice 3: stranded-issue monitor decision core.

`decide_stranded_action` is the loop-safety heart — it decides redispatch vs
giveup vs skip from an issue's state + the three guards (DBOS ownership, wall
clock, per-issue redispatch cap, cooldown). Pure + exhaustively tested; the
DBOS/DB wiring around it is thin.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.workflows.stranded_issue_monitor import decide_stranded_action

NOW = datetime(2026, 6, 14, 12, 0, 0, tzinfo=timezone.utc)


def _decide(**kw):
    base = dict(
        now=NOW,
        started_at=NOW - timedelta(minutes=5),
        redispatch_count=0,
        last_dispatched_at=None,
        dbos_owned=False,
        max_redispatch=2,
        cooldown_s=300,
        wall_clock_s=1800,
    )
    base.update(kw)
    return decide_stranded_action(**base)


def test_redispatch_when_clean():
    action, reason = _decide()
    assert action == "redispatch"


def test_skip_when_dbos_still_owns():
    # DBOS will recover the workflow itself — never double-dispatch.
    action, reason = _decide(dbos_owned=True)
    assert action == "skip"
    assert reason == "dbos_owns"


def test_dbos_owns_takes_priority_over_everything():
    # Even if wall-clock + cap exceeded, an owned workflow is left alone.
    action, _ = _decide(
        dbos_owned=True,
        started_at=NOW - timedelta(hours=2),
        redispatch_count=99,
    )
    assert action == "skip"


def test_giveup_when_wall_clock_exceeded():
    action, reason = _decide(started_at=NOW - timedelta(minutes=31))
    assert action == "giveup"
    assert reason == "wall_clock_exceeded"


def test_giveup_when_redispatch_cap_hit():
    action, reason = _decide(redispatch_count=2)
    assert action == "giveup"
    assert reason == "max_redispatch"


def test_skip_during_cooldown():
    action, reason = _decide(last_dispatched_at=NOW - timedelta(minutes=2))
    assert action == "skip"
    assert reason == "cooldown"


def test_redispatch_after_cooldown_elapsed():
    action, _ = _decide(last_dispatched_at=NOW - timedelta(minutes=6))
    assert action == "redispatch"


def test_wall_clock_beats_cap_and_cooldown():
    # Ordering: a too-old issue gives up even if it also looks cooldown/cap-bound.
    action, reason = _decide(
        started_at=NOW - timedelta(minutes=40),
        redispatch_count=2,
        last_dispatched_at=NOW - timedelta(seconds=1),
    )
    assert action == "giveup"
    assert reason == "wall_clock_exceeded"


def test_missing_started_at_does_not_crash():
    # No started_at → wall-clock guard can't fire; falls through to redispatch.
    action, _ = _decide(started_at=None)
    assert action == "redispatch"
