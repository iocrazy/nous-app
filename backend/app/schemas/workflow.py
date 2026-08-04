"""Workflow template API schemas (M1 PR-A).

Mirrors the mig 380 tables. Owner is single (user XOR agent); members are a
list of user-or-agent refs. Guardrails: a template may hold at most
``MAX_NODES_PER_TEMPLATE`` nodes (enforced here → 422); a team at most
``MAX_TEMPLATES_PER_TEAM`` templates (enforced in the router → 422).
"""

from __future__ import annotations

from datetime import date
from typing import Any, Dict, List, Literal, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# Soft guardrails (spec §3): keep a template from ballooning and a team from
# hoarding templates. Both surface as 422.
MAX_NODES_PER_TEMPLATE = 30
MAX_TEMPLATES_PER_TEAM = 20

# Form-based deliverables (mig 390, M3 PR-I §2). Six-type whitelist; a node's
# form may hold at most MAX_FORM_FIELDS fields (same "soft guardrail → 422"
# idiom as the two constants above).
FormFieldType = Literal["text", "textarea", "number", "select", "checkbox", "date"]
FORM_FIELD_TYPES = ("text", "textarea", "number", "select", "checkbox", "date")
MAX_FORM_FIELDS = 20


class FormFieldDef(BaseModel):
    """One field definition in a node's deliverable form (mig 390, M3 PR-I).

    ``key`` rides in empty at the API boundary — the repo write path (I2)
    generates it from ``label`` (slugify, deduped with a ``-2`` suffix within
    the node) since only the server can guarantee uniqueness across a node's
    field list. ``options`` only makes sense for ``type == "select"`` and, when
    present there, must be a non-empty list — an empty options list would
    render a picker with nothing to pick.
    """

    model_config = ConfigDict(extra="ignore")

    key: str = ""
    label: str
    type: FormFieldType
    required: bool = False
    options: Optional[List[str]] = None

    @field_validator("label")
    @classmethod
    def _label_stripped_nonempty(cls, v: str) -> str:
        stripped = v.strip()
        if not stripped:
            raise ValueError("label must not be blank")
        return stripped

    @model_validator(mode="after")
    def _options_only_for_select(self) -> "FormFieldDef":
        if self.type == "select":
            if not self.options:
                raise ValueError("a 'select' field requires a non-empty options list")
        elif self.options is not None:
            raise ValueError("options is only allowed when type is 'select'")
        return self


class TemplateNodeMemberIn(BaseModel):
    """A default member of a template node — exactly one of user/agent."""

    # UUID (not str) so empty/malformed values fail at parse, before the DB.
    user_id: Optional[UUID] = None
    agent_id: Optional[UUID] = None

    @model_validator(mode="after")
    def _member_xor(self) -> "TemplateNodeMemberIn":
        if (self.user_id is None) == (self.agent_id is None):
            raise ValueError("member must set exactly one of user_id / agent_id")
        return self


class WorkflowNodeEvents(BaseModel):
    """The three configurable event toggles on a node (mig 386). The two
    built-in events (arrival → derived issue, completion → close issue) stay
    hardcoded (spec §4, M3 territory); this only gates their notifications.
    Unknown keys are dropped rather than rejected — forward-compatible with
    a future toggle added here before the frontend picks it up."""

    model_config = ConfigDict(extra="ignore")

    notify_on_arrival: bool = True
    notify_on_complete: bool = False
    suggest_agent_run: bool = False
    # mig 389 (M3 PR-H1): arrival hook that pre-fills an agent run without
    # auto-launching it (H2/H3 territory — this schema only carries the flag).
    prepare_agent_run: bool = False
    # mig 389 (M3 PR-H1): reserved for a future "chain into another workflow on
    # completion" hook. Not implemented yet -- any non-None value is rejected
    # rather than silently accepted and later ignored.
    on_complete_workflow: Optional[str] = None
    # mig 395 (M4 Autopilot §1): auto-open + auto-dispatch this node the moment
    # its dependencies are satisfied (the "B档" behavior — see design doc). This
    # is CONFIG (template-layer, copied verbatim at instantiation, same as
    # completion_policy/the other events keys), never runtime data — it is
    # never accepted on an instance PATCH (NodePatch has no ``events`` field at
    # all, so this is enforced by omission, not a validator). Default false:
    # merging this PR changes zero behavior until a template/node opts in.
    auto_start: bool = False

    @field_validator("on_complete_workflow")
    @classmethod
    def _on_complete_workflow_not_implemented(cls, v: Optional[str]) -> Optional[str]:
        if v is not None:
            raise ValueError("on_complete_workflow is not implemented in M3")
        return v


class TemplateNodeIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    sort_order: int
    parallel_group: Optional[int] = None
    default_owner_user_id: Optional[UUID] = None
    default_owner_agent_id: Optional[UUID] = None
    skip_default: bool = False
    review_required: bool = False
    deliverable_required: bool = False
    deliverable_label: Optional[str] = None
    # Snowflake ids ride as strings at the API boundary (bigIntSafeFetch).
    source_stage_id: Optional[str] = None
    duration_days: Optional[int] = None
    # Flow Rules / Events (mig 386) — template-layer only; instances copy these
    # at instantiation and do not open them for in-place tweaks (spec §5).
    completion_policy: Literal["owner", "any_editor"] = "owner"
    events: WorkflowNodeEvents = Field(default_factory=WorkflowNodeEvents)
    members: List[TemplateNodeMemberIn] = Field(default_factory=list)
    # Form-based deliverables (mig 390, M3 PR-I) — template-layer only, same
    # instantiate-then-freeze idiom as completion_policy/events above. ``key``
    # dedup/slugify happens in the repo write path (I2), not here.
    form_schema: List[FormFieldDef] = Field(default_factory=list)
    # Dependency edges (mig 391, M3 PR-J). ⚠️ PAYLOAD-INDEX CONTRACT, not node
    # ids: ``TemplateUpdate.nodes`` is a FULL replacement on every save
    # (workflow_templates_repository.update_template deletes every existing
    # node and reinserts the whole list — see that method), so a node's real
    # DB id is never stable across two saves and TemplateNodeIn carries no
    # ``id`` field for the caller to reference in the first place. Each string
    # here MUST be the stringified 0-based INDEX of another node within THIS
    # SAME ``nodes`` list (e.g. "0", "1", ...) — never a previously-returned
    # node id. The repo resolves indices to freshly-created ids after insert
    # (see ``_validate_deps_backward`` / the id_by_index map in
    # ``update_template``). A caller loading an existing template for editing
    # must convert the ids it got back from GET into positions within the
    # array it is about to submit before populating this field.
    depends_on: List[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _owner_xor(self) -> "TemplateNodeIn":
        if (
            self.default_owner_user_id is not None
            and self.default_owner_agent_id is not None
        ):
            raise ValueError(
                "default_owner_user_id and default_owner_agent_id are "
                "mutually exclusive"
            )
        return self

    @model_validator(mode="after")
    def _max_form_fields(self) -> "TemplateNodeIn":
        if len(self.form_schema) > MAX_FORM_FIELDS:
            raise ValueError(f"a node's form may hold at most {MAX_FORM_FIELDS} fields")
        return self


class TemplateCreate(BaseModel):
    """POST /workflows body. team_id is a query param; created_by is set from
    auth context in the router."""

    name: str = Field(min_length=1, max_length=200)


class AttachWorkflowRequest(BaseModel):
    """POST /projects/{id}/workflow body (M1.x opt-in migration path — attach
    a workflow to an EXISTING project that has none yet, the non-lossy
    alternative to a bulk legacy-SOP-to-template migration script)."""

    template_id: str
    method: Optional[Literal["live", "ai", "hybrid"]] = None


class TemplateUpdate(BaseModel):
    """PATCH /workflows/{id}. Every field optional. When ``nodes`` is present it
    is a FULL replacement of the template's node list (delete + insert)."""

    name: Optional[str] = Field(default=None, min_length=1, max_length=200)
    is_default: Optional[bool] = None
    nodes: Optional[List[TemplateNodeIn]] = None

    @model_validator(mode="after")
    def _max_nodes(self) -> "TemplateUpdate":
        if self.nodes is not None and len(self.nodes) > MAX_NODES_PER_TEMPLATE:
            raise ValueError(
                f"a template may hold at most {MAX_NODES_PER_TEMPLATE} nodes"
            )
        return self


# ── project workflow instance (PR-B) ────────────────────────────────────────


class NodeMemberOut(BaseModel):
    """A member ref on a live node — exactly one of user/agent is set."""

    user_id: Optional[str] = None
    agent_id: Optional[str] = None


class NodeOut(BaseModel):
    """One live ``project_stage_nodes`` row (ids as strings)."""

    id: str
    project_id: str
    source_template_node_id: Optional[str] = None
    legacy_stage_id: Optional[str] = None
    name: str
    sort_order: int
    parallel_group: Optional[int] = None
    status: str
    owner_user_id: Optional[str] = None
    owner_agent_id: Optional[str] = None
    planned_start: Optional[str] = None
    planned_due: Optional[str] = None
    review_required: bool
    deliverable_required: bool
    deliverable_label: Optional[str] = None
    skipped: bool
    # Deliverable folder link + filed-file count (M2-W1). ``folder_id`` is the
    # node's stage folder (mig 383); ``deliverable_file_count`` is the number of
    # non-trashed files filed into it — the CurrentNodeCard's "N files filed".
    folder_id: Optional[str] = None
    deliverable_file_count: int = 0
    # Flow Rules / Events (mig 386, M2 PR-D) — copied verbatim from the
    # template at instantiation (see project_stage_nodes_repository); a later
    # task (E3 suggest-agent-run chip) reads node.events.suggest_agent_run
    # straight off this endpoint's payload, so both must actually reach the
    # response JSON rather than being silently dropped by the response model.
    completion_policy: Literal["owner", "any_editor"] = "owner"
    events: WorkflowNodeEvents = Field(default_factory=WorkflowNodeEvents)
    members: List[NodeMemberOut] = Field(default_factory=list)
    # mig 389 (M3 PR-H1/H3): node-level metadata JSONB — today the only key
    # written is ``run_prepared_at`` (ISO timestamp, set by the
    # stage_hook_dispatch workflow when events.prepare_agent_run fires on
    # arrival). ProjectStageNodesRepository._node_row already returns this key
    # (obj.metadata_), but without a declared field here pydantic silently
    # drops it converting the row dict → NodeOut — the exact same shape of bug
    # completion_policy/events hit before (see the regression test above this
    # one in tests/test_workflow_flow_rules.py). Default empty dict so a
    # pre-mig-389 row (or NodeOut() built with a bare {} row) never crashes a
    # consumer reading ``node.metadata.get("run_prepared_at")``.
    metadata: Dict[str, Any] = Field(default_factory=dict)
    # mig 390 (M3 PR-I): form-based deliverables. ``form_schema`` is copied
    # verbatim from the template node at instantiation (I2); ``form_data`` is
    # the live values entered into this node's form — instance-only, no
    # template-side counterpart. Declared here for the same reason
    # ``metadata`` was above: without a declared field pydantic silently
    # drops it converting the repo row dict → NodeOut.
    form_schema: List[FormFieldDef] = Field(default_factory=list)
    form_data: Dict[str, Any] = Field(default_factory=dict)
    # mig 391 (M3 PR-J): dependency edges — instance node ids (real snowflake
    # ids, NOT payload indices; unlike the template side, an instance node's
    # id is stable once created) this node depends on. Same "declare it or
    # pydantic silently drops it" reason as metadata/form_schema above.
    # Instance-node-field convention (controller ruling, J-wave): optional +
    # default-empty so a pre-mig-391 row (or a bare-dict NodeOut) never
    # crashes a consumer reading ``node.depends_on``.
    depends_on: List[str] = Field(default_factory=list)
    # mig 395 (M4 Autopilot §1): instance-only runtime "heads up before you
    # start" text — same declare-it-or-pydantic-silently-drops-it reason as
    # metadata/form_schema/depends_on above. Default '' matches the column's
    # server_default so a pre-mig-395 row never crashes a consumer.
    brief: str = ""


class ProjectWorkflowOut(BaseModel):
    """GET /projects/{id}/workflow payload."""

    has_workflow: bool
    current_node_id: Optional[str] = None
    agents_active: int = 0
    nodes: List[NodeOut] = Field(default_factory=list)


class NodePatch(BaseModel):
    """PATCH /projects/{id}/workflow/nodes/{node_id}. Every field optional;
    ``exclude_unset`` distinguishes "clear to null" from "leave unchanged"."""

    owner_user_id: Optional[UUID] = None
    owner_agent_id: Optional[UUID] = None
    members: Optional[List[TemplateNodeMemberIn]] = None
    planned_start: Optional[date] = None
    planned_due: Optional[date] = None
    skipped: Optional[bool] = None
    # mig 390 (M3 PR-I): the live values entered into this node's deliverable
    # form. Deliberately NOT paired with a ``form_schema`` field here — the
    # form's field definitions are template-layer config, copied at
    # instantiation and frozen; an instance may fill in data but may not
    # change what fields exist. ``form_schema`` therefore has no place on this
    # patch model at all (see test_workflow_form_schema.py).
    form_data: Optional[Dict[str, Any]] = None
    # mig 391 (M3 PR-J): dependency edges are instance STRUCTURE (like members/
    # add/delete), not template config, so they full-replace on an instance
    # PATCH — unlike form_schema, which is frozen at instantiation. Values are
    # real instance node ids (this node's live project siblings), never
    # payload indices (contrast TemplateNodeIn.depends_on). Backward-only
    # (sort_order strictly smaller) is validated against the DB in
    # ProjectStageNodesRepository.update_node — a schema-level check can't see
    # sibling sort_order values.
    depends_on: Optional[List[str]] = None
    # mig 395 (M4 Autopilot §1): runtime data (same PATCH-able-instance-field
    # treatment as form_data above) — writable any time before/while the node
    # is open. Deliberately NOT ``events`` (auto_start stays config, frozen at
    # instantiation, never accepted here — see WorkflowNodeEvents.auto_start).
    brief: Optional[str] = None

    @model_validator(mode="after")
    def _owner_xor(self) -> "NodePatch":
        if self.owner_user_id is not None and self.owner_agent_id is not None:
            raise ValueError("owner_user_id and owner_agent_id are mutually exclusive")
        return self


class NodeCreate(BaseModel):
    """POST /projects/{id}/workflow/nodes (W3-1). Add a node to a live instance
    from the node bank (``source_stage_id``) OR blank (``name``) — exactly one.

    A library add inherits the bank row's name / deliverable / review defaults;
    a blank add takes the supplied ``name`` with those defaults off. ``sort_order``
    is the insert position (existing nodes at or after it shift down by one);
    ``parallel_group`` optionally drops the node into a group.
    """

    # Snowflake id rides as a string at the API boundary (bigIntSafeFetch).
    source_stage_id: Optional[str] = None
    name: Optional[str] = Field(default=None, min_length=1, max_length=200)
    sort_order: int
    parallel_group: Optional[int] = None

    @model_validator(mode="after")
    def _source_xor(self) -> "NodeCreate":
        if (self.source_stage_id is None) == (self.name is None):
            raise ValueError(
                "provide exactly one of source_stage_id (from library) or name (blank)"
            )
        return self


# Blocked-reason codes the node-delete guard can return (spec §5/§8, W3-1). A
# node may only be removed while it is still ``pending``, carries no mirror
# issue, and is not part of the current active group; each failure maps to 409.
DELETE_BLOCK_NOT_PENDING = "NODE_NOT_PENDING"
DELETE_BLOCK_HAS_ISSUE = "NODE_HAS_ISSUE"
DELETE_BLOCK_ACTIVE = "NODE_IN_ACTIVE_GROUP"


class NodeDeleteBlocked(Exception):
    """Raised by the delete guard with a machine reason (router → 409)."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


# Dependency-edge validation (mig 391, M3 PR-J). Shared by BOTH the template
# full-replace path (workflow_templates_repository._validate_deps_backward)
# and the instance PATCH path (project_stage_nodes_repository.update_node) —
# a depends_on target must exist and have a strictly smaller sort_order than
# the dependent node; a self-reference always fails this (its own sort_order
# is never smaller than itself) and shares the same code (spec §3: "自依赖同
# 罪"). Router maps this to 422, code envelope matches the existing
# {"code": ..., "message": ...} shape (see episodes_router.delete_episode).
DEP_BACKWARD_ONLY = "DEP_BACKWARD_ONLY"


class DepsBackwardOnly(Exception):
    """Raised by the deps validator (repo layer) on any backward-only
    violation (missing target / self-dep / forward dep). Router → 422."""

    def __init__(self, reason: str = DEP_BACKWARD_ONLY) -> None:
        super().__init__(reason)
        self.reason = reason


# ── advance preview / execute (PR-B) ────────────────────────────────────────

# Blocked-reason codes the advance predicate can return (spec §7). The frontend
# confirm dialog maps each to copy — the server never sends free text.
BLOCK_NOT_MANAGER_OR_EDITOR = "NOT_MANAGER_OR_EDITOR"
BLOCK_REVIEW_PENDING = "REVIEW_PENDING"
BLOCK_DELIVERABLE_MISSING = "DELIVERABLE_MISSING"
BLOCK_NO_NEXT = "NO_NEXT"
# mig 390 (M3 PR-I): a required form field on a target-group node is unfilled.
# Parallel to (never confused with) BLOCK_DELIVERABLE_MISSING — a deliverable
# is a filed file, a form field is a typed value (spec §2).
BLOCK_FORM_INCOMPLETE = "FORM_INCOMPLETE"
# mig 391 (M3 PR-J): the TARGET group (the one forward would land on) has a
# node whose declared dependency is not yet done/skipped. Unlike
# REVIEW_PENDING/DELIVERABLE_MISSING/FORM_INCOMPLETE above — which all gate
# the CURRENT group's own completion — this gates the NEXT group's readiness
# to start (spec §3). Back (retreat) is never gated by dependencies.
BLOCK_DEPS_PENDING = "DEPS_PENDING"


class AdvanceNodeRef(BaseModel):
    """A node named in an advance preview (closing / creating lists)."""

    node_id: str
    name: str
    assignee_user_id: Optional[str] = None
    assignee_agent_id: Optional[str] = None
    due_date: Optional[str] = None


class AdvancePreview(BaseModel):
    """Server-computed advance ruling. ``compute_advance_preview`` and
    ``execute_advance`` share the predicate that fills this (#1400)."""

    direction: Literal["forward", "back"]
    will_advance: bool
    blocked_reason: Optional[str] = None
    closing: List[AdvanceNodeRef] = Field(default_factory=list)
    creating: List[AdvanceNodeRef] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    # mig 390 (M3 PR-I): required form-field LABELS (never keys) missing from
    # the ACTIVE (current) group when blocked_reason == BLOCK_FORM_INCOMPLETE
    # — Gate 3 checks the group that's about to close, not the target group
    # Gate 5 (deps) checks (fixed M3 final review #4: this comment previously
    # said "target group", which is Gate 5's language, not Gate 3's).
    missing_fields: List[str] = Field(default_factory=list)
    # mig 391 (M3 PR-J): names of unmet-dependency nodes (deduped, sort_order
    # order) when blocked_reason == BLOCK_DEPS_PENDING.
    waiting_on: List[str] = Field(default_factory=list)


class AdvanceRequest(BaseModel):
    """POST /projects/{id}/advance body (direction also accepted as a query)."""

    direction: Literal["forward", "back"] = "forward"


def preview_to_dict(preview: AdvancePreview) -> dict[str, Any]:
    """AdvancePreview → plain dict (envelope ``data``)."""
    return preview.model_dump()
