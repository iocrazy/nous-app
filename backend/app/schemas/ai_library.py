"""Pydantic models for AI Library (agents, prompts-as-agent-fields, skills, skill files)."""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

# ---------- Agents ----------


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
    enabled: Optional[bool] = None
    skill_ids: Optional[list[int]] = None  # replace binding
    # Budget guard. 0 or null ⇒ unlimited. Positive int ⇒ hard cap; the
    # heartbeat sweeper flips paused_reason='budget' when exceeded and
    # restores it to null when spend drops below on month rollover.
    monthly_token_budget: Optional[int] = Field(default=None, ge=0)
    monthly_cost_cents_budget: Optional[int] = Field(default=None, ge=0)
    # Admin can pause/resume. 'budget' is server-owned (sweeper-only) and is
    # rejected here via the Literal so a client can't forge a fake budget
    # pause. Pass null to resume from either a manual or a budget pause.
    paused_reason: Optional[Literal["manual"]] = None


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
    # Reverse index of which agents bind this skill. Populated by the
    # skill detail endpoint via an agent_skills join. Empty list for
    # un-bound skills. The list endpoint leaves this empty for
    # performance — UI's "Used by" badge only renders in the detail view.
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
    prefix_fingerprint: str = ""  # M1.B: stable prefix hash
    dynamic_fingerprint: str = ""  # M1.B: prefix + memory recall hash
    recalled_memory_ids: list[UUID] = Field(default_factory=list)
