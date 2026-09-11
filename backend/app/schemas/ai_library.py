"""Pydantic models for AI Library (agents, prompts-as-agent-fields, skills, skill files)."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Optional
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    model_validator,
)

# ---------- Agents ----------


class ChatPermissionsIn(BaseModel):
    """Partial chat-permission patch merged into capability_profile.chat.

    All fields optional so the client can toggle one at a time. ``None`` means
    "leave unchanged"; absent in storage means denied (see agent_chat_caps).
    """

    enabled: Optional[bool] = None
    read_team_resources: Optional[bool] = None
    auto_broadcast: Optional[bool] = None
    allowed_team_ids: Optional[list[int]] = None


class ChatPermissionsOut(BaseModel):
    """Read shape exposed on AgentOut — the resolved (fail-closed) chat caps.

    Built from agent_chat_caps so the wire value matches enforcement exactly.
    We expose ONLY chat perms, never the raw capability_profile (which holds
    internal gating like tool_blacklist).
    """

    enabled: bool = False
    read_team_resources: bool = False
    auto_broadcast: bool = False
    allowed_team_ids: list[int] = Field(default_factory=list)

    @classmethod
    def from_caps(cls, caps: Any) -> ChatPermissionsOut:
        """Build from a ChatCaps instance (duck-typed to avoid circular import)."""
        return cls(
            enabled=caps.enabled,
            read_team_resources=caps.read_team_resources,
            auto_broadcast=caps.auto_broadcast,
            allowed_team_ids=list(caps.allowed_team_ids),
        )


# Sanity ceiling for the per-turn media cap. Not an enforcement limit (the
# gate reads whatever number is stored); purely a guard so a fat-fingered
# grant can't ask for thousands of paid generations in one turn.
MAX_MEDIA_CALLS_PER_TURN = 100


class MediaCapsIn(BaseModel):
    """Partial patch for capability_profile.capabilities.media (A1/A8).

    Deep-merged by the router, so a client toggling ``image`` alone keeps the
    stored ``max_calls_per_turn``. ``None`` means "leave unchanged".
    """

    model_config = ConfigDict(extra="forbid")

    image: Optional[StrictBool] = None
    video: Optional[StrictBool] = None
    # StrictInt, not int: lax mode would turn `true` into 1 and `"4"` into 4.
    # high_risk_caps._as_media_cap explicitly rejects bool for the same reason.
    # ge=0 mirrors that reader too — it treats a negative as malformed and
    # falls back to its conservative default, so accepting one here would let
    # the client believe it set a cap it didn't.
    max_calls_per_turn: Optional[StrictInt] = Field(
        default=None, ge=0, le=MAX_MEDIA_CALLS_PER_TURN
    )


class CapabilitiesIn(BaseModel):
    """Partial grant patch merged into capability_profile.capabilities (A8).

    The counterpart write path to ``high_risk_caps.high_risk_caps`` — every
    field name and value here is exactly what that fail-closed reader parses,
    so a successful PATCH provably changes enforcement (spec §2).

    Strictness matters more here than anywhere else in this file: the reader
    defaults every unparseable value to DENIED, so a payload we accept but the
    reader rejects would look like a grant and behave like a denial. Hence
    ``extra="forbid"`` (a misspelled dimension 422s instead of writing a dead
    key) and exact Literals for write_level (``"admin"`` etc. are rejected at
    the schema, never coerced into something permissive).

    Booleans are ``StrictBool`` on purpose. Pydantic's default lax mode
    coerces ``"yes"`` / ``1`` / ``"true"`` into ``True``, which here would mean
    a sloppy client payload silently produces a REAL grant — while
    high_risk_caps._as_bool grants only on a literal JSON ``true``. Strict
    types keep the two ends of the wire agreeing on what "granted" means.

    ``None`` means "leave unchanged"; ``write_level="none"`` / ``false`` are
    the explicit REVOKE values.
    """

    model_config = ConfigDict(extra="forbid")

    write_level: Optional[Literal["none", "read", "propose", "write"]] = None
    # DEPRECATED-UI (2026-08-10 spec §4): 无工具消费,UI 已下架;字段保留仅为向后兼容,勿删(extra="forbid" 下删字段会 422 旧客户端)。
    delete: Optional[StrictBool] = None
    media: Optional[MediaCapsIn] = None
    cross_episode_read: Optional[StrictBool] = None
    # DEPRECATED-UI (2026-08-10 spec §4): 无工具消费,UI 已下架;字段保留仅为向后兼容,勿删(extra="forbid" 下删字段会 422 旧客户端)。
    external_publish: Optional[StrictBool] = None


class MediaCapsOut(BaseModel):
    """Resolved (fail-closed) media caps — mirrors high_risk_caps.MediaCaps."""

    image: bool = False
    video: bool = False
    max_calls_per_turn: int = 4


class CapabilitiesOut(BaseModel):
    """Read shape on AgentOut — the resolved (fail-closed) high-risk caps.

    Built from ``high_risk_caps`` so the wire value matches enforcement
    exactly, the same discipline ChatPermissionsOut follows for the ``chat``
    subtree. Still NOT the raw capability_profile: the low-risk tuning keys
    (tool_blacklist / allowed_skills / ...) stay server-side.
    """

    write_level: Literal["none", "read", "propose", "write"] = "none"
    delete: bool = False
    media: MediaCapsOut = Field(default_factory=MediaCapsOut)
    cross_episode_read: bool = False
    external_publish: bool = False

    @classmethod
    def from_caps(cls, caps: Any) -> CapabilitiesOut:
        """Build from a HighRiskCaps instance (duck-typed, avoids circular import)."""
        return cls(
            write_level=caps.write_level,
            delete=caps.delete,
            media=MediaCapsOut(
                image=caps.media.image,
                video=caps.media.video,
                max_calls_per_turn=caps.media.max_calls_per_turn,
            ),
            cross_episode_read=caps.cross_episode_read,
            external_publish=caps.external_publish,
        )


class AgentBase(BaseModel):
    slug: str = Field(..., min_length=1, max_length=64)
    name: str = Field(..., min_length=1, max_length=200)
    description: Optional[str] = None
    icon: Optional[str] = Field(
        default=None,
        max_length=64,
        description="Lucide icon slug (e.g. 'bot', 'sparkles'); UI falls back to default when null.",
    )
    model: str = "qwen-max"
    temperature: float = 0.7
    max_tokens: int = 4096
    identity_md: Optional[str] = None
    soul_md: Optional[str] = None
    agent_md: Optional[str] = None
    # Roster grouping for the gallery (mig 400): writing / art / tools.
    # NULL = ungrouped; the UI buckets those under tools rather than
    # rendering a fourth section.
    agent_group: Optional[str] = Field(default=None, max_length=32)


class AgentOut(AgentBase):
    id: UUID
    is_system_preset: bool = False
    team_id: Optional[int] = None
    project_id: Optional[int] = None
    user_id: Optional[UUID] = None
    enabled: bool = True
    created_at: datetime
    updated_at: datetime
    skill_ids: list[int] = Field(default_factory=list)  # BIGINT FK to skills.id
    # Phase 2 PR 2.9 — denormalized names for the scope badge in the UI.
    # Populated by the router when team_id / project_id is set. Both optional
    # (absent for private agents and system presets).
    team_name: Optional[str] = None
    project_name: Optional[str] = None
    # Budget guard (migration 148). Null budget == unlimited. paused_reason
    # is read-through so the sidebar pulse + editor banner can both show
    # pause state without a second fetch. 'budget' is set by the sweeper,
    # 'manual' by admin PATCH — see AgentUpdate for write policy.
    monthly_token_budget: Optional[int] = None
    monthly_cost_cents_budget: Optional[int] = None
    paused_reason: Optional[Literal["budget", "manual"]] = None
    # Run limits (mig 286, paperclip P4). NULL = unlimited.
    timeout_sec: Optional[int] = None
    max_concurrent_runs: Optional[int] = None
    # Team Chat PHASE-0: resolved (fail-closed) chat capabilities. Populated by
    # the router from agent_chat_caps(row); defaults to all-false so clients
    # never have to guess. NOT the raw capability_profile (no internal-gating leak).
    chat_permissions: ChatPermissionsOut = Field(default_factory=ChatPermissionsOut)
    # A8: resolved (fail-closed) HIGH-RISK capabilities, populated by the router
    # from high_risk_caps(row). Without this the settings UI could offer no
    # toggles — it can't render state it can't read. Same no-leak rule as
    # chat_permissions: only the granted dimensions, never capability_profile.
    capabilities: CapabilitiesOut = Field(default_factory=CapabilitiesOut)
    # Agent-overrides (mig 341). On single-get/patch: which layer produced the
    # merged view ('user' beats 'team') + which fields it replaced. On list:
    # override_scopes marks agents the caller (or their teams) customized —
    # drives the sidebar "customized" badge + reset affordance.
    override_scope: Optional[Literal["user", "team"]] = None
    override_fields: list[str] = Field(default_factory=list)
    override_scopes: list[str] = Field(default_factory=list)


class AgentUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    icon: Optional[str] = Field(default=None, max_length=64)
    model: Optional[str] = None
    temperature: Optional[float] = None
    max_tokens: Optional[int] = None
    identity_md: Optional[str] = None
    soul_md: Optional[str] = None
    agent_md: Optional[str] = None
    agent_group: Optional[str] = Field(default=None, max_length=32)
    enabled: Optional[bool] = None
    skill_ids: Optional[list[int]] = None  # replace binding
    # Budget guard. 0 or null ⇒ unlimited. Positive int ⇒ hard cap; the
    # heartbeat sweeper flips paused_reason='budget' when exceeded and
    # restores it to null when spend drops below on month rollover.
    monthly_token_budget: Optional[int] = Field(default=None, ge=0)
    monthly_cost_cents_budget: Optional[int] = Field(default=None, ge=0)
    # Agent-overrides (mig 341): where a SYSTEM PRESET content edit lands.
    # Default 'user' = personal layer; 'team' (+ override_team_id, caller must
    # be that team's owner or a platform admin) = shared team layer.
    override_scope: Optional[Literal["user", "team"]] = None
    override_team_id: Optional[int] = None
    # Admin can pause/resume. 'budget' is server-owned (sweeper-only) and is
    # rejected here via the Literal so a client can't forge a fake budget
    # pause. Pass null to resume from either a manual or a budget pause.
    paused_reason: Optional[Literal["manual"]] = None
    # Run limits (mig 286). 0/None timeout = no cap; concurrency >= 1.
    timeout_sec: Optional[int] = Field(default=None, ge=0)
    max_concurrent_runs: Optional[int] = Field(default=None, ge=1)
    # Team Chat PHASE-0: merged into capability_profile.chat by the router
    # (deep-merge, never clobbers the Phase 4.5 keys). Allowed even on
    # system-preset agents (permissions are governance, not content).
    chat_permissions: Optional[ChatPermissionsIn] = None
    # A8: merged into capability_profile.capabilities by the router (deep-merge
    # down to media.*, never clobbers the sibling `chat` subtree). This is the
    # ONLY write path for the A1 high-risk gate — before it existed every
    # screenwriting tool was permanently denied because nothing could set the
    # keys high_risk_caps reads. Gated exactly like chat_permissions: granting
    # a high-risk capability must never be easier than editing the prompt.
    capabilities: Optional[CapabilitiesIn] = None
    # Free-text justification for a chat_permissions/capabilities change,
    # captured into agent_permission_audits.reason (2026-08-10 spec §3). NOT a
    # content field — the router excludes it from the content-updates dict
    # before it ever reaches update_fields_versioned's `updates` arg.
    permission_change_reason: Optional[str] = Field(default=None, max_length=500)


class PermissionAuditItem(BaseModel):
    """One row of an agent's permission-change audit trail (read-only)."""

    id: str
    changed_by: str
    before: dict[str, Any]
    after: dict[str, Any]
    reason: Optional[str] = None
    created_at: datetime


class PermissionAuditListOut(BaseModel):
    items: list[PermissionAuditItem] = Field(default_factory=list)


class AgentCreate(BaseModel):
    """Payload for POST /agents — create a new user-owned (non-preset) agent.

    Optional ``fork_from`` copies identity_md / soul_md / agent_md / model /
    temperature / max_tokens from an existing agent (system preset or user-owned)
    as a starting point. Skill bindings are NOT copied — the user adds those
    explicitly via PATCH afterwards.

    Scope (Phase 2 PR 2.9): optional ``team_id`` / ``project_id`` make the new
    agent visible to all members of that team / project. If neither is set,
    the agent is private to the creating user. The two fields are mutually
    exclusive — set at most one.
    """

    slug: str = Field(..., min_length=1, max_length=64, pattern=r"^[a-z0-9_-]+$")
    name: str = Field(..., min_length=1, max_length=200)
    description: Optional[str] = None
    fork_from: Optional[str] = Field(
        default=None, description="Slug of an existing agent to copy content from."
    )
    # Scope — at most one of team_id / project_id. Both None = private per-user.
    team_id: Optional[int] = None
    project_id: Optional[int] = None
    # Explicit field overrides. If fork_from is also set, these win.
    model: Optional[str] = None
    temperature: Optional[float] = None
    max_tokens: Optional[int] = None
    identity_md: Optional[str] = None
    soul_md: Optional[str] = None
    agent_md: Optional[str] = None
    # Omitted on a fork ⇒ inherit the source agent's group (see create_agent).
    agent_group: Optional[str] = Field(default=None, max_length=32)


class AgentFault(BaseModel):
    """Why an agent is unhealthy, plus what to do about it.

    ``detail`` is NOT optional in practice for every kind we emit — the spec
    requires a fault badge to carry one actionable line, so a bare kind with
    no remedy would defeat the point.
    """

    kind: Literal["budget", "manual", "dead_runs"]
    detail: str


class AgentStatsItem(BaseModel):
    runs_7d: int = 0
    tokens_7d: int = 0
    #: Spend over the same window. Named ``_7d`` like its siblings even though
    #: the window follows the ``days`` query param.
    cost_cents_7d: int = 0
    running_count: int = 0
    needs_input_count: int = 0
    fault: Optional[AgentFault] = None
    #: Set when the agent's most recent run was cut short by something that is
    #: not the agent's fault (currently only a backend restart mid-run). Kept
    #: OFF ``fault`` on purpose: everything reading ``fault`` — the red badge,
    #: the "Faults only" filter, the fault counter — means "this agent needs
    #: fixing", and a routine deploy does not. Carries a bare reason code so
    #: the copy can be localized client-side.
    interrupted_reason: Optional[Literal["restart"]] = None


class AgentStatsResponse(BaseModel):
    """Batch stats for the AI Library gallery, keyed by agent id (str UUID)."""

    items: dict[str, AgentStatsItem] = Field(default_factory=dict)


# ---------- Skills & files ----------


class SkillFileOut(BaseModel):
    id: UUID
    skill_id: int
    path: str
    content: Optional[str] = None
    file_type: Literal["markdown", "script", "text-asset", "binary-ref"]
    binary_url: Optional[str] = None
    updated_at: datetime
    # Skill scanner findings (only populated on upsert response — empty
    # on read paths). Surfaced so the UI can show a security badge on
    # the file. Each item: {line, category, severity, snippet, message}.
    security_findings: list[dict] = []


class SkillFileUpsert(BaseModel):
    path: str = Field(..., min_length=1, max_length=500)
    content: Optional[str] = None
    file_type: Literal["markdown", "script", "text-asset", "binary-ref"] = "markdown"
    binary_url: Optional[str] = None


class SkillAgentRef(BaseModel):
    """Slug + name of an agent that binds this skill. Used by SkillOut.agents
    to drive the "Used by" badge in the skill detail UI."""

    slug: str
    name: str


class SkillOut(BaseModel):
    id: int
    slug: Optional[str] = None
    name: str
    description: Optional[str] = None
    body_md: Optional[str] = None
    category: Optional[str] = None
    icon: Optional[str] = None
    is_public: bool = False
    team_id: Optional[int] = None
    project_id: Optional[int] = None
    output_format: Optional[str] = None
    frontmatter_json: dict = Field(default_factory=dict)
    files: list[SkillFileOut] = Field(default_factory=list)
    # Reverse index of which agents bind this skill, via an agent_skills
    # join. Empty list for un-bound skills — the gallery's "no agent uses
    # this" warning depends on that being [] rather than absent. Populated
    # on BOTH the detail endpoint (per skill) and the list endpoint (one
    # batched JOIN for the page — see skill_repository.map_binding_agents).
    agents: list[SkillAgentRef] = Field(default_factory=list)
    updated_at: datetime
    # Phase 2 minor cleanup — denormalized names for the scope badge in the UI.
    # Populated by the router when team_id / project_id is set. Both optional
    # (absent for private skills and system presets). Mirrors AgentOut.
    team_name: Optional[str] = None
    project_name: Optional[str] = None


class SkillUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    body_md: Optional[str] = None
    category: Optional[str] = None
    icon: Optional[str] = None
    output_format: Optional[str] = None
    frontmatter_json: Optional[dict] = None


class SkillCreate(BaseModel):
    """Payload for POST /skills — create a new user-owned (non-preset) skill.

    Mirrors ``AgentCreate`` in spirit:

    * Optional ``fork_from`` copies body_md / frontmatter_json / category /
      icon / output_format / description from an existing skill as a
      starting point. Skill files (the ``skill_files`` rows) are NOT copied
      — fork only clones the primary SKILL.md body + metadata.
    * Scope: optional ``team_id`` / ``project_id`` make the new skill visible
      to all members of that team / project. If neither is set, the skill
      is private to the creating user. The two fields are mutually
      exclusive — set at most one.
    """

    slug: str = Field(..., min_length=1, max_length=64, pattern=r"^[a-z0-9_-]+$")
    name: str = Field(..., min_length=1, max_length=200)
    description: Optional[str] = None
    category: Optional[str] = None
    # Default None so fork_from can copy the source's icon. The router falls
    # back to "✨" for non-forked skills where the caller omits it.
    icon: Optional[str] = None
    body_md: Optional[str] = None
    frontmatter_json: Optional[dict] = None
    output_format: Optional[str] = None
    # Scope — at most one of team_id / project_id. Both None = private per-user.
    team_id: Optional[int] = None
    project_id: Optional[int] = None
    fork_from: Optional[str] = Field(
        default=None,
        description="Slug of an existing skill to copy body/metadata from.",
    )

    @model_validator(mode="after")
    def _mutually_exclusive_scope(self) -> "SkillCreate":
        if self.team_id is not None and self.project_id is not None:
            raise ValueError("team_id and project_id are mutually exclusive")
        return self


# ---------- Composer output ----------


class ComposedSystemPrompt(BaseModel):
    """Output of prompt_composer.compose().

    Two fingerprints (M1.B, plan-eng-review Issue 2.2):
      - prefix_fingerprint: hash of agent + skills (stable across turns)
      - dynamic_fingerprint: prefix_fingerprint + recalled memory ids hash
        (changes when memory recall set changes)

    Downstream prompt-cache providers use prefix for prefix-cache reuse,
    dynamic for staleness checks. Splitting prevents cross-user memory
    leakage via stale cache hits (the failure mode that motivated the
    split).

    cache_fingerprint is kept as an alias of prefix_fingerprint for
    back-compat with existing call sites (Phase-1 code reads this name).
    """

    agent_id: UUID
    agent_slug: str
    model: str
    temperature: float
    max_tokens: int
    system_message: str
    tools: list[dict]  # function-calling schema array
    skill_manifest: list[dict]  # [{slug, name, description}]
    cache_fingerprint: str  # alias of prefix_fingerprint (back-compat)
    # mig 286: per-run wall-clock cap (seconds) carried from the agent row.
    # AgentRunner checks it between LLM iterations. None/0 = no cap.
    timeout_sec: Optional[int] = None
    prefix_fingerprint: str = ""  # M1.B: stable prefix hash
    dynamic_fingerprint: str = ""  # M1.B: prefix + memory recall hash
    recalled_memory_ids: list[UUID] = Field(default_factory=list)
    # 三期 3a T8c: the output versions this turn cites, as
    # ``{kind, ref_id, version, title}``. Carried beside the prompt the way
    # ``timeout_sec`` is — it is not prompt text (the frame the model reads is
    # already spliced into ``system_message``); it rides here so the runner can
    # put it on the turn's ``user`` transcript event, which is what the person
    # reading the run sees. Empty on every turn that cites nothing, and the
    # runner then writes no key at all.
    referenced_outputs: list[dict] = Field(default_factory=list)
