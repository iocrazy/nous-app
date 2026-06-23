"""Unit tests for UnifiedTaskManager pure helpers.

Covers the non-I/O parts: phase transition validation, dedup-key
construction, error classification, singleton behavior, and the
phase→legacy-status mapping.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.infra.unified_task_manager import (
    _PHASE_TO_STATUS,
    VALID_TRANSITIONS,
    TaskPhase,
    UnifiedTaskManager,
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
        assert (
            UnifiedTaskManager.classify_error(Exception("connection timed out"))
            == "NETWORK_TIMEOUT"
        )

    def test_resource_404(self) -> None:
        assert (
            UnifiedTaskManager.classify_error(Exception("HTTP 404 Not Found"))
            == "RESOURCE_404"
        )

    def test_rate_limited(self) -> None:
        assert (
            UnifiedTaskManager.classify_error(Exception("429 too many requests"))
            == "RATE_LIMITED"
        )

    def test_transcode_failed(self) -> None:
        assert (
            UnifiedTaskManager.classify_error(Exception("ffmpeg exited with code 1"))
            == "TRANSCODE_FAILED"
        )

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
        assert (
            UnifiedTaskManager.classify_error(Exception("disk full")) == "STORAGE_FULL"
        )

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


# ─── _build_row (pure) ─────────────────────────────────────────────


class TestBuildRow:
    def test_minimal_defaults(self) -> None:
        row = UnifiedTaskManager._build_row(
            user_id="u1", task_type="parse", title="Hello"
        )
        assert row == {
            "user_id": "u1",
            "task_type": "parse",
            "title": "Hello",
            "status": "pending",
            "phase": TaskPhase.QUEUED.value,
            "progress": 0,
        }

    def test_title_truncated_to_200(self) -> None:
        row = UnifiedTaskManager._build_row(
            user_id="u1", task_type="parse", title="x" * 500
        )
        assert len(row["title"]) == 200

    def test_empty_title_falls_back(self) -> None:
        row = UnifiedTaskManager._build_row(user_id="u1", task_type="parse", title="")
        assert row["title"] == "Untitled"

    def test_optional_fields_only_when_set(self) -> None:
        row = UnifiedTaskManager._build_row(
            user_id="u1",
            task_type="parse",
            title="t",
            dbos_workflow_id="wf-1",
            flow_id="flow-1",
            subtitle="Initializing...",
        )
        assert row["dbos_workflow_id"] == "wf-1"
        assert row["flow_id"] == "flow-1"
        assert row["subtitle"] == "Initializing..."
        # Unset optionals must be absent (not None).
        assert "resource_id" not in row
        assert "media_id" not in row


# ─── create_many (bulk insert) ─────────────────────────────────────


def _mock_client_with_insert(insert_mock: MagicMock) -> MagicMock:
    """Build a fake supabase client whose .table(..).insert(..).execute() is wired."""
    table = MagicMock()
    table.insert = insert_mock
    client = MagicMock()
    client.table = MagicMock(return_value=table)
    return client


class TestCreateMany:
    @pytest.mark.asyncio
    async def test_empty_specs_returns_empty(self) -> None:
        mgr = UnifiedTaskManager()
        mgr._get_client = AsyncMock()  # must not even be called
        assert await mgr.create_many([]) == []
        mgr._get_client.assert_not_called()

    @pytest.mark.asyncio
    async def test_single_bulk_insert_for_small_batch(self) -> None:
        # 5 specs, chunk_size default 100 → ONE insert call (the whole point).
        specs = [
            {
                "user_id": "u",
                "task_type": "parse",
                "title": f"t{i}",
                "dbos_workflow_id": f"wf-{i}",
                "flow_id": "flow",
            }
            for i in range(5)
        ]
        execute = AsyncMock(
            return_value=SimpleNamespace(
                data=[{"dbos_workflow_id": f"wf-{i}"} for i in range(5)]
            )
        )
        insert = MagicMock(return_value=SimpleNamespace(execute=execute))
        client = _mock_client_with_insert(insert)
        mgr = UnifiedTaskManager()
        mgr._get_client = AsyncMock(return_value=client)

        ids = await mgr.create_many(specs)

        assert ids == [f"wf-{i}" for i in range(5)]
        assert insert.call_count == 1  # one bulk INSERT, not five
        # The argument was a LIST of rows (batch), not a single dict.
        (sent_rows,), _ = insert.call_args
        assert isinstance(sent_rows, list) and len(sent_rows) == 5

    @pytest.mark.asyncio
    async def test_chunks_large_batch(self) -> None:
        # 185 specs, chunk_size 100 → 2 bulk inserts (100 + 85).
        specs = [
            {
                "user_id": "u",
                "task_type": "parse",
                "title": f"t{i}",
                "dbos_workflow_id": f"wf-{i}",
            }
            for i in range(185)
        ]

        async def _execute_returns_sent_rows() -> SimpleNamespace:
            # echo back the rows that were inserted in this call
            (rows,), _ = insert.call_args
            return SimpleNamespace(data=list(rows))

        execute = MagicMock(side_effect=_execute_returns_sent_rows)
        insert = MagicMock(return_value=SimpleNamespace(execute=execute))
        client = _mock_client_with_insert(insert)
        mgr = UnifiedTaskManager()
        mgr._get_client = AsyncMock(return_value=client)

        ids = await mgr.create_many(specs, chunk_size=100)

        assert insert.call_count == 2
        assert len(ids) == 185

    @pytest.mark.asyncio
    async def test_bulk_failure_falls_back_to_per_row(self) -> None:
        # Bulk insert raises; per-row path recovers each row, and a duplicate
        # (23505) on one row is treated as idempotent success.
        specs = [
            {
                "user_id": "u",
                "task_type": "parse",
                "title": f"t{i}",
                "dbos_workflow_id": f"wf-{i}",
            }
            for i in range(3)
        ]

        def _insert(arg):  # type: ignore[no-untyped-def]
            if isinstance(arg, list):
                # the bulk call → fail
                return SimpleNamespace(
                    execute=AsyncMock(side_effect=Exception("bulk boom"))
                )
            # per-row calls
            wf = arg.get("dbos_workflow_id")
            if wf == "wf-1":
                return SimpleNamespace(
                    execute=AsyncMock(
                        side_effect=Exception("duplicate key value 23505")
                    )
                )
            return SimpleNamespace(
                execute=AsyncMock(
                    return_value=SimpleNamespace(data=[{"dbos_workflow_id": wf}])
                )
            )

        insert = MagicMock(side_effect=_insert)
        client = _mock_client_with_insert(insert)
        mgr = UnifiedTaskManager()
        mgr._get_client = AsyncMock(return_value=client)

        ids = await mgr.create_many(specs)

        # All three accounted for: wf-0 + wf-2 inserted, wf-1 idempotent dup.
        assert sorted(ids) == ["wf-0", "wf-1", "wf-2"]
