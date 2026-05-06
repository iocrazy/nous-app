"""workflow_timeout_policy — per-workflow-type ceiling.

The reaper sweeper today (scheduled_recovery.reap_stuck_pending_tasks)
treats every task the same: anything in 'processing' for >10min gets
marked failed. That's wrong:
  - parse stuck for 5min is already a real failure (parse is a 30s job)
  - ai_visual_analysis at 30min is normal (60min ceiling)

Per-type ceilings let the sweeper apply the right threshold per task,
calibrated against actual mediahub workload patterns.

Mirrors OpenClaw ``cron/service/timeout-policy.ts``.

Usage:
    from app.agent_framework import is_stuck, timeout_minutes

    # Inside the reaper:
    elapsed = (now - task.started_at).total_seconds()
    if is_stuck(task.task_type, elapsed_seconds=elapsed):
        await mark_failed(task, reason="stuck_timeout")
"""

from __future__ import annotations

from datetime import timedelta

# Per-type ceilings in MINUTES. Calibrated against current production
# task patterns; revise as workload changes.
_TIMEOUT_MINUTES: dict[str, int] = {
    "parse": 5,  # yt-dlp metadata fetch
    "download": 30,  # large video file download
    "transcode": 45,  # 4K HEVC encode
    "ai_transcription": 15,  # whisper on 30-min audio
    "ai_summary": 10,  # LLM summarize transcript
    "ai_visual_analysis": 60,  # multimodal LLM on every keyframe
    "ai_analyze_l1": 30,  # L1 analyze
    "ai_analyze_l2": 60,  # L2 analyze (extended)
    "thumbnail": 5,  # ffmpeg keyframe extraction
    "storyboard": 30,  # storyboard ai
    "storyboard_video_analysis": 60,
    "storyboard_scene_detect": 30,
    "scheduled_sweep": 2,  # any sweeper exceeding this is wedged
    "agent_run": 10,  # LLM agent turn
}

# Fallback for unknown task types — generous so a new task type
# launching with no policy entry doesn't immediately get reaped.
DEFAULT_TIMEOUT_MINUTES: int = 10


def timeout_minutes(task_type: str) -> int:
    """Return the configured ceiling in minutes for ``task_type``.
    Unknown types fall back to ``DEFAULT_TIMEOUT_MINUTES``."""
    return _TIMEOUT_MINUTES.get(task_type, DEFAULT_TIMEOUT_MINUTES)


def timeout_for_task_type(task_type: str) -> timedelta:
    """Convenience: return ``timedelta`` for ``task_type``."""
    return timedelta(minutes=timeout_minutes(task_type))


def is_stuck(task_type: str, *, elapsed_seconds: float) -> bool:
    """Return True if ``elapsed_seconds`` exceeds the type's ceiling.

    Defensive: negative elapsed (clock skew) returns False — better to
    skip the reap than to mark something failed based on broken time.
    """
    if elapsed_seconds < 0:
        return False
    return elapsed_seconds > timeout_minutes(task_type) * 60
