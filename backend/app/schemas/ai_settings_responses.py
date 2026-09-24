"""Response models for the ``/ai`` settings-side routes that used to return
bare dicts (OpenAPI P5): provider-health report, capability health board,
module governance flags, and the public platform-model list.

Each model declares what the handler ALREADY sends; the wire test
``tests/api/test_ai_settings_wire.py`` compares HTTP bodies with
``jsonable_encoder`` of the dicts the handlers build.
"""

from __future__ import annotations

from typing import Dict, List, Literal, Optional

from pydantic import BaseModel

from app.schemas.nous_model import NousModelType


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


class AiNousModelsResponse(BaseModel):
    """``GET /ai/nous-models``."""

    models: List[AiNousModelPublic]
