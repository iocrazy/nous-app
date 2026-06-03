"""G1 + G2 + G3 — sweeper must not WRONGLY mark long-running tasks LOST.

These tests target the pure decision helpers so they need no DB:
  * G1 — `_classify_in_python` must NOT return LOST on a NULL heartbeat
    just because elapsed > heartbeat_stale. Downloads write no heartbeat,
    so the heartbeat signal is meaningless for them — fall back to the
    absolute `hard` ceiling on `elapsed`.
  * G2 — `within_boot_grace` gate makes the sweeper/reaper skip LOST marking
    right after a process restart.
  * G3 — `_dbos_claims_workflow` decides whether DBOS still owns a row.
"""

from datetime import datetime, timedelta, timezone

import app.workflows.workflow_health_sweeper as sweeper


def _row(**over):
    now = datetime.now(timezone.utc)
    base = {
        "dbos_workflow_id": "wf-1",
        "task_type": "download",
        "phase": "processing",
        "started_at": now - timedelta(seconds=900),  # 15 min in
        "heartbeat_at": None,  # downloads write NO heartbeat
        "progress": 0.5,
        "updated_at": now - timedelta(seconds=10),
        "max_duration_minutes": None,
        "do_not_auto_cancel": False,
        "health_status": None,
        "user_id": "u-1",
        "title": "Big playlist",
    }
    base.update(over)
    return base


def _set_download_policy():
    # Mirrors migration 201 seed for 'download'.
    sweeper._POLICY_CACHE = {
        "download": {"expected": 1800, "hard": 14400, "heartbeat_stale": 600},
    }


# ── G1 ────────────────────────────────────────────────────────────────


def test_null_heartbeat_not_lost_under_hard_ceiling():
    """15 min into a download (heartbeat NULL, elapsed > heartbeat_stale=600
    but < hard=14400): must NOT be LOST. This is the bug."""
    _set_download_policy()
    out = sweeper._classify_in_python(_row())
    assert out != "LOST", f"download at 15min wrongly classified {out}"


def test_null_heartbeat_lost_past_hard_ceiling():
    """Past the absolute hard ceiling (4h+) with NULL heartbeat → genuinely
    LOST (the legit-LOST case must still work)."""
    _set_download_policy()
    now = datetime.now(timezone.utc)
    out = sweeper._classify_in_python(
        _row(started_at=now - timedelta(seconds=14400 + 60))
    )
    assert out == "LOST"


def test_present_heartbeat_still_lost_when_stale():
    """When a heartbeat IS present and stale beyond heartbeat_stale, LOST
    behavior is unchanged."""
    _set_download_policy()
    now = datetime.now(timezone.utc)
    out = sweeper._classify_in_python(
        _row(
            task_type="transcription",  # not in cache → unknown
        )
    )
    # unknown type → HEALTHY (benefit of doubt) — separate path
    assert out == "HEALTHY"

    sweeper._POLICY_CACHE = {
        "transcription": {"expected": 1800, "hard": 14400, "heartbeat_stale": 600},
    }
    out = sweeper._classify_in_python(
        _row(
            task_type="transcription",
            heartbeat_at=now - timedelta(seconds=700),  # stale heartbeat
            started_at=now - timedelta(seconds=700),
        )
    )
    assert out == "LOST"
