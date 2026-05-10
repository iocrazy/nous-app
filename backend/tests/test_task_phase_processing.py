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
  - parse_workflow + download_workflow both call a new
    `mark_workflow_processing_step` at body entry to transition phase.

These tests pin both pieces.
"""

from __future__ import annotations

import inspect


def test_taskphase_processing_value_is_processing():
    """Belt and braces — enum value must be 'processing' (matching
    the get_queue_status filter)."""
    from app.services.infra.unified_task_manager import TaskPhase

    assert TaskPhase.PROCESSING.value == "processing"


def test_get_queue_status_filters_on_processing():
    """`get_queue_status` must filter active tasks by phase='processing'
    — this is the regression we're guarding. Look for the actual
    `.eq("phase", "processing")` call rather than a naked substring,
    so the docstring discussing the historical 'in_progress' typo
    doesn't false-positive against itself."""
    from app.services.infra import system_monitor_service

    src = inspect.getsource(system_monitor_service.get_queue_status)
    assert '.eq("phase", "processing")' in src


def test_parse_workflow_calls_mark_processing():
    """parse_workflow body must call `mark_workflow_processing_step`
    so phase is bumped to 'processing' on dispatch. Source grep
    instead of mocking DBOS — the workflow runs in DBOS context
    only, so functional invocation is too brittle to test here."""
    from app.workflows import parse

    src = inspect.getsource(parse.parse_workflow)
    assert "mark_workflow_processing_step" in src


def test_download_workflow_calls_mark_processing():
    """Same contract for download_workflow — without this the WORKER
    stat ticks back to 'Idle' the moment a download takes over from
    parse."""
    from app.workflows import download

    src = inspect.getsource(download.download_workflow)
    assert "mark_workflow_processing_step" in src
