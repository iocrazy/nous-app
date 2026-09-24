"""Response models for the ``/ai-library`` routes that used to return bare dicts.

Every model here declares what the handler ALREADY sends (OpenAPI P4). Each
route has a wire-parity test in ``tests/api/test_ai_library_*_wire.py`` that
compares the HTTP body with ``jsonable_encoder`` of the dict the handler
builds, so a key dropped or a value re-serialized shows up as a diff.

Conventions on this surface (照实, not idealised):

- Timestamps the router already turned into ISO strings (``_serialize_row``,
  explicit ``.isoformat()``) are ``str``; native datetimes are
  :data:`WireDatetime` so they keep the ``+00:00`` form.
- Snowflake ids are ``str`` where the router ``str()``s them, ``int`` where it
  does not (commitment ids, the fork result's ``issue_id``).
- ``Numeric`` money columns reach the router as ``Decimal``; ``float`` gives
  the same JSON number ``jsonable_encoder`` did.

Names carry an ``AiLibrary`` / domain prefix: ``UsageDailyRow``,
``UsageSummary`` and friends already exist for ``/api/v1/usage/*`` with a
different meaning.
"""

from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel

from app.schemas.wire import WireDatetime

# --------------------------------------------------------------------------- #
# Agents: status / dashboard / usage
# --------------------------------------------------------------------------- #


class AgentStatusOut(BaseModel):
    """``GET /agents/{slug}/status`` — the header chip."""

    status: Literal["idle", "running", "paused"]
    paused_reason: Optional[str]
    running_count: int


class AgentDashboardAgent(BaseModel):
    id: str
    slug: Optional[str]
    name: Optional[str]
    icon: Optional[str]
    model: Optional[str]
    persistent: bool
    paused_reason: Optional[str]


class AgentDashboardLatestRun(BaseModel):
    """Most recent run (ISO timestamps, id as a numeric string)."""

    id: str
    status: str
    trigger: str
    model: Optional[str]
    started_at: str
    ended_at: Optional[str]
    prompt_tokens: int
    completion_tokens: int
    cost_cents: Optional[float]
    input_summary: Optional[str]
    output_summary: Optional[str]
    error_code: Optional[str]
    error_message: Optional[str]


class AgentDashboardRecentRun(BaseModel):
    id: str
    status: str
    trigger: str
    model: Optional[str]
    started_at: str
    ended_at: Optional[str]
    prompt_tokens: int
    completion_tokens: int
    cost_cents: Optional[float]


class AgentDashboardDailyCount(BaseModel):
    date: str
    count: int


class AgentDashboardDailySuccess(BaseModel):
    date: str
    success: int
    total: int


class AgentDashboardCosts(BaseModel):
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    total_cost_cents: float
    run_count: int


class AgentTaskShape(BaseModel):
    """A ``task_tracking`` row in agent_tasks shape (``tt_row_to_task_shape``).

    The dashboard SELECTs only some columns, so several keys are always null
    there; they are still sent, because the mapper always writes them.
    """

    id: Optional[str]
    agent_id: Optional[str]
    user_id: Optional[str]
    title: Optional[str]
    payload: dict[str, Any]
    lifecycle_status: str
    current_run_id: Optional[str]
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


class AgentDashboard(BaseModel):
    """``GET /agents/{slug}/dashboard`` — 14-day, caller-scoped."""

    agent: AgentDashboardAgent
    latest_run: Optional[AgentDashboardLatestRun]
    run_activity_14d: list[AgentDashboardDailyCount]
    tasks_by_status_14d: dict[str, int]
    success_rate_14d: list[AgentDashboardDailySuccess]
    costs_14d: AgentDashboardCosts
    recent_tasks: list[AgentTaskShape]
    recent_runs: list[AgentDashboardRecentRun]


class AgentUsageModule(BaseModel):
    module_key: str
    feature_key: str


class AgentUsageTrigger(BaseModel):
    trigger: str
    feature_key: str
    count: int


class AgentUsageOut(BaseModel):
    """``GET /agents/{slug}/usage`` — the "Used by" card."""

    modules: list[AgentUsageModule]
    trigger_counts: list[AgentUsageTrigger]
    conversation_count: int
    routine_count: int
    window_days: int


# --------------------------------------------------------------------------- #
# Version history (agents / skills / skill files)
# --------------------------------------------------------------------------- #


class _VersionListItemBase(BaseModel):
    id: str
    version_number: int
    notes: Optional[str]
    created_by: Optional[str]
    created_at: WireDatetime


class AgentVersionListItem(_VersionListItemBase):
    model: Optional[str]
    temperature: Optional[float]
    max_tokens: Optional[int]


class SkillVersionListItem(_VersionListItemBase):
    pass


class SkillFileVersionListItem(_VersionListItemBase):
    path: str
    file_type: Optional[str]


class AgentVersionList(BaseModel):
    items: list[AgentVersionListItem]
    current_version: Optional[int]


class SkillVersionList(BaseModel):
    items: list[SkillVersionListItem]
    current_version: Optional[int]


class SkillFileVersionList(BaseModel):
    items: list[SkillFileVersionListItem]
    current_version: Optional[int]


class AgentVersionDetail(BaseModel):
    """One ``ai_agent_versions`` row, every column (ISO timestamps, str uuids)."""

    id: str
    agent_id: str
    version_number: int
    created_at: str
    identity_md: Optional[str]
    soul_md: Optional[str]
    agent_md: Optional[str]
    model: Optional[str]
    temperature: Optional[float]
    max_tokens: Optional[int]
    notes: Optional[str]
    created_by: Optional[str]


class SkillVersionDetail(BaseModel):
    """One ``skill_versions`` row, every column (``skill_id`` is a number)."""

    id: str
    skill_id: int
    version_number: int
    created_at: str
    body_md: Optional[str]
    frontmatter_json: Optional[dict[str, Any]]
    notes: Optional[str]
    created_by: Optional[str]


class VersionRollbackResult(BaseModel):
    """Rollback writes a NEW version holding the old content."""

    rolled_back_to: int
    new_version: Optional[int]
    notes: str


# --------------------------------------------------------------------------- #
# MCP servers / approval requests / commitments
# --------------------------------------------------------------------------- #


class McpServerOut(BaseModel):
    """The bearer token never leaves the server; only whether one is set."""

    id: str
    name: str
    url: str
    description: Optional[str]
    enabled: bool
    has_bearer_token: bool


class McpServerList(BaseModel):
    items: list[McpServerOut]
    count: int


class ApprovalRequestItem(BaseModel):
    id: str
    agent_id: str
    session_id: Optional[str]
    run_id: Optional[str]
    hook_name: str
    reason: str
    payload: dict[str, Any]
    created_at: Optional[str]
    expires_at: Optional[str]


class ApprovalRequestList(BaseModel):
    items: list[ApprovalRequestItem]
    count: int


class ApprovalDecisionResult(BaseModel):
    id: str
    status: Literal["approved", "rejected"]


CommitmentStatusValue = Literal[
    "pending", "fulfilled", "cancelled", "expired", "failed"
]


class CommitmentItem(BaseModel):
    """``agent_commitments`` row; ``id`` is a BIGINT sent as a JSON number."""

    id: int
    agent_id: str
    session_id: Optional[str]
    description: str
    trigger_type: Optional[Literal["time", "event", "next_session"]]
    trigger_at: Optional[str]
    trigger_event: Optional[str]
    status: CommitmentStatusValue
    created_at: Optional[str]
    fulfilled_at: Optional[str]
    expires_at: Optional[str]


class CommitmentList(BaseModel):
    items: list[CommitmentItem]
    count: int


class CommitmentStatusResult(BaseModel):
    id: int
    status: CommitmentStatusValue


# --------------------------------------------------------------------------- #
# Runs: live strip / transcript / replay / fork / cancel
# --------------------------------------------------------------------------- #


class LiveRunItem(BaseModel):
    id: str
    agent_id: str
    status: str
    trigger: str
    model: Optional[str]
    started_at: str
    prompt_tokens: int
    completion_tokens: int
    cost_cents: Optional[float]
    input_summary: Optional[str]
    task_id: Optional[str]
    agent_slug: Optional[str]
    agent_name: Optional[str]
    agent_icon: Optional[str]


class LiveRunList(BaseModel):
    items: list[LiveRunItem]
    count: int


class RunTranscriptEvent(BaseModel):
    seq: int
    event_type: str
    payload: dict[str, Any]
    created_at: str
    turn: Optional[int]
    step: Optional[int]


class RunTranscriptPage(BaseModel):
    items: list[RunTranscriptEvent]
    count: int
    has_more: bool


class RunViewAt(BaseModel):
    """``view`` / ``cost`` are the fold registry's open projections
    (``run_projection.empty_views``); their keys grow with each fold."""

    seq: int
    view: dict[str, Any]
    cost: dict[str, Any]


class RunForkOrigin(BaseModel):
    run_id: int
    at_seq: int


class RunForkResult(BaseModel):
    """``run_id`` is always null: the workflow opens the run row later."""

    run_id: None
    session_id: str
    workflow_id: str
    issue_id: int
    forked_from: RunForkOrigin


class RunForkItem(BaseModel):
    run_id: str
    at_seq: Optional[int]
    created_at: WireDatetime
    status: str


class RunForkList(BaseModel):
    items: list[RunForkItem]


class RunCancelResult(BaseModel):
    status: Literal["cancel_requested"]
    run_id: str


# --------------------------------------------------------------------------- #
# Usage (caller-scoped) and chat attachments
# --------------------------------------------------------------------------- #


class AiLibraryUsageRunItem(BaseModel):
    """``agent_slug`` / ``agent_name`` are ABSENT when the agent lookup found
    nothing (the route declares ``response_model_exclude_unset``)."""

    id: str
    agent_id: Optional[str]
    agent_slug: Optional[str] = None
    agent_name: Optional[str] = None
    model: Optional[str]
    provider: Optional[str]
    status: str
    trigger: Optional[str]
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    cost_cents: float
    duration_ms: Optional[int]
    started_at: Optional[str]
    error_code: Optional[str]


class AiLibraryUsageRunsPage(BaseModel):
    items: list[AiLibraryUsageRunItem]
    total: int
    page: int
    page_size: int


class AiLibraryUsageDailyRow(BaseModel):
    """One (day, model|agent) bucket of ``/ai-library/usage/daily``.

    Not ``UsageDailyRow``: that is the team ``/usage/summary`` row."""

    date: str
    key: Optional[str]
    label: Optional[str]
    requests: int
    failed_requests: int
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    cost_cents: float


class AiLibraryUsageDaily(BaseModel):
    days: int
    month: Optional[str]
    group_by: Literal["model", "agent"]
    total_requests: int
    total_failed: int
    total_tokens: int
    total_cost_cents: float
    daily: list[AiLibraryUsageDailyRow]


class AiLibraryUsageOverall(BaseModel):
    total_tokens: int
    cost_points: float
    run_count: int


class AiLibraryUsageByModel(BaseModel):
    model: str
    total_tokens: int
    cost_points: float
    run_count: int


class AiLibraryUsageByDay(BaseModel):
    date: str
    total_tokens: int
    cost_points: float
    run_count: int


class AiLibraryUsageSummary(BaseModel):
    """``/ai-library/usage/summary`` — points rollup from ``ai_usage_logs``."""

    window_start: str
    window_end: str
    overall: AiLibraryUsageOverall
    by_model: list[AiLibraryUsageByModel]
    by_day: list[AiLibraryUsageByDay]


class ChatAttachmentUpload(BaseModel):
    kind: Literal["image", "video", "pdf"]
    resource_id: str
    file_path: str
    url: str
    size_bytes: int
    mime: Optional[str]
    filename: str
