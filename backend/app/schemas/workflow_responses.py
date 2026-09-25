"""Response shapes of ``/api/v1/workflows`` (two routers share the prefix).

These declare what the routers already sent; nothing here changes the wire.

**Workflow templates** (``workflow_templates_router``): ids are Snowflake
BIGINTs that the repository renders as strings, timestamps are already
``isoformat()`` strings (``+00:00``), so both are declared ``str``. The JSONB
columns ``events`` / ``form_schema`` go out as stored: declaring them as
``WorkflowNodeEvents`` / ``FormFieldDef`` would drop unknown keys and fill
defaults on legacy rows, so they stay loose dicts. ``completion_policy`` and
``surface`` carry DB CHECK constraints (mig 386 / 402), so their literals are
safe on output.

**DBOS runs** (``workflows_router``): a projection of DBOS'
``WorkflowStatus`` / ``StepInfo``. Timestamps are Unix epoch **milliseconds**
(ints), not ISO strings. ``status`` stays ``str``: DBOS adds states between
releases, and a ``Literal`` that lags it would turn a status read into a 500.
``input`` / ``output`` are whatever the workflow was called with / returned
(already passed through ``jsonable_encoder`` by the route), so ``Any``.

The SSE stream ``GET /workflows/{id}/events`` pushes the same
``DbosWorkflowSnapshot`` per ``event: status`` (plus ``steps`` when
``include_steps=true``); SSE bodies are not in OpenAPI, so the frontend types
that payload from these two models.

Spec: docs/superpowers/specs/2026-09-24-openapi-typed-frontend-design.md
"""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field

# ── workflow templates ───────────────────────────────────────────────────────


class WorkflowTemplateSummary(BaseModel):
    """A ``workflow_templates`` row with its node count (list / create)."""

    id: str
    team_id: str
    name: str
    is_default: bool
    created_by: Optional[str]
    created_at: str
    updated_at: str
    node_count: int


class WorkflowTemplateNodeMember(BaseModel):
    """A default member of a template node: exactly one of user / agent."""

    id: str
    node_id: str
    user_id: Optional[str]
    agent_id: Optional[str]


class WorkflowTemplateNodeRow(BaseModel):
    """One ``workflow_template_nodes`` row with its members and dependencies."""

    id: str
    template_id: str
    name: str
    sort_order: int
    parallel_group: Optional[int]
    default_owner_user_id: Optional[str]
    default_owner_agent_id: Optional[str]
    skip_default: bool
    review_required: bool
    deliverable_required: bool
    deliverable_label: Optional[str]
    source_stage_id: Optional[str]
    duration_days: Optional[int]
    completion_policy: Literal["owner", "any_editor"]
    events: Dict[str, Any] = Field(
        ..., description="Stored JSONB as written (see WorkflowNodeEvents)."
    )
    form_schema: List[Dict[str, Any]] = Field(
        ..., description="Stored JSONB as written (see FormFieldDef)."
    )
    surface: Optional[Literal["script", "storyboard", "renders"]]
    members: List[WorkflowTemplateNodeMember]
    depends_on: List[str] = Field(
        ..., description="Real ids of the nodes this one depends on."
    )


class WorkflowTemplateDetail(WorkflowTemplateSummary):
    """``GET`` / ``PATCH /workflows/{id}``: the template with ordered nodes."""

    nodes: List[WorkflowTemplateNodeRow]


class WorkflowTemplateDeleted(BaseModel):
    deleted: Literal[True]


class WorkflowStageLibraryEntry(BaseModel):
    """One node-bank row (``project_stages`` with ``phase IS NOT NULL``)."""

    id: str
    slug: str
    name: str
    sort_order: int
    phase: str
    default_role_label: Optional[str]
    deliverable_label: Optional[str]
    review_required: bool


# ── DBOS runs ────────────────────────────────────────────────────────────────


class DbosWorkflowSnapshot(BaseModel):
    """``GET /workflows/{id}/status``, and each SSE ``event: status`` push."""

    workflow_id: Optional[str]
    status: Optional[str] = Field(
        ..., description="DBOS status string: PENDING, ENQUEUED, SUCCESS, ERROR …"
    )
    name: Optional[str]
    queue_name: Optional[str]
    created_at: Optional[int] = Field(..., description="Unix epoch ms.")
    updated_at: Optional[int] = Field(..., description="Unix epoch ms.")
    error: Optional[str]
    executor_id: Optional[str]
    app_version: Optional[str]
    authenticated_user: Optional[str]
    input: Any = Field(..., description="The workflow's arguments ({args, kwargs}).")
    output: Any


class DbosWorkflowStep(BaseModel):
    function_id: Optional[int]
    function_name: Optional[str]
    output: Any
    error: Optional[str]
    child_workflow_id: Optional[str]
    started_at_epoch_ms: Optional[int]
    completed_at_epoch_ms: Optional[int]


class DbosWorkflowSteps(BaseModel):
    """``GET /workflows/{id}/steps``. Empty when DBOS cannot list them."""

    workflow_id: str
    steps: List[DbosWorkflowStep]


class DbosWorkflowCancelResult(BaseModel):
    status: Literal["cancel_requested"]
    workflow_id: str


class DbosWorkflowResumeResult(BaseModel):
    status: Literal["resumed"]
    workflow_id: str


class DbosWorkflowRestartResult(BaseModel):
    """The fork runs under a NEW id; the original stays as it was."""

    status: Literal["restarted"]
    original_workflow_id: str
    new_workflow_id: str
