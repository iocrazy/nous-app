"""workflow_timeout_policy — per-workflow-type ceiling.

Today scheduled_recovery sweeper marks ANY task in 'processing' >10min
as stuck. But analyze (visual analysis on long video) legitimately
takes 60min; 10min is too aggressive. parse takes 30s — 10min is too
generous; a parse stuck for 5min is already a real failure.

Per-type ceilings let the sweeper apply the right threshold per task:

    parse              5 min   (yt-dlp metadata)
    download          30 min   (large video files)
    transcode         45 min   (4K HEVC encode)
    ai_transcription  15 min   (whisper on 30-min audio)
    ai_summary        10 min   (LLM summarize)
    ai_visual_analysis 60 min  (multimodal LLM on every keyframe)
    scheduled_sweep    2 min   (any scheduled job exceeding this is wedged)
    default           10 min

Mirrors OpenClaw cron/service/timeout-policy.ts.
"""
from __future__ import annotations

from datetime import timedelta

import pytest

from app.agent_framework.workflow_timeout_policy import (
    DEFAULT_TIMEOUT_MINUTES,
    is_stuck,
    timeout_for_task_type,
    timeout_minutes,
)


@pytest.mark.unit
@pytest.mark.parametrize(
    "task_type,expected",
    [
        ("parse", 5),
        ("download", 30),
        ("transcode", 45),
        ("ai_transcription", 15),
        ("ai_summary", 10),
        ("ai_visual_analysis", 60),
        ("scheduled_sweep", 2),
    ],
)
def test_timeout_minutes_known_types(task_type: str, expected: int):
    assert timeout_minutes(task_type) == expected


@pytest.mark.unit
def test_timeout_minutes_unknown_uses_default():
    assert timeout_minutes("totally-made-up-task-type") == DEFAULT_TIMEOUT_MINUTES


@pytest.mark.unit
def test_timeout_for_task_type_returns_timedelta():
    td = timeout_for_task_type("parse")
    assert isinstance(td, timedelta)
    assert td == timedelta(minutes=5)


@pytest.mark.unit
def test_is_stuck_short_running_not_stuck():
    """A parse running for 1 minute is NOT stuck (under 5min ceiling)."""
    assert is_stuck("parse", elapsed_seconds=60) is False


@pytest.mark.unit
def test_is_stuck_over_ceiling():
    """A parse running for 6 minutes IS stuck (over 5min ceiling)."""
    assert is_stuck("parse", elapsed_seconds=6 * 60) is True


@pytest.mark.unit
def test_is_stuck_analyze_60min_not_stuck():
    """Visual analysis legitimately takes 60min — should NOT be flagged
    as stuck at 30min."""
    assert is_stuck("ai_visual_analysis", elapsed_seconds=30 * 60) is False


@pytest.mark.unit
def test_is_stuck_analyze_75min_stuck():
    """Past the analyze ceiling, IS stuck."""
    assert is_stuck("ai_visual_analysis", elapsed_seconds=75 * 60) is True


@pytest.mark.unit
def test_is_stuck_unknown_type_uses_default_ceiling():
    """Unknown task type uses default 10min."""
    assert is_stuck("custom_task", elapsed_seconds=5 * 60) is False
    assert is_stuck("custom_task", elapsed_seconds=15 * 60) is True


@pytest.mark.unit
def test_is_stuck_negative_elapsed_not_stuck():
    """Defensive: clock skew / future timestamp returns not stuck."""
    assert is_stuck("parse", elapsed_seconds=-100) is False
