"""Regression tests for task_tracking.phase='processing' transition.

Background — bug surfaced 2026-05-08:
TaskMonitor's WORKER stat showed "Idle" while a parse / download
workflow was actively running. Two-part root cause:

  1. `get_queue_status()` filtered by `phase='in_progress'`, but the
     enum value is `'processing'` — that COUNT was always zero.
  2. Nothing in the workflow body called `manager.start()` to
     transition phase from 'queued' → 'processing' in the first
     place. The `mirror_dbos_lifecycle_to_tracking` trigger only
     writes the `status` column; `phase` stayed at the value
     unified_task_manager.create() set at insert time.

Fix:
  - `get_queue_status` filter changed to 'processing'.
  - parse_workflow + download_workflow each call a per-workflow
    "mark processing" DBOS step at body entry to transition phase
    (renamed from a single shared `mark_workflow_processing_step`
    to `mark_parse_processing_step` / `mark_download_processing_step`
    in the post-PR-283 cleanup so DBOS doesn't warn about a duplicate
    step name registration).

These tests pin both pieces.
"""

from __future__ import annotations

import inspect


def test_taskphase_processing_value_is_processing():
    """Belt and braces — enum value must be 'processing' (matching
    the get_queue_status filter)."""
    from app.services.infra.unified_task_manager import TaskPhase

    assert TaskPhase.PROCESSING.value == "processing"


def test_get_queue_status_counts_every_live_phase_as_active():
    """`get_queue_status`'s active count must cover every live phase.

    This assertion used to pin the literal ``TaskTracking.phase ==
    "processing"``. That was the 2026-05-08 hand-fix for the original
    'in_progress' typo, and pinning it froze half the answer: 'processing' is
    what ``manager.start()`` writes, but the DB trigger independently writes
    'in_progress' (and the manager parks tasks at 'dedup_check'), so the
    counter still under-reported. The vocabulary now has exactly one home —
    assert the code READS it rather than re-typing any word here, or this
    test becomes another copy of the thing it is guarding
    (see tests/test_task_phase_vocabulary.py)."""
    from app.services.infra import system_monitor_service
    from app.services.infra.unified_task_manager import ACTIVE_PHASES, TaskPhase

    src = inspect.getsource(system_monitor_service.get_queue_status)
    assert "ACTIVE_PHASES" in src, (
        "active count must derive from unified_task_manager.ACTIVE_PHASES, "
        "not a hand-written phase literal"
    )
    assert "WHERE phase = 'processing'" not in src  # the old text() branch is gone

    # And the set it derives must really contain the phase a running workflow
    # sits at — otherwise "reads the constant" would be satisfied by an empty one.
    assert TaskPhase.PROCESSING.value in ACTIVE_PHASES


def test_parse_workflow_calls_mark_processing():
    """parse_workflow body must call `mark_parse_processing_step` so
    phase is bumped to 'processing' on dispatch. Source grep instead of
    mocking DBOS — the workflow runs in DBOS context only, so functional
    invocation is too brittle to test here."""
    from app.workflows import parse

    src = inspect.getsource(parse.parse_workflow)
    assert "mark_parse_processing_step" in src


def test_download_workflow_calls_mark_processing():
    """Same contract for download_workflow (`mark_download_processing_step`).
    Without this the WORKER stat ticks back to 'Idle' the moment a download
    takes over from parse."""
    from app.workflows import download

    src = inspect.getsource(download.download_workflow)
    assert "mark_download_processing_step" in src
