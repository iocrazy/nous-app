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


class SkillFileUpsert(BaseModel):
    path: str = Field(..., min_length=1, max_length=500)
    content: Optional[str] = None
    file_type: Literal["markdown", "script", "text-asset", "binary-ref"] = "markdown"
    binary_url: Optional[str] = None


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
    """Output of prompt_composer.compose()."""

    agent_id: UUID
    agent_slug: str
    model: str
    temperature: float
    max_tokens: int
    system_message: str
    tools: list[dict]  # function-calling schema array
    skill_manifest: list[dict]  # [{slug, name, description}]
    cache_fingerprint: str  # sha1 of stable prefix inputs
