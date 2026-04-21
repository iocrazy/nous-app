"""Pydantic models for AI Library (agents, prompts-as-agent-fields, skills, skill files)."""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional
from uuid import UUID

from pydantic import BaseModel, Field


# ---------- Agents ----------


class AgentBase(BaseModel):
    slug: str = Field(..., min_length=1, max_length=64)
    name: str = Field(..., min_length=1, max_length=200)
    description: Optional[str] = None
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


class AgentUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    model: Optional[str] = None
    temperature: Optional[float] = None
    max_tokens: Optional[int] = None
    identity_md: Optional[str] = None
    soul_md: Optional[str] = None
    agent_md: Optional[str] = None
    enabled: Optional[bool] = None
    skill_ids: Optional[list[int]] = None  # replace binding


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


class SkillUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    body_md: Optional[str] = None
    category: Optional[str] = None
    icon: Optional[str] = None
    output_format: Optional[str] = None
    frontmatter_json: Optional[dict] = None


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
