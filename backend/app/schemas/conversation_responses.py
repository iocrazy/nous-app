"""Response shapes of the ``/api/v1/conversations`` management routes.

Each model mirrors, key for key, the dict the route (or
``ConversationService``) already returned before it declared a response model
(spec 2026-09-24-openapi-typed-frontend-design.md §5; wire tests in
``tests/api/test_conversations_wire.py``). None of these bodies is wrapped in
the ``{"success", "data"}`` envelope, and none gains one.

The list/create/message routes of the same router were typed earlier with
``ConversationOut`` / ``MessageOut`` / ``MemberOut`` (``app/schemas/conversation.py``).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class ConversationMembersAddedResponse(BaseModel):
    """``POST /{id}/members``: rows actually inserted (existing members skip)."""

    added: int


class ConversationMemberRemovedResponse(BaseModel):
    """``DELETE /{id}/members/{user_id}`` (remove or leave).

    Always ``true``: a delete that matched no row is a 400, not ``false``.
    """

    removed: bool


class ConversationMemberRoleResponse(BaseModel):
    """``PATCH /{id}/members/{user_id}/role``: the role that was written."""

    updated: bool
    role: Literal["admin", "member"]


class ConversationOwnerTransferResponse(BaseModel):
    """``POST /{id}/transfer-owner``."""

    transferred: bool


class ConversationAgentAddedResponse(BaseModel):
    """``POST /{id}/agents``. ``added`` is true even when the agent was
    already a member (the insert is idempotent)."""

    added: bool
    agent_id: str


class ConversationAgentRemovedResponse(BaseModel):
    """``DELETE /{id}/agents/{agent_id}``: ``false`` when the agent was not a
    member of this conversation."""

    removed: bool


class ConversationDissolveResponse(BaseModel):
    """``DELETE /{id}``: ``false`` only when a concurrent dissolve won."""

    archived: bool


class ConversationMarkReadResponse(BaseModel):
    """``POST /{id}/read``."""

    ok: bool


class ConversationAttachmentUploadResponse(BaseModel):
    """``POST /{id}/attachments``: the staged ``generated_media`` row.

    ``id`` is the Snowflake as a string; ``url`` is the cover route of that
    row. ``mime`` / ``file_size_bytes`` are nullable columns.
    """

    id: str
    mime: str | None
    file_size_bytes: int | None
    url: str


class ConversationAttachmentPromoteResponse(BaseModel):
    """``POST /attachments/{id}/promote``: the resource id as a string."""

    promoted_resource_id: str
