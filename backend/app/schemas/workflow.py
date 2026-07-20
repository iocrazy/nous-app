"""Workflow template API schemas (M1 PR-A).

Mirrors the mig 380 tables. Owner is single (user XOR agent); members are a
list of user-or-agent refs. Guardrails: a template may hold at most
``MAX_NODES_PER_TEMPLATE`` nodes (enforced here → 422); a team at most
``MAX_TEMPLATES_PER_TEAM`` templates (enforced in the router → 422).
"""

from __future__ import annotations

from typing import List, Optional
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

# Soft guardrails (spec §3): keep a template from ballooning and a team from
# hoarding templates. Both surface as 422.
MAX_NODES_PER_TEMPLATE = 30
MAX_TEMPLATES_PER_TEAM = 20


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
    members: List[TemplateNodeMemberIn] = Field(default_factory=list)

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


class TemplateCreate(BaseModel):
    """POST /workflows body. team_id is a query param; created_by is set from
    auth context in the router."""

    name: str = Field(min_length=1, max_length=200)


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
