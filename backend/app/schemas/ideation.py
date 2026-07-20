"""Ideation topic-pool API schemas (M1.5).

Mirrors the mig 382 ``topics`` table. A topic = cover + title + reference, where
the reference is a soft pointer to exactly one source (or none = blank). Snowflake
ids ride as strings at the API boundary (bigIntSafeFetch).
"""

from __future__ import annotations

from typing import List, Literal, Optional

from pydantic import BaseModel, Field, model_validator

# Status flow (spec §1): candidate → shortlisted → produced → archived.
TopicStatus = Literal["candidate", "shortlisted", "produced", "archived"]

# The four soft-reference source ids (all snowflake strings). At most one may be
# set on a topic; none = a blank hand-written card.
_SOURCE_FIELDS = ("note_id", "resource_id", "media_id", "inspiration_topic_id")


class TopicCreate(BaseModel):
    """POST /ideation/topics body. team_id is a query param; created_by comes
    from auth context in the router."""

    title: str = Field(min_length=1, max_length=300)
    cover_url: Optional[str] = None
    excerpt: Optional[str] = None
    # At most one source reference (snowflake string). None across all four = a
    # blank hand-written topic.
    note_id: Optional[str] = None
    resource_id: Optional[str] = None
    media_id: Optional[str] = None
    inspiration_topic_id: Optional[str] = None

    @model_validator(mode="after")
    def _at_most_one_source(self) -> "TopicCreate":
        set_count = sum(1 for f in _SOURCE_FIELDS if getattr(self, f) is not None)
        if set_count > 1:
            raise ValueError(
                "a topic references at most one source "
                "(note_id / resource_id / media_id / inspiration_topic_id)"
            )
        return self


class TopicUpdate(BaseModel):
    """PATCH /ideation/topics/{id}. Every field optional; ``exclude_unset``
    distinguishes an explicit null from "leave unchanged". ``status`` drives the
    candidate → shortlisted → produced → archived flow."""

    title: Optional[str] = Field(default=None, min_length=1, max_length=300)
    cover_url: Optional[str] = None
    excerpt: Optional[str] = None
    status: Optional[TopicStatus] = None


class TopicOut(BaseModel):
    """GET payload for one topic (ids as strings)."""

    id: str
    team_id: str
    title: str
    cover_url: Optional[str] = None
    excerpt: Optional[str] = None
    status: str
    note_id: Optional[str] = None
    resource_id: Optional[str] = None
    media_id: Optional[str] = None
    inspiration_topic_id: Optional[str] = None
    created_by: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class TopicListOut(BaseModel):
    """Envelope data for GET /ideation/topics."""

    data: List[TopicOut] = Field(default_factory=list)
