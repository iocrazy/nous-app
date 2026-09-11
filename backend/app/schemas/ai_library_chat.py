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

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

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

    # ai_messages.id: historically a real UUID PK under the retired
    # Supabase-backed store; under ConversationsAiStore (Task 4, the sole
    # store since P3 Task 6) it's public.messages.id, a BIGINT Snowflake.
    # Widened to str + an explicit `mode="before"` coercion (mirrors
    # schemas/conversation.py::MessageOut._coerce_bigint_str) so both shapes
    # validate without ever losing BIGINT precision through a UUID cast —
    # kept flexible since pre-collapse rows may still be read until the
    # Wave 2 migration drops the legacy tables.
    id: str
    session_id: str
    role: Literal["user", "assistant", "system"]
    content: str
    agent_id: Optional[UUID] = None
    prompt_tokens: Optional[int] = None
    completion_tokens: Optional[int] = None
    metadata_json: Optional[dict[str, Any]] = None
    # Attachment display metadata persisted with user turns (kind /
    # resource_id / mime / alt_text) — lets the UI re-render image chips
    # in the bubble after a history reload. None for assistant rows.
    attachments: Optional[list[dict[str, Any]]] = None
    created_at: Optional[datetime] = None

    @field_validator("id", mode="before")
    @classmethod
    def _coerce_id_str(cls, v: Any) -> str:
        return str(v)


class SessionWithMessages(SessionOut):
    """Session detail view + full message history (newest last)."""

    messages: list[MessageOut] = Field(default_factory=list)


# ─── Chat request/response ──────────────────────────────────────────────────


class AttachmentRequest(BaseModel):
    """G2: One attachment in a chat turn. The chat service converts these
    to multimodal Attachment[] (image / video frames / PDF pages) before
    composing the user message."""

    kind: str = Field(..., description="image | video | pdf | resource_ref | asset_ref")
    """Source format. video → frames extracted via Q2; pdf → pages
    rendered via Q3; image → passed through as-is.

    Two kinds carry no bytes and name a library row instead: ``resource_ref``
    resolves ``resource_id`` through ``resource_ref_resolver`` (S4), and
    ``asset_ref`` resolves ``asset_id`` (+ optional ``loadout_id``) through
    ``asset_ref_resolver`` (P5). Both are listed in ``<available_resources>``
    and loaded only if the model calls ``ResourceFetch``.

    The field stays a free string rather than an enum: unknown kinds fall
    through to the binary path, which reports ``unsupported attachment kind``
    as a typed failure the user sees. Narrowing it to a Literal would turn the
    same input into a 422 from FastAPI with no per-attachment reason.
    """

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

    # P5: @-reference asset fields (kind='asset_ref')
    asset_id: Optional[str] = Field(
        default=None,
        description="assets.id Snowflake for kind='asset_ref'.",
    )
    loadout_id: Optional[str] = Field(
        default=None,
        description="Optional asset_loadouts.id for kind='asset_ref'. Null "
        "(the only value v1 clients send) means the asset's default loadout.",
    )

    # 三期 3a Task 4: @-reference a registered OUTPUT version (kind='output_ref')
    ref_kind: Optional[str] = Field(
        default=None,
        description="Deliverable kind for kind='output_ref' — one of "
        "generated_media / script_shot / script_scene / script_chapter.",
    )
    ref_id: Optional[str] = Field(
        default=None,
        description="The cited object's id for kind='output_ref'.",
    )
    version: Optional[int] = Field(
        default=None,
        description="Which version of that object is cited (1-based). A "
        "citation names one specific version, so this is required for "
        "kind='output_ref'.",
    )
    title: Optional[str] = Field(
        default=None,
        description="Display title for kind='output_ref'. Client-supplied "
        "values are IGNORED and overwritten with the registry row's title at "
        "post time — the thread must render what was actually registered.",
    )

    @field_validator("resource_id", "asset_id", "loadout_id", "ref_id", mode="before")
    @classmethod
    def _coerce_ref_id_str(cls, v: Any) -> Any:
        """Accept a Snowflake sent as a JSON number.

        All three are BIGINT Snowflakes modelled as ``str`` because a JS number
        loses the low bits above 2^53. Our own clients send strings — the assets
        and resources routers ``str()`` every id on the way out — but pydantic
        v2's lax mode does NOT coerce int→str, so a hand-built client, a script,
        or any future caller that forgets would 422 the ENTIRE ChatRequest with
        `string_type`. That is the same failure this model avoids for ``kind``
        by leaving it a free string: a whole-request rejection says nothing
        about which attachment was wrong, where a coerced id resolves normally
        and a genuinely bad one comes back as a per-attachment typed failure.

        Deliberately narrow. ``bool`` is an ``int`` subclass and would become
        ``"True"``; a float would become ``"7001.0"``. Neither is an id, so both
        fall through to pydantic and are rejected. This is also why the file's
        model-wide ``_COERCE_IDS`` config is not used here — it would coerce
        every str field on the model, silently turning ``kind: 5`` into the
        string ``"5"``.

        Downstream, ``asset_ref_resolver.coerce_asset_id`` normalizes again for
        callers that never cross this boundary (internal invocations, tests).
        Both must agree, which is why neither one is allowed to be the only one.
        """
        if isinstance(v, int) and not isinstance(v, bool):
            return str(v)
        return v


class ScriptContextRequest(BaseModel):
    """§5.3：随消息携带的剧本选区 handle。文本折叠仍在 content 里（展示/
    持久化不变）；这里只携带 id，让 agent 能用 ReadScene/ProposeEdit 精确
    定位，而不是拿文本再搜一遍。id 全部字符串（BIGINT snowflake 精度）。"""

    scene_id: Optional[str] = None
    element_ids: list[str] = Field(default_factory=list)
    element_type: Optional[str] = None
    scene_label: Optional[str] = None
    cross_scene: bool = False


class ChatRequest(BaseModel):
    """POST /sessions/:id/chat body. Only the user's new message; history
    comes from the server-side ai_messages rows."""

    # NOT ``min_length=1``. A turn may legitimately carry no text at all —
    # "here, look at this" with a library asset or an image attached and
    # nothing typed. That used to be impossible by accident: library assets
    # were inline editor nodes whose text rendering ("@pitch.mp4") made
    # content non-empty, so the length floor never fired on a real turn.
    # Once assets moved to the attachment row, the floor started rejecting
    # the feature's main path with a 422 raised BEFORE the endpoint body,
    # where the attachments are never even looked at.
    # The real rule is "a turn must carry something", enforced below.
    content: str = ""
    # Phase 2a: this message answers the typed question parked on the latest
    # assistant message (metadata_json.awaiting_input.question_id). The body
    # must equal one of its labels or be free text when allowed — otherwise
    # 400 answer_shape / 409 no_open_question. Sending a plain message
    # without answer_to while a question is open supersedes the question.
    answer_to: Optional[str] = Field(default=None, max_length=120)

    # §5.3: structured selection handle (scene_id / element_ids) alongside
    # the folded-text content — lets the agent target the exact scene/
    # element instead of re-locating it by text search. None when the
    # user didn't have a context capsule active.
    script_context: Optional["ScriptContextRequest"] = None
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
    #
    # No `max_length` on purpose. The caps live where the cost is known:
    # `chat_attachment_resolver.MAX_ATTACHMENTS_PER_TURN` bounds bytes fetched,
    # `ai_library_chat_service.MAX_ASSET_REF_ATTACHMENTS` bounds database round
    # trips (final review I2). `resource_ref` needs neither — any number of them
    # is one batched query. A single length here would reject the whole request
    # with a 422 that names no attachment, where the service answers the turn
    # and reports each refused entry as a typed `attachment_limit_exceeded`
    # failure against its own index.
    attachments: list[AttachmentRequest] = Field(default_factory=list)

    @model_validator(mode="after")
    def _require_text_or_attachment(self) -> "ChatRequest":
        """A turn must carry something the agent can act on.

        Whitespace-only text counts as nothing — the previous
        ``min_length=1`` accepted a lone space, so this is stricter there
        and looser where it matters (attachments now speak for themselves).
        """
        if not self.content.strip() and not self.attachments:
            raise ValueError("content or attachments required")
        return self


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
