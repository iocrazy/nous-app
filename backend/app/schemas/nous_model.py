# backend/app/schemas/nous_model.py

"""Pydantic schemas for Nous models API."""

from typing import Any, List, Literal, Optional

from pydantic import BaseModel, Field, model_validator

# Must stay in lockstep with the table's CHECK constraint (migration 345).
# It stopped at "asr" from 345 until 2026-09-21 while the database accepted
# image and video, so the API rejected two types it was already storing —
# every image/video row was inserted by a migration and could not afterwards
# be edited through the admin dialog (which echoes `type` back in the PATCH).
# Guarded by tests/test_nous_model_type_vocabulary.py and, against the
# real constraint, tests/db/test_nous_model_type_matches_db_check.py.
NousModelType = Literal["llm", "embedding", "tts", "asr", "image", "video"]

# context_window_tokens is an int4 column: a larger value fails the UPDATE,
# which the repository swallows into None (→ a misleading 404).
INT4_MAX = 2_147_483_647


class NousModelCreate(BaseModel):
    """Request body for creating a Nous model."""

    name: str
    display_name: str
    type: NousModelType
    description: Optional[str] = None
    actual_provider: str
    actual_model: str
    # Blank → inherit from an existing model on the same provider+base_url
    # (provider-card UX: enter the key once, add more models without re-typing).
    api_key: str = ""
    app_id: Optional[str] = None
    base_url: Optional[str] = None
    pricing_type: Literal["per_hour", "per_request", "per_token"] = "per_hour"
    pricing_value: float = 8
    is_enabled: bool = True
    sort_order: int = 0
    # Context window in tokens (migration 500); None = unknown.
    context_window_tokens: Optional[int] = Field(default=None, gt=0, le=INT4_MAX)


class NousModelUpdate(BaseModel):
    """Request body for updating a Nous model (all fields optional)."""

    name: Optional[str] = None
    display_name: Optional[str] = None
    type: Optional[NousModelType] = None
    description: Optional[str] = None
    actual_provider: Optional[str] = None
    actual_model: Optional[str] = None
    api_key: Optional[str] = None
    app_id: Optional[str] = None
    base_url: Optional[str] = None
    pricing_type: Optional[Literal["per_hour", "per_request", "per_token"]] = None
    pricing_value: Optional[float] = None
    is_enabled: Optional[bool] = None
    sort_order: Optional[int] = None
    # Omitted = unchanged (the router drops None fields). To withdraw a value
    # and fall back to the builtin table / default, send clear_context_window.
    context_window_tokens: Optional[int] = Field(default=None, gt=0, le=INT4_MAX)
    # The only way to write NULL through the exclude_none patch (same trick as
    # issues ``clear_budget``). Mutually exclusive with context_window_tokens.
    clear_context_window: bool = False

    @model_validator(mode="after")
    def _window_value_xor_clear(self) -> "NousModelUpdate":
        if self.clear_context_window and self.context_window_tokens is not None:
            raise ValueError(
                "Send either context_window_tokens or clear_context_window, not both"
            )
        return self


class NousModelResponse(BaseModel):
    """Admin response — api_key masked."""

    id: str
    name: str
    display_name: str
    type: str
    description: Optional[str] = None
    actual_provider: str
    actual_model: str
    api_key_masked: str
    app_id: Optional[str] = None
    base_url: Optional[str] = None
    pricing_type: str
    pricing_value: float
    is_enabled: bool
    sort_order: int
    # Migration 431: a non-NULL owner makes this row ONE user's — RLS and
    # list_enabled(viewer_user_id=…) hide it from everyone else. Projected for
    # the admin page so a private row and a platform-wide row stop rendering as
    # two identical cards (2026-09-21: codex-image vs codex-local-image were
    # exactly that pair). Admin-only surface; never in _PUBLIC_COLS.
    owner_user_id: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    # Last connectivity-test result (persisted; drives the status dot + the
    # "last tested" hint across navigation). NULL = never tested.
    last_test_status: Optional[str] = None
    last_test_detail: Optional[str] = None
    last_tested_at: Optional[str] = None
    # Context window in tokens (migration 500); None = unknown.
    context_window_tokens: Optional[int] = None
    # Does RunRecorder have an ai_model_prices row to snapshot for this model?
    # priced / missing / not_applicable / unknown (see model_pricing_coverage).
    # Independent of the probe status: reachable and unpriced both happen.
    # Only the list endpoint fills it; single-row responses leave None.
    price_coverage: Optional[str] = None
    # nous-engine rows only (actual_provider='nous'), list endpoint only; None
    # for every other row. Read live from the engine's own list with the row's
    # credential (spec 2026-09-25 §3.5) — the status dot reads these instead of
    # last_test_status for such rows:
    #   listed       the engine lists the service for this key
    #   missing      the engine answered and does not list it (grant revoked
    #                or service removed) — the admin decides; nothing is
    #                disabled automatically
    #   unreachable  no usable list (down, timeout, key refused)
    engine_status: Optional[Literal["listed", "missing", "unreachable"]] = None
    # listed rows only: is the service loaded right now. None otherwise.
    engine_ready: Optional[bool] = None


class NousModelPublic(BaseModel):
    """Public response — no API key or provider details."""

    name: str
    display_name: str
    type: str
    description: Optional[str] = None
    pricing_type: str
    pricing_value: float


class NousModelProbeRequest(BaseModel):
    """Admin 'Test & Load Models' request.

    ``name`` is the existing model being edited (optional): when ``api_key`` is
    left blank in the edit form (the stored key is never sent to the client),
    the backend falls back to that model's stored key so probing works without
    re-typing the key.
    """

    provider_key: str
    api_key: Optional[str] = ""
    app_id: Optional[str] = ""
    base_url: Optional[str] = ""
    model: Optional[str] = ""
    name: Optional[str] = None


class NousModelTestResponse(BaseModel):
    """Result of a real per-model connectivity probe (chat / embedding / asr)."""

    ok: bool
    # True when the probe has no protocol for this model's type and therefore
    # checked nothing (image / video / tts). ``ok`` stays False — nothing
    # succeeded — so this is the only signal that separates "didn't check" from
    # "checked and failed", and the admin page needs it to render a neutral
    # badge instead of a red one.
    not_probed: bool = False
    # True when a local nous-engine model is authorized but not loaded right
    # now (engine readiness 503). Only the hourly poll's passive read produces
    # it; the admin Test does a real call, which loads the model instead.
    idle: bool = False
    detail: str = ""
    error: Optional[str] = None
    dims: Optional[int] = None
    # ISO timestamp the result was persisted at (lets the UI show "tested just
    # now" without re-fetching the whole list).
    tested_at: Optional[str] = None


class ProviderProtocolItem(BaseModel):
    """One provider protocol for the admin dropdown."""

    key: str
    label: str
    description: str
    model_types: List[str]
    aliases: List[str]
    is_default: bool
    # "api_key" | "server_session" | "user_device" — whose credential runs
    # this, and so whose machine and whose quota. The only thing separating
    # the three gpt-image cards from each other in the admin UI; see
    # ProviderProtocol.credential_kind.
    credential_kind: str


class ProviderProtocolListResponse(BaseModel):
    protocols: List[ProviderProtocolItem]


class CardLabelsResponse(BaseModel):
    """Admin-chosen provider card names, keyed ``"<provider>|<base_url>"``."""

    labels: dict[str, str]


class CardLabelsUpdate(BaseModel):
    """Partial update: listed cards are renamed, a blank name removes one.

    ``Any`` values on purpose — the shape check lives in
    ``settings_validation`` so the generic settings PATCH and this endpoint
    reject the same things with the same message."""

    labels: dict[str, Any] = Field(default_factory=dict)


class NousEngineSyncSkipped(BaseModel):
    """One engine service the sync did not write, and why (e.g.
    ``unsupported_type:app``, ``name_taken``, ``no_credential_source``)."""

    id: str
    reason: str


class NousEngineSyncResponse(BaseModel):
    """POST /admin/nous-models/sync-engine — merged over every engine endpoint.

    ``created`` / ``updated`` / ``disabled`` are catalog names. The engine list
    is read with ``include_unready=1`` and holds every AUTHORIZED service,
    loaded or not, so an enabled platform row whose service is missing had its
    grant revoked and is in ``disabled``. ``unauthorized`` means an endpoint
    answered 401 (the key itself was revoked) and all its enabled platform rows
    were disabled. Disabled rows are never re-enabled by a sync.
    ``ready_changed`` counts rows whose ok/idle status followed the engine's
    ``ready``. ``error`` is set when at least one endpoint's ``/v1/models``
    could not be read (the others still synced).
    """

    discovered: int
    created: List[str]
    updated: List[str]
    skipped: List[NousEngineSyncSkipped]
    disabled: List[str]
    ready_changed: int
    unauthorized: bool
    error: Optional[str] = None
