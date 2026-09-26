"""Response models for the ``/ai`` settings-side routes that used to return
bare dicts (OpenAPI P5): provider-health report, capability health board,
module governance flags, and the public platform-model list.

Each model declares what the handler ALREADY sends; the wire test
``tests/api/test_ai_settings_wire.py`` compares HTTP bodies with
``jsonable_encoder`` of the dicts the handlers build.
"""

from __future__ import annotations

from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, Field

from app.schemas.nous_model import NousModelType
from app.schemas.wire import WireDatetime


class AiProviderHealthReportResponse(BaseModel):
    """``POST /ai/provider-health``."""

    ok: bool


AiCapabilityStatus = Literal[
    "ok",
    "no_key",
    "no_model",
    "not_vision",
    "not_configured",
    "probe_failing",
    "key_test_failed",
    "unknown_provider",
    "runtime_failing",
    "error",
]


class AiCapabilityHealthRow(BaseModel):
    """One row of the capability board (``app/services/ai/ai_health.py``).

    ``origin`` is absent on an agent-task row whose resolution crashed (the
    ``error`` row of the first loop); the system rows' error row carries
    ``""``. The runtime quartet (``task_type`` … ``last_error``) is present
    only on capabilities backed by a tracked workflow. The route uses
    ``response_model_exclude_unset`` so absent stays absent.
    """

    capability: str
    label: str
    agent_slug: str
    assigned: bool
    model: str
    provider: str
    needs_vision: bool
    status: AiCapabilityStatus
    hint: str
    origin: Optional[Literal["platform", "governance", "byok", "env", ""]] = None
    task_type: Optional[str] = None
    recent_runs: Optional[int] = None
    recent_failures: Optional[int] = None
    last_error: Optional[str] = None


class AiHealthResponse(BaseModel):
    """``GET /ai/health``."""

    capabilities: List[AiCapabilityHealthRow]


class AiGovernanceResponse(BaseModel):
    """``GET /ai/governance``: one ``user_allowed`` bool per governed module
    (``ALL_MODULES``), the Nous master switch, and per-module Nous flags.

    The module fields must equal ``ALL_MODULES`` — pinned by
    ``tests/api/test_ai_settings_wire.py`` so a new module cannot be dropped
    from the response by this model.
    """

    caption: bool
    chat: bool
    classification: bool
    embedding: bool
    summarization: bool
    topic_scorer: bool
    transcription: bool
    translation: bool
    visual_analysis: bool
    nous_enabled: bool
    nous_modules: Dict[str, bool]


class AiNousModelPublic(BaseModel):
    """An enabled platform model as a user may see it (no key, host or
    upstream provider; ``is_local`` is the one bit derived from the latter).

    ``id`` is the native BIGINT — a JSON number on the wire, as it always
    was. ``pricing_value`` is ``Numeric``; ``float`` emits the same JSON
    number ``jsonable_encoder`` did. ``last_tested_at`` is already an ISO
    string (the repository's ``_parity``).
    """

    id: int
    name: str
    display_name: str
    actual_model: str
    type: NousModelType
    pricing_type: Literal["per_hour", "per_request", "per_token"]
    pricing_value: float
    sort_order: int
    last_test_status: Optional[Literal["ok", "fail", "idle", "not_probed"]]
    last_tested_at: Optional[str]
    last_test_code: Optional[str]
    is_local: bool


# ─── platform provider view (spec 2026-09-25 §3.1 / §3.3) ─────────────────────

AiPlatformModelStatus = Literal["ok", "idle", "not_probed"]

#: ``nous.user_enabled`` as the platform view read it. ``unknown`` = the read
#: FAILED and the view listed rows as if on (a failed read is not "off").
AiPlatformGovernance = Literal["on", "off", "unknown"]


class AiPlatformModelEntry(BaseModel):
    """``platform_models[<name>]`` in ``GET/PUT /ai/settings``.

    The mapping from a platform row name to what a picker shows. No
    ``base_url`` / key / upstream provider (the 2026-08-14 leak tripwire);
    ``status`` never carries ``fail`` — failed rows are not in the list.
    """

    actual_model: str
    type: NousModelType
    status: AiPlatformModelStatus
    is_local: bool
    pricing_type: Literal["per_hour", "per_request", "per_token"]
    pricing_value: float
    context_window_tokens: Optional[int]
    generatable: bool = Field(
        description=(
            "Whether the row can generate an image/clip from a prompt — the same "
            "predicate the generation pickers use (services/generation/"
            "model_capabilities.generates_from_prompt). False for every "
            "non-image/video row and for upscale-only services."
        )
    )


class AiPlatformEngineState(BaseModel):
    """nous-engine reachability behind the platform list.

    ``reachable=False`` means the list could not be read (it is NOT "no
    models" — rows stay listed as ``not_probed``); ``stale=True`` means the
    list is a carried-over snapshot at most 10 minutes old.
    """

    reachable: bool
    stale: bool
    checked_at: Optional[WireDatetime]


class AiNousModelsResponse(BaseModel):
    """``GET /search/vectors/catalog``: platform embedding rows from the
    platform provider view's system computation (governance, engine state,
    ``fail`` rows dropped). ``last_test_status`` carries the live status
    (``ok`` / ``idle`` / ``not_probed``), the same value
    ``platform_models[name].status`` has on the AI settings."""

    models: List[AiNousModelPublic]
    engine: Optional[AiPlatformEngineState] = None
    governance: Optional[AiPlatformGovernance] = None


class AiPlatformProviderEntry(BaseModel):
    """``ai_providers.nous`` in ``GET/PUT /ai/settings`` — the same shape as a
    BYOK card (``models`` / ``enabled_models`` are catalog names), computed by
    the server on every read. Only ``enabled`` and ``disabled_models`` are
    stored; a PUT drops the rest."""

    enabled: bool
    managed: Literal[True] = True
    models: List[str]
    enabled_models: List[str]
    disabled_models: List[str]


class AiPlatformModelRuntime(BaseModel):
    """One row of ``GET /ai/platform-status``.

    ``local_ready`` is ``None`` for rows that do not run on the user's own
    machine; ``superseded`` marks a server twin hidden because its local twin
    can run (``local_readiness.local_verdict``; the generation pickers hide
    both kinds client side).
    """

    status: AiPlatformModelStatus
    local_ready: Optional[bool]
    superseded: bool


class AiPlatformStatusResponse(BaseModel):
    """``GET /ai/platform-status``."""

    models: Dict[str, AiPlatformModelRuntime]
    engine: Optional[AiPlatformEngineState]
    governance: Optional[AiPlatformGovernance] = None
