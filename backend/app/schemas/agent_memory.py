"""Pydantic schemas for user-facing agent memory endpoints."""

from __future__ import annotations

from typing import List

from pydantic import BaseModel


class UserMemoryItem(BaseModel):
    """A single memory entry visible to the requesting user.

    Note: owner_user_id is intentionally omitted — is_owner is computed
    server-side so no other user's UUID leaks to the client.
    """

    id: int
    title: str
    body_md: str
    kind: str
    scope: str
    visibility: str
    when_to_use: str
    created_at: str
    is_owner: bool


class UserMemoryListResponse(BaseModel):
    """Response envelope for the user-facing memory list endpoint."""

    items: List[UserMemoryItem]


class AgentMemoryDeleteResponse(BaseModel):
    """``DELETE /agent-memory/{memory_id}``: always ``{"deleted": true}``.

    A row that is not there, or not the caller's, is a typed 404
    (``not_found_or_out_of_scope``) rather than ``{"deleted": false}``.
    """

    deleted: bool
