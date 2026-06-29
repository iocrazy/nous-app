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
