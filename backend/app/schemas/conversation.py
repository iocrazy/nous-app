"""Pydantic models for Unified Conversations API (Phase 1 — Task 7).

Port of schemas/chat.py with the Rename Map applied:
  ChannelCreate       → ConversationCreate
  ChannelOut          → ConversationOut
  team_id             → scope_id
  last_message_seq    → last_seq
  content_type        → type
  reply_to_id         → parent_id
  channel_id          → conversation_id

BIGINT snowflake fields (id, scope_id, last_seq, conversation_id, seq,
parent_id) are coerced to str at the schema boundary so JS clients never
lose precision.  sender_id UUID→str coercion is copied verbatim from
chat.py's MessageOut (asyncpg returns UUID objects that must be serialised
as str; without this every message endpoint 500s on ResponseValidationError).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, field_validator


class ConversationCreate(BaseModel):
    type: Literal["group", "dm", "public"]
    scope_id: int
    name: Optional[str] = None
    topic: Optional[str] = None
    history_mode: Literal["shared", "joined"] = "shared"
    member_ids: list[str] = Field(
        default_factory=list
    )  # UUIDs to add besides the creator


class ConversationOut(BaseModel):
    id: str
    scope_id: str
    type: str
    history_mode: str
    name: Optional[str] = None
    topic: Optional[str] = None
    last_seq: str = "0"
    unread: int = 0
    mention_count: int = 0
    created_at: datetime

    @field_validator("id", "scope_id", mode="before")
    @classmethod
    def _coerce_bigint_str(cls, v: Any) -> str:
        return str(v)

    @field_validator("last_seq", mode="before")
    @classmethod
    def _coerce_last_seq(cls, v: Any) -> str:
        return "0" if v is None else str(v)


class MemberAdd(BaseModel):
    user_ids: list[str] = Field(default_factory=list)


class MessageCreate(BaseModel):
    type: Literal["text", "media_card", "task_card", "image"] = "text"
    body: dict[str, Any] = Field(default_factory=dict)
    parent_id: Optional[int] = None


class MessageOut(BaseModel):
    id: str
    conversation_id: str
    seq: str
    sender_id: Optional[str] = None
    sender_type: str
    type: str
    body: dict[str, Any]
    parent_id: Optional[str] = None
    edited_at: Optional[datetime] = None
    deleted_at: Optional[datetime] = None
    created_at: datetime

    @field_validator("sender_id", mode="before")
    @classmethod
    def _coerce_sender_id(cls, v: Any) -> Optional[str]:
        # asyncpg returns the auth.users UUID column as a uuid.UUID object;
        # MessageOut's contract is a string, so coerce (None stays None).
        # Without this, every message endpoint 500s on ResponseValidationError.
        return None if v is None else str(v)

    @field_validator("id", "conversation_id", "seq", mode="before")
    @classmethod
    def _coerce_bigint_str(cls, v: Any) -> str:
        return str(v)

    @field_validator("parent_id", mode="before")
    @classmethod
    def _coerce_parent_id(cls, v: Any) -> Optional[str]:
        return None if v is None else str(v)


class MarkReadIn(BaseModel):
    last_read_seq: int


class AgentAdd(BaseModel):
    agent_slug: str


class MemberOut(BaseModel):
    """One conversation member — a user or an agent (group settings drawer)."""

    member_type: Literal["user", "agent"]
    user_id: Optional[str] = None
    agent_id: Optional[str] = None
    role: str = "member"
    name: Optional[str] = None
    email: Optional[str] = None
    agent_slug: Optional[str] = None
    joined_at: datetime

    @field_validator("user_id", "agent_id", mode="before")
    @classmethod
    def _coerce_uuid_str(cls, v: Any) -> Optional[str]:
        # asyncpg returns UUID columns as uuid.UUID objects; serialise as str.
        return None if v is None else str(v)


class MemberRoleSet(BaseModel):
    role: Literal["admin", "member"]


class OwnerTransfer(BaseModel):
    to_user_id: str


class ConversationUpdate(BaseModel):
    name: Optional[str] = None
    type: Optional[Literal["group", "public"]] = None
