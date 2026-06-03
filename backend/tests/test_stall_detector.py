"""Unit tests for the worker-stall detector's pure decision logic."""

from app.startup.stall_detector import StallState, stall_step

OPTS = {"queue_min": 5, "stall_ticks": 3}


def test_no_stall_when_below_queue_min():
    st, action = stall_step(3, 0, StallState(), **OPTS)
    assert action is None
    assert st.streak == 0


def test_no_stall_when_workers_running():
    st, action = stall_step(50, 2, StallState(), **OPTS)
    assert action is None
    assert st.streak == 0


def test_alert_only_after_sustained_streak():
    st = StallState()
    # tick 1, 2 — building streak, no alert yet
    st, a1 = stall_step(10, 0, st, **OPTS)
    assert a1 is None and st.streak == 1
    st, a2 = stall_step(10, 0, st, **OPTS)
    assert a2 is None and st.streak == 2
    # tick 3 — crosses STALL_TICKS → alert
    st, a3 = stall_step(10, 0, st, **OPTS)
    assert a3 == "alert" and st.alerting is True and st.streak == 3


def test_alert_is_debounced_while_still_stalled():
    st = StallState(streak=3, alerting=True)
    st, action = stall_step(10, 0, st, **OPTS)
    assert action is None  # already alerted, don't repeat
    assert st.alerting is True


def test_recover_fires_once_when_queue_drains():
    st = StallState(streak=5, alerting=True)
    st, action = stall_step(10, 4, st, **OPTS)  # workers running again
    assert action == "recover"
    assert st.streak == 0 and st.alerting is False


def test_recover_not_fired_if_never_alerted():
    st = StallState(streak=2, alerting=False)
    st, action = stall_step(0, 0, st, **OPTS)
    assert action is None
    assert st.streak == 0 and st.alerting is False
