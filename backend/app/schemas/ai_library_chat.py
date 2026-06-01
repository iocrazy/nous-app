"""Pydantic shapes for AI Library chat sessions + messages.

Mirrors the old /api/v1/ai/* schemas (app/schemas/ai.py) but scoped to
the AI Library world: sessions belong to an agent by ``agent_slug``,
execution goes through AgentRunner + RunRecorder so every chat message
produces an agent_runs row (with tokens, cost, budget check, pulse).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

# BIGINT Snowflake ids (ai_sessions.id, agent_runs.id, ai_messages.session_id)
# arrive from the DB as JSON numbers / Python ints but are modelled as str.
# Pydantic v2 does not coerce int→str by default, so opt in explicitly.
_COERCE_IDS = ConfigDict(coerce_numbers_to_str=True)


# ─── Sessions ───────────────────────────────────────────────────────────────


class SessionCreate(BaseModel):
    """Request body for POST /agents/:slug/sessions."""

    title: str = Field(default="New Chat", max_length=200)
    project_id: Optional[int] = None
    team_id: Optional[int] = None
    context_type: Optional[str] = Field(
        default=None,
        max_length=32,
        description="Free-form hint so clients can group sessions by feature (e.g. 'script', 'storyboard').",
    )
    context_id: Optional[str] = Field(default=None, max_length=64)


class SessionOut(BaseModel):
    """Slim representation for the session list."""

    model_config = _COERCE_IDS

    # ai_sessions.id is BIGINT Snowflake (mig 231) — a numeric string, not a
    # UUID. Typing it UUID would 422 on every real session.
    id: str
    user_id: UUID
    agent_id: Optional[UUID] = None
    agent_slug: Optional[str] = None
    team_id: Optional[int] = None
    project_id: Optional[int] = None
    title: Optional[str] = None
    context_type: Optional[str] = None
    context_id: Optional[str] = None
    status: Optional[str] = None
    total_tokens: Optional[int] = 0
    message_count: Optional[int] = 0
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class SessionUpdate(BaseModel):
    """PATCH body — only title is user-editable today."""

    title: Optional[str] = Field(default=None, max_length=200)


# ─── Messages ───────────────────────────────────────────────────────────────


class MessageOut(BaseModel):
    """One row from ai_messages — persisted chat turn."""

    model_config = _COERCE_IDS

    # ai_messages.id is still UUID (its own PK), but session_id FKs
    # ai_sessions.id which became BIGINT Snowflake (mig 231) → numeric string.
    id: UUID
    session_id: str
    role: Literal["user", "assistant", "system"]
    content: str
    agent_id: Optional[UUID] = None
    prompt_tokens: Optional[int] = None
    completion_tokens: Optional[int] = None
    metadata_json: Optional[dict[str, Any]] = None
    created_at: Optional[datetime] = None


class SessionWithMessages(SessionOut):
    """Session detail view + full message history (newest last)."""

    messages: list[MessageOut] = Field(default_factory=list)


# ─── Chat request/response ──────────────────────────────────────────────────


class AttachmentRequest(BaseModel):
    """G2: One attachment in a chat turn. The chat service converts these
    to multimodal Attachment[] (image / video frames / PDF pages) before
    composing the user message."""

    kind: str = Field(..., description="image | video | pdf")
    """Source format. video → frames extracted via Q2; pdf → pages
    rendered via Q3; image → passed through as-is."""

    url: Optional[str] = Field(
        default=None,
        description="Either a public http(s) URL the model can fetch, or a "
        "path relative to the shared library (DOWNLOAD_PATH) — typically the "
        "`file_path` returned by /chat-attachments/upload. The chat service "
        "resolves the relative path under DOWNLOAD_PATH (mounted by both the "
        "gateway and worker containers) for video/pdf extraction and image "
        "inlining.",
    )

    data_url: Optional[str] = Field(
        default=None,
        description="Inline data URL (base64). For images only — video/pdf "
        "data URLs are too large to ship in JSON.",
    )

    alt_text: Optional[str] = Field(default=None)
    mime: Optional[str] = Field(default=None)

    # S4: @-reference resource fields
    resource_id: Optional[str] = Field(
        default=None,
        description="Resource UUID/Snowflake for kind='resource_ref'.",
    )
    name: Optional[str] = Field(
        default=None,
        description="Display name snapshot used by the ref resolver.",
    )
    scope: Optional[dict] = Field(
        default=None,
        description="Frontend scope hint only — backend re-checks access.",
    )


class ChatRequest(BaseModel):
    """POST /sessions/:id/chat body. Only the user's new message; history
    comes from the server-side ai_messages rows."""

    content: str = Field(..., min_length=1)
    # Phase M (M4): opt-in PlanMode. When set to 'prompt_user' the chat
    # service runs the model in plan-only mode for this turn — emits
    # a structured plan and waits for the user to reply approve/reject.
    # Default 'auto' = original behavior. 'dry_run' emits plan only,
    # never executes (good for what-if queries).
    plan_mode: Optional[str] = Field(
        default=None, description="auto / prompt_user / dry_run"
    )

    # G2: optional multi-modal attachments. Vision-capable models see
    # them as image parts; text-only models gracefully degrade to
    # placeholder text.
    attachments: list[AttachmentRequest] = Field(default_factory=list)


class ChatToolCall(BaseModel):
    """One Skill / Delegate dispatch made during this chat turn.

    Surfaced by the chat endpoint so the frontend can render sub-task
    cards inline ("→ summarize, 24s, ¢0.27, see result"). Order
    matches the LLM's emission order. Args + result are the raw payloads
    the agent runner saw, untruncated — frontend decides how to display.
    """

    name: str  # 'Skill' | 'Delegate'
    iteration: int  # 1-indexed loop tick within the turn
    args: dict[str, Any] = Field(default_factory=dict)
    result: dict[str, Any] = Field(default_factory=dict)


class ChatResponse(BaseModel):
    """Non-streaming chat response — the assistant's assistant message +
    usage for this turn + traced tool calls (for UI sub-task rendering)."""

    model_config = _COERCE_IDS

    message: MessageOut
    usage: dict[str, int] = Field(default_factory=dict)
    # agent_runs.id is BIGINT Snowflake (mig 232) → numeric string.
    run_id: str
    tool_calls: list[ChatToolCall] = Field(default_factory=list)
