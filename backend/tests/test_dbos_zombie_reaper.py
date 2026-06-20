"""Zombie reaper — `_is_permanent_dbos_orphan` decides which
`dbos.workflow_status` rows are permanently-orphaned USER workflows safe to
cancel. Pure helper so it's testable without a DB.

A zombie = PENDING/ENQUEUED + user-facing (not internal queue / not sched-*)
+ permanently orphaned (post-deploy version-orphan OR frozen past max-age).
"""

import app.workflows.workflow_health_sweeper as sweeper

_MAX_AGE = 6 * 3600.0


def _orphan(**kw):
    """Build args with sensible defaults; override per-case."""
    base = dict(
        status="PENDING",
        queue_name="download_user",
        name="download_workflow",
        app_version="old_sha",
        current_version="new_sha",
        age_seconds=10.0,
        max_age_seconds=_MAX_AGE,
    )
    base.update(kw)
    return sweeper._is_permanent_dbos_orphan(
        base["status"],
        base["queue_name"],
        base["name"],
        base["app_version"],
        base["current_version"],
        base["age_seconds"],
        base["max_age_seconds"],
    )


# ── version-orphan: the precise signal ─────────────────────────────────
def test_version_orphan_pending_is_zombie():
    # PENDING on a real queue tagged with an old version → no live executor.
    assert _orphan(app_version="old_sha", current_version="new_sha") is True


def test_version_orphan_enqueued_is_zombie():
    assert _orphan(status="ENQUEUED") is True


def test_matching_version_young_is_not_zombie():
    # Same version + young → DBOS will still run it / real backlog. Not a zombie.
    assert _orphan(app_version="v1", current_version="v1", age_seconds=30.0) is False


# ── age backstop (same / unknown version) ──────────────────────────────
def test_same_version_frozen_past_max_age_is_zombie():
    assert (
        _orphan(app_version="v1", current_version="v1", age_seconds=_MAX_AGE + 1)
        is True
    )


def test_unknown_version_only_age_decides():
    # current_version unknown (dev / no build-info) → never cancel young work,
    # only the age backstop applies.
    assert _orphan(current_version=None, age_seconds=30.0) is False
    assert _orphan(current_version=None, age_seconds=_MAX_AGE + 1) is True
    # workflow's own version unknown → same: age-only.
    assert _orphan(app_version=None, age_seconds=30.0) is False


# ── terminal statuses are never zombies ────────────────────────────────
def test_terminal_status_is_never_zombie():
    for s in ("SUCCESS", "ERROR", "CANCELLED", "", None):
        assert _orphan(status=s, age_seconds=_MAX_AGE + 1) is False


# ── internal / scheduled workflows are out of scope ────────────────────
def test_internal_queue_excluded():
    # Even a version-orphan on the internal queue is the pre-launch sweep's job.
    assert _orphan(queue_name="_dbos_internal_queue") is False


def test_scheduled_workflow_excluded():
    assert _orphan(name="sched-workflow_health_sweeper-2026") is False


def test_case_insensitive_status():
    assert _orphan(status="pending") is True
