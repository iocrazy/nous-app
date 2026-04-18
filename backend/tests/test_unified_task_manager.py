"""Unit tests for UnifiedTaskManager pure helpers.

Covers the non-I/O parts: phase transition validation, dedup-key
construction, error classification, singleton behavior, and the
phase→legacy-status mapping.
"""

from __future__ import annotations

import pytest

from app.services.unified_task_manager import (
    TaskPhase,
    UnifiedTaskManager,
    VALID_TRANSITIONS,
    _PHASE_TO_STATUS,
    get_task_manager,
)


# ─── Phase transitions ─────────────────────────────────────────────


class TestValidateTransition:
    def test_queued_to_processing_allowed(self) -> None:
        mgr = UnifiedTaskManager()
        mgr._validate_transition(TaskPhase.QUEUED, TaskPhase.PROCESSING)

    def test_processing_to_completed_allowed(self) -> None:
        mgr = UnifiedTaskManager()
        mgr._validate_transition(TaskPhase.PROCESSING, TaskPhase.COMPLETED)

    def test_completed_is_terminal(self) -> None:
        mgr = UnifiedTaskManager()
        with pytest.raises(ValueError, match="Invalid phase transition"):
            mgr._validate_transition(TaskPhase.COMPLETED, TaskPhase.PROCESSING)

    def test_cancelled_is_terminal(self) -> None:
        mgr = UnifiedTaskManager()
        with pytest.raises(ValueError):
            mgr._validate_transition(TaskPhase.CANCELLED, TaskPhase.PROCESSING)

    def test_failed_can_retry_to_queued(self) -> None:
        mgr = UnifiedTaskManager()
        mgr._validate_transition(TaskPhase.FAILED, TaskPhase.QUEUED)

    def test_failed_cannot_jump_to_completed(self) -> None:
        mgr = UnifiedTaskManager()
        with pytest.raises(ValueError):
            mgr._validate_transition(TaskPhase.FAILED, TaskPhase.COMPLETED)


def test_valid_transitions_covers_all_phases() -> None:
    # Every TaskPhase must have an entry in VALID_TRANSITIONS.
    for phase in TaskPhase:
        assert phase in VALID_TRANSITIONS


# ─── Phase → legacy status ─────────────────────────────────────────


def test_phase_to_status_maps_every_phase() -> None:
    for phase in TaskPhase:
        assert phase in _PHASE_TO_STATUS


def test_phase_to_status_values() -> None:
    assert _PHASE_TO_STATUS[TaskPhase.QUEUED] == "pending"
    assert _PHASE_TO_STATUS[TaskPhase.DEDUP_CHECK] == "pending"
    assert _PHASE_TO_STATUS[TaskPhase.PROCESSING] == "processing"
    assert _PHASE_TO_STATUS[TaskPhase.COMPLETED] == "completed"
    assert _PHASE_TO_STATUS[TaskPhase.FAILED] == "failed"
    assert _PHASE_TO_STATUS[TaskPhase.CANCELLED] == "cancelled"


# ─── Dedup key ─────────────────────────────────────────────────────


class TestMakeDedupKey:
    def test_format(self) -> None:
        assert (
            UnifiedTaskManager.make_dedup_key("download", "7412345678901234")
            == "task:download:7412345678901234"
        )

    def test_different_types_produce_different_keys(self) -> None:
        a = UnifiedTaskManager.make_dedup_key("parse", "x")
        b = UnifiedTaskManager.make_dedup_key("download", "x")
        assert a != b


# ─── Error classification ──────────────────────────────────────────


class TestClassifyError:
    def test_network_timeout(self) -> None:
        assert UnifiedTaskManager.classify_error(Exception("connection timed out")) == "NETWORK_TIMEOUT"

    def test_resource_404(self) -> None:
        assert UnifiedTaskManager.classify_error(Exception("HTTP 404 Not Found")) == "RESOURCE_404"

    def test_rate_limited(self) -> None:
        assert UnifiedTaskManager.classify_error(Exception("429 too many requests")) == "RATE_LIMITED"

    def test_transcode_failed(self) -> None:
        assert UnifiedTaskManager.classify_error(Exception("ffmpeg exited with code 1")) == "TRANSCODE_FAILED"

    def test_ai_quota_exceeded_on_ai_keyword(self) -> None:
        # 'openai' keyword falls through to AI_QUOTA_EXCEEDED branch
        assert (
            UnifiedTaskManager.classify_error(Exception("openai something"))
            == "AI_QUOTA_EXCEEDED"
        )

    def test_ai_quota_exceeded_on_explicit_msg(self) -> None:
        assert (
            UnifiedTaskManager.classify_error(Exception("ai quota exhausted"))
            == "AI_QUOTA_EXCEEDED"
        )

    def test_storage_full(self) -> None:
        assert UnifiedTaskManager.classify_error(Exception("disk full")) == "STORAGE_FULL"

    def test_ai_storage_full_becomes_ai_quota(self) -> None:
        # "openai ... quota exceeded" → AI_QUOTA_EXCEEDED branch
        assert (
            UnifiedTaskManager.classify_error(Exception("openai quota exceeded"))
            == "AI_QUOTA_EXCEEDED"
        )

    def test_unknown_fallback(self) -> None:
        assert UnifiedTaskManager.classify_error(Exception("mystery")) == "UNKNOWN"


# ─── Singleton ─────────────────────────────────────────────────────


def test_get_task_manager_returns_singleton() -> None:
    a = get_task_manager()
    b = get_task_manager()
    assert a is b


def test_singleton_is_unified_task_manager_instance() -> None:
    mgr = get_task_manager()
    assert isinstance(mgr, UnifiedTaskManager)
