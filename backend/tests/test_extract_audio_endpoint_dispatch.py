"""Regression tests for the POST /{platform_id}/extract-audio endpoint.

Background (2026-06-10): the endpoint created its task_tracking row via
``tracker.create()`` WITHOUT ``dbos_workflow_id`` — that column is the
NOT NULL primary key, so every create failed with 23502, the exception
was swallowed into a warning, and the toast said "Audio extraction
started" while Task Center showed nothing. The endpoint then ran its own
inline ffmpeg fork that never updated ``parsed_media.extract_audio_status``
(stuck 'pending' forever) and never chained transcript/summary.

Pinned contract (same rule as reference_dbos_dispatch_endpoint_wf_id):
the endpoint must dispatch the real ``extract_audio_workflow`` with a
pre-created task_tracking row whose ``dbos_workflow_id`` MATCHES the
dispatched ``workflow_id``.
"""

from __future__ import annotations

import importlib
import inspect


def _endpoint_source() -> str:
    mod = importlib.import_module("app.api.media_fetch_router")
    return inspect.getsource(mod.extract_audio)


def test_extract_audio_creates_row_with_workflow_id() -> None:
    source = _endpoint_source()
    assert "dbos_workflow_id=wf_id" in source, (
        "task_tracking.dbos_workflow_id is the NOT NULL PK — create() "
        "without it fails 23502 and the task silently never appears in "
        "Task Center (2026-06-10 regression)."
    )
    assert "workflow_id=wf_id" in source, (
        "the dispatched workflow_id must match the pre-created row's "
        "dbos_workflow_id, or the lifecycle trigger can't associate them "
        "and the task never completes."
    )


def test_extract_audio_dispatches_real_workflow_not_inline_ffmpeg() -> None:
    source = _endpoint_source()
    assert "extract_audio_workflow" in source and "start_workflow_routed" in source, (
        "the endpoint must dispatch extract_audio_workflow — the inline "
        "ffmpeg fork never updated extract_audio_status and never chained "
        "transcript/summary."
    )
    assert "subprocess" not in source, (
        "no inline subprocess/ffmpeg in the API layer — extraction logic "
        "lives in extract_audio_workflow / "
        "download_helpers.extract_audio_from_video."
    )
