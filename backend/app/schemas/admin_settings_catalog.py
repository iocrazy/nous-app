"""Response models for the admin settings / alerts / table-preferences /
nous-models / agent-catalog writes (OpenAPI typing, group D1).

Each model declares exactly the keys the handler already built as a bare
dict; ``tests/api/admin/test_admin_settings_catalog_wire.py`` pins every one
against ``jsonable_encoder`` of that dict.

- The agent-catalog rows come from ``agent_repository._agent_to_dict``, which
  already turned uuids into ``str`` and timestamps into ISO strings, so those
  are declared ``str``. ``Numeric`` columns stay native ``Decimal`` there and
  are declared :data:`AdminWireNumeric`, which writes the same JSON number
  ``jsonable_encoder`` did (``Decimal("1")`` → ``1``, ``Decimal("0.70")`` →
  ``0.7``); a plain ``float`` would turn the first into ``1.0``.
- Every catalog column is declared nullable: these rows are hand-editable
  from the admin page and seeded from files, and a response model must never
  turn an odd row into a 500.

Spec: docs/superpowers/specs/2026-09-24-openapi-typed-frontend-design.md
"""

from __future__ import annotations

from decimal import Decimal
from typing import Annotated, Any

from fastapi.encoders import decimal_encoder
from pydantic import BaseModel, PlainSerializer, WithJsonSchema

AdminWireNumeric = Annotated[
    Decimal,
    PlainSerializer(decimal_encoder, return_type=Any, when_used="json"),
    WithJsonSchema({"type": "number"}),
]
"""A ``Numeric`` column value serialized exactly as ``jsonable_encoder`` does."""


# --------------------------------------------------------------------------- #
# /admin/settings
# --------------------------------------------------------------------------- #


class AdminSecretsSelfhealSummary(BaseModel):
    """``POST /admin/settings/encrypt-secrets``: rows rewritten per store.

    Counts only — the sweep never puts a secret (plain or encrypted) into its
    summary. A skipped run carries ``ok=false`` + ``reason`` and none of the
    counters; the route drops unset keys so those stay absent on the wire.
    """

    ok: bool
    reason: str | None = None
    errors: int | None = None
    system_settings: int | None = None
    platform_ai_providers: int | None = None
    nous_models: int | None = None
    user_mcp_servers: int | None = None
    user_settings_ai_providers: int | None = None


class AdminMemoryPromotionApproveResult(BaseModel):
    """``approved=false``: the proposal was not pending, the owner has left
    the target team (it stays pending), or the write failed."""

    approved: bool


class AdminMemoryPromotionRejectResult(BaseModel):
    """``rejected=false``: no pending proposal with that id, or the write
    failed."""

    rejected: bool


class AdminMemoryDemoteResult(BaseModel):
    """``demoted=false``: no memory row with that id, or the write failed."""

    demoted: bool


# --------------------------------------------------------------------------- #
# /admin/alerts
# --------------------------------------------------------------------------- #


class AdminAlertOkResult(BaseModel):
    """``{"ok": true}`` from delete / unmute / resolve."""

    ok: bool


class AdminAlertMuteResult(BaseModel):
    ok: bool
    mute_until: str


# --------------------------------------------------------------------------- #
# /admin/table-preferences
# --------------------------------------------------------------------------- #


class AdminTablePreferenceResetResult(BaseModel):
    ok: bool


# --------------------------------------------------------------------------- #
# /admin/nous-models
# --------------------------------------------------------------------------- #


class AdminNousModelDeleteResult(BaseModel):
    message: str


# --------------------------------------------------------------------------- #
# /admin/agents (system-agent catalog)
# --------------------------------------------------------------------------- #


class AdminAgentOverrideCounts(BaseModel):
    """How many users / teams customized a preset (``agent_overrides``)."""

    user: int
    team: int


class AdminCatalogAgentItem(BaseModel):
    """One ``GET /admin/agents`` row: a fixed projection of the preset."""

    id: str | None
    slug: str | None
    name: str | None
    description: str | None
    icon: str | None
    model: str | None
    temperature: AdminWireNumeric | None
    max_tokens: int | None
    identity_md: str | None
    soul_md: str | None
    agent_md: str | None
    fallback_models: list[str] | None
    enabled: bool | None
    updated_at: str | None
    current_version: int | None
    override_counts: AdminAgentOverrideCounts


class AdminCatalogAgentList(BaseModel):
    items: list[AdminCatalogAgentItem]
    total: int


class AdminCatalogAgentDetail(BaseModel):
    """``PUT /admin/agents/{slug}``: the whole refreshed ``ai_agents`` row
    (``SELECT *`` shape) plus its override counts.

    ``team_id`` / ``project_id`` are Snowflake BIGINTs sent as JSON numbers.
    """

    id: str | None
    name: str | None
    current_version: int | None
    fallback_models: list[str] | None
    persistent: bool | None
    description: str | None
    model: str | None
    temperature: AdminWireNumeric | None
    max_tokens: int | None
    team_id: int | None
    project_id: int | None
    created_by: str | None
    enabled: bool | None
    sort_order: int | None
    created_at: str | None
    updated_at: str | None
    slug: str | None
    identity_md: str | None
    soul_md: str | None
    agent_md: str | None
    is_system_preset: bool | None
    capability_profile: Any
    user_id: str | None
    icon: str | None
    monthly_token_budget: int | None
    monthly_cost_cents_budget: AdminWireNumeric | None
    paused_reason: str | None
    budget_per_run_cents: AdminWireNumeric | None
    memory_injection_top_n: int | None
    seed_hash: str | None
    timeout_sec: int | None
    max_concurrent_runs: int | None
    agent_group: str | None
    deleted_at: str | None
    override_counts: AdminAgentOverrideCounts
