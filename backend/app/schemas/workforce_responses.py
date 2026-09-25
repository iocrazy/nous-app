"""Response models for ``/api/v1/workforce/*`` — the shapes the routes already emit.

Rows go through ``workforce_router._serialize_row`` before they reach these
models: timestamps are ISO strings, uuids are strings, BIGINT ``agent_runs.id``
stays a JSON **number** (no ``str()`` on it anywhere — the generated type says
``number`` because the wire does). ``cost_cents`` is a ``NUMERIC`` column and
is declared :data:`WorkforceWireNumeric` so it serializes exactly as
``jsonable_encoder`` did.

Nullable columns are ``| None`` and required (every row carries the key), so a
historical row with a NULL is accepted instead of turning the page into a 500.

Spec: docs/superpowers/specs/2026-09-24-openapi-typed-frontend-design.md
"""

from __future__ import annotations

from decimal import Decimal
from typing import Annotated, Any, Optional

from fastapi.encoders import decimal_encoder
from pydantic import BaseModel, PlainSerializer, WithJsonSchema

WorkforceWireNumeric = Annotated[
    Decimal,
    PlainSerializer(decimal_encoder, return_type=Any, when_used="json"),
    WithJsonSchema({"type": "number"}),
]
"""A ``NUMERIC`` value serialized exactly as ``jsonable_encoder`` does."""


# --------------------------------------------------------------------------- #
# GET /workforce/board
# --------------------------------------------------------------------------- #


class WorkforceWorkerRow(BaseModel):
    agent_id: str
    state: str
    current_task_id: Optional[str]
    state_changed_at: str
    heartbeat_at: str


class WorkforceQueueCounts(BaseModel):
    inbox_unread: int
    inbox_reading: int
    outbox_undelivered: int


class WorkforceBoardRun(BaseModel):
    """One of an agent's last five runs. ``id`` is a BIGINT Snowflake."""

    id: int
    agent_id: str
    status: str
    trigger: str
    started_at: str
    ended_at: Optional[str]
    cost_cents: Optional[WorkforceWireNumeric]
    prompt_tokens: int
    completion_tokens: int
    model: Optional[str]


class WorkforceBoardAgent(BaseModel):
    """``slug`` is a nullable column; ``name`` falls back to it."""

    id: str
    slug: Optional[str]
    name: Optional[str]
    icon: Optional[str]
    model: Optional[str]
    persistent: bool
    paused_reason: Optional[str]
    worker: Optional[WorkforceWorkerRow]
    queue: WorkforceQueueCounts
    recent_runs: list[WorkforceBoardRun]


class WorkforceStateHistoryRow(BaseModel):
    """``agent_slug`` is the agent's slug (nullable column)."""

    agent_slug: Optional[str]
    from_state: Optional[str]
    to_state: str
    trigger: str
    task_id: Optional[str]
    changed_at: str


class WorkforceBoard(BaseModel):
    agents: list[WorkforceBoardAgent]
    recent_state_history: list[WorkforceStateHistoryRow]


# --------------------------------------------------------------------------- #
# GET /workforce/agents/{slug}/detail (platform admin)
# --------------------------------------------------------------------------- #


class WorkforceAgentSummary(BaseModel):
    id: str
    slug: str
    name: Optional[str]
    icon: Optional[str]
    model: Optional[str]
    persistent: bool
    paused_reason: Optional[str]


class WorkforceInboxRow(BaseModel):
    id: str
    sender_kind: str
    sender_user_id: Optional[str]
    sender_agent_id: Optional[str]
    message_type: str
    payload: dict[str, Any]
    status: str
    priority: int
    created_at: str
    processed_at: Optional[str]
    reply_to_message_id: Optional[str]
    dedup_key: Optional[str]


class WorkforceOutboxRow(BaseModel):
    id: str
    recipient_kind: str
    recipient_user_id: Optional[str]
    recipient_agent_id: Optional[str]
    message_type: str
    payload: dict[str, Any]
    task_id: Optional[str]
    delivered: bool
    delivered_at: Optional[str]
    created_at: str


class WorkforceDetailRun(BaseModel):
    """``id`` is a BIGINT Snowflake, sent as a JSON number."""

    id: int
    status: str
    trigger: str
    model: Optional[str]
    provider: Optional[str]
    started_at: str
    ended_at: Optional[str]
    prompt_tokens: int
    completion_tokens: int
    cost_cents: Optional[WorkforceWireNumeric]
    input_summary: Optional[str]
    output_summary: Optional[str]
    error_code: Optional[str]
    error_message: Optional[str]


class WorkforceAgentDetail(BaseModel):
    agent: WorkforceAgentSummary
    inbox: list[WorkforceInboxRow]
    outbox: list[WorkforceOutboxRow]
    runs: list[WorkforceDetailRun]


# --------------------------------------------------------------------------- #
# Admin actions
# --------------------------------------------------------------------------- #


class WorkforceAgentPauseResult(BaseModel):
    """``POST .../pause`` → ``status='paused'``; ``.../resume`` → ``'resumed'``
    with ``paused_reason`` null."""

    slug: str
    paused_reason: Optional[str]
    status: str


class WorkforceInboxClearResult(BaseModel):
    slug: str
    cleared: int


class WorkforceTaskCancelResult(BaseModel):
    """``note`` is present when nothing was cancelled outright: the task was
    already terminal, or its run was asked to stop. Absent (not null) on a
    queued task cancelled on the spot — the route is ``exclude_unset``."""

    task_id: str
    lifecycle_status: Optional[str]
    note: Optional[str] = None


# --------------------------------------------------------------------------- #
# GET /workforce/tasks/by-inbox/{inbox_message_id}
# --------------------------------------------------------------------------- #


class WorkforceDelegateTask(BaseModel):
    """``tt_row_to_task_shape`` over the columns the route selects. Keys the
    route does not select (``user_id``, ``title``, ``parent_task_id``,
    ``root_task_id``, ``updated_at``) are always present and null.
    ``payload`` / ``current_run_id`` / ``result`` / ``assigned_at`` /
    ``workforce_workflow_id`` come out of the row's ``metadata`` jsonb."""

    id: str
    agent_id: Optional[str]
    user_id: Optional[str]
    title: Optional[str]
    payload: dict[str, Any]
    lifecycle_status: str
    current_run_id: Optional[str | int]
    result: Any
    error_code: Optional[str]
    error_message: Optional[str]
    parent_task_id: Optional[str]
    root_task_id: Optional[str]
    inbox_message_id: Optional[str]
    created_at: Optional[str]
    assigned_at: Optional[str]
    dispatch_attempt: int
    workforce_workflow_id: Optional[str]
    started_at: Optional[str]
    ended_at: Optional[str]
    updated_at: Optional[str]


class WorkforceDelegateOutboxResponse(BaseModel):
    id: str
    sender_agent_id: str
    message_type: str
    payload: dict[str, Any]
    created_at: str
    delivered: bool
    delivered_at: Optional[str]


class WorkforceDelegateTaskLookup(BaseModel):
    inbox_message_id: str
    task: Optional[WorkforceDelegateTask]
    outbox_response: Optional[WorkforceDelegateOutboxResponse]


# --------------------------------------------------------------------------- #
# GET /workforce/healthz (anonymous probe)
# --------------------------------------------------------------------------- #


class WorkforceHealthDispatcher(BaseModel):
    engine: str
    launched: bool
    inflight_agent_tasks: Optional[int]
    oldest_undispatched_age_s: Optional[float]


class WorkforceHealthDatabase(BaseModel):
    """Reachable: the three counts. Unreachable: ``error`` (an exception class
    name). The route is ``exclude_unset``, so only the keys of the branch taken
    are sent."""

    reachable: bool
    persistent_agents: Optional[int] = None
    recent_processed_5m: Optional[int] = None
    pending_depth: Optional[int] = None
    error: Optional[str] = None


class WorkforceHealth(BaseModel):
    status: str
    issues: list[str]
    dispatcher: WorkforceHealthDispatcher
    supabase: WorkforceHealthDatabase
