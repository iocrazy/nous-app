"""Response shapes of ``/api/v1/task-manager`` (``task_manager_router``).

These declare what the router already sent; nothing here changes the wire.

Rows are ``task_tracking`` rows keyed by DB column name (so ``metadata``, not
the ORM attribute ``metadata_``). The key is ``dbos_workflow_id`` (TEXT; the
old ``id`` column was dropped in migration 180). ``UnifiedTaskManager`` and the
health-override route run every row through ``_serialize_task_row``
(``isoformat()``), so timestamps are already ``str`` with ``+00:00`` and are
declared ``str``. UUID columns go out as strings; ``issue_id`` (BIGINT) is a
JSON number.

``status`` / ``phase`` / ``task_type`` stay ``str``: two writers with two
vocabularies fill them (the DBOS mirror trigger and the task manager, see
``unified_task_manager.KNOWN_PHASES``), and a ``Literal`` that lags either one
would turn a list request into a 500.

This is the REST shape only. The frontend also receives ``task_tracking`` rows
from Supabase Realtime (PostgREST encoding) — that boundary is not typed here.

Spec: docs/superpowers/specs/2026-09-24-openapi-typed-frontend-design.md
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Union
from uuid import UUID

from pydantic import BaseModel, Field

from app.schemas.envelope import Envelope


class TaskTrackingRow(BaseModel):
    """Every ``task_tracking`` column, as the task manager serializes it.

    ``tests/api/test_task_manager_wire.py`` pins the field set to the ORM
    columns.
    """

    dbos_workflow_id: str = Field(
        ..., description="Primary key; the UI task id (== DBOS workflow uuid)."
    )
    user_id: UUID
    task_type: str
    task_kind: str
    status: str
    phase: Optional[str]
    title: str
    subtitle: Optional[str]
    progress: Optional[int]
    speed: Optional[int]
    total_bytes: Optional[int]
    error_msg: Optional[str]
    error_code: Optional[str]
    resource_id: Optional[str]
    media_id: Optional[str]
    metadata: Optional[Dict[str, Any]]
    dedup_key: Optional[str]
    subscribers: Optional[List[Dict[str, Any]]]
    cost_cents: int
    group_id: Optional[UUID]
    flow_id: Optional[UUID]
    issue_id: Optional[int]
    agent_id: Optional[UUID]
    parent_task_id: Optional[str]
    root_task_id: Optional[str]
    inbox_message_id: Optional[UUID]
    heartbeat_at: Optional[str]
    health_status: Optional[str]
    health_notified_at: Optional[str]
    max_duration_minutes: Optional[int]
    expected_duration_minutes: Optional[int]
    do_not_auto_cancel: bool
    created_at: Optional[str]
    started_at: Optional[str]
    completed_at: Optional[str]
    updated_at: Optional[str]


class TaskListPage(Envelope[List[TaskTrackingRow]]):
    """``GET /tasks``: one page plus page-number pagination siblings."""

    total: int
    page: int
    page_size: int


class TaskActiveCounts(BaseModel):
    """Active (pending/processing) task counts by ``task_type``."""

    total: int
    by_type: Dict[str, int]


class TaskStatsByType(BaseModel):
    """Fixed buckets; a row of any other type is not counted."""

    parse: int
    download: int
    upload: int
    transcode: int
    ai_pipeline: int
    ai_extract: int
    ai_transcription: int
    ai_summary: int


class TaskStatsByStatus(BaseModel):
    """Fixed buckets; ``lost`` and other statuses are not counted."""

    pending: int
    processing: int
    completed: int
    failed: int
    cancelled: int


class TaskStats(BaseModel):
    by_type: TaskStatsByType
    by_status: TaskStatsByStatus
    active_total: int


class TaskIdList(BaseModel):
    """``GET /tasks/ids``: terminal task ids for cross-page select-all."""

    success: bool = True
    ids: List[str]
    total: int
    capped: bool = Field(
        ..., description="True when more rows matched than the server cap."
    )


class TaskAck(BaseModel):
    """Cancel / delete answer ``{"success": true}`` and nothing else."""

    success: bool = True


class TaskClearCompletedResult(BaseModel):
    success: bool = True
    cleared: int


class TaskHealthOverrideResult(BaseModel):
    """``PATCH /tasks/{id}/health-override``: the patched row."""

    success: bool = True
    task: TaskTrackingRow


class TaskExtendResult(BaseModel):
    success: bool = True
    max_duration_minutes: int


class TaskProgress(BaseModel):
    """``GET /tasks/{id}/progress``.

    Two sources: the Redis ``download_progress:<id>`` key while a download is
    live, else the ``task_tracking`` row. ``downloaded`` exists only on the
    Redis branch (absent, not null, otherwise). ``speed`` is a human string
    (``"2.5 MB/s"``) from Redis but the raw BIGINT bytes/s column from the DB.
    """

    task_id: str
    dbos_workflow_id: Optional[str]
    status: str
    percent: Optional[Union[int, float]]
    downloaded: Optional[int] = Field(
        None, description="Redis branch only; absent on the DB fallback."
    )
    total: Optional[int]
    speed: Optional[Union[str, int]]
    error: Optional[str]
    subtitle: Optional[str]
