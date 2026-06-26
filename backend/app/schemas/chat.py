"""Pydantic models for Team Chat (PHASE-1 backend foundation)."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


class ChannelCreate(BaseModel):
    type: Literal["group", "dm", "public"]
    team_id: int
    name: Optional[str] = None
    topic: Optional[str] = None
    history_mode: Literal["shared", "joined"] = "shared"
    member_ids: list[str] = Field(
        default_factory=list
    )  # UUIDs to add besides the creator


class ChannelOut(BaseModel):
    id: int
    team_id: int
    type: str
    history_mode: str
    name: Optional[str] = None
    topic: Optional[str] = None
    last_message_seq: int = 0
    unread: int = 0
    mention_count: int = 0
    created_at: datetime


class MemberAdd(BaseModel):
    user_ids: list[str] = Field(default_factory=list)


class MessageCreate(BaseModel):
    content_type: Literal["text", "media_card", "task_card"] = "text"
    body: dict[str, Any] = Field(default_factory=dict)
    reply_to_id: Optional[int] = None


class MessageOut(BaseModel):
    id: int
    channel_id: int
    seq: int
    sender_id: Optional[str] = None
    sender_type: str
    content_type: str
    body: dict[str, Any]
    reply_to_id: Optional[int] = None
    edited_at: Optional[datetime] = None
    deleted_at: Optional[datetime] = None
    created_at: datetime


class MarkReadIn(BaseModel):
    last_read_seq: int


class AgentAdd(BaseModel):
    agent_slug: str
