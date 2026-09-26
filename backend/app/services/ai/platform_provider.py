# backend/app/services/ai/platform_provider.py

"""The platform (Nous) provider card, computed once per read (spec 2026-09-25 §3.1).

A BYOK card is a saved object every consumer maps from. The platform card
used to be five separate requests, each with its own filter, over a catalog
that background jobs kept rewriting. Here it becomes one computation:

1. enabled catalog rows the viewer may see (owner-scoped rows included);
2. admin governance ``nous.user_enabled`` off → no models;
3. ``actual_provider='nous'`` rows get their status from the engine's own
   list (``engine_catalog``), read with the row's own credential:
   listed → ``ok`` / ``idle`` by ``ready``; explicitly not listed → dropped
   (grant revoked or service gone — nothing is written or disabled);
   no usable list → kept as ``not_probed`` (could not reach ≠ revoked);
4. every other row keeps the status the hourly probe stored;
5. ``fail`` rows are dropped;
6. ``enabled_models`` = models − the user's ``disabled_models`` blacklist.

``live_platform_rows`` (steps 1, 3–5, no user gates) also feeds the implicit
default pick; ``platform_status`` adds the user's local daemon readiness.
Nothing here writes to the catalog.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Iterable, Mapping, Optional, Sequence

from loguru import logger

from app.services.ai.engine_catalog import (
    NOUS_ENGINE_PROVIDER,
    EngineSnapshot,
    engine_credential,
    snapshots_for,
)

_FAILED_STATUSES = frozenset({"fail", "failed", "error"})
_STORED_LIVE_STATUSES = frozenset({"ok", "idle", "not_probed"})


@dataclass(frozen=True)
class PlatformModel:
    """One row of the platform list. ``actual_provider`` is PRIVATE: derive
    from it, never serialize it (the 2026-08-14 leak tripwire)."""

    id: int
    name: str
    display_name: str
    actual_model: str
    type: str
    status: str  # ok | idle | not_probed
    is_local: bool
    actual_provider: str
    pricing_type: str
    pricing_value: float
    context_window_tokens: Optional[int]
    generatable: bool
    sort_order: int
    last_tested_at: Optional[str]
    last_test_code: Optional[str]

    def public_row(self) -> dict[str, Any]:
        """Public row shape (``AiNousModelPublic`` fields) for
        ``platform_rows_with_status``; ``last_test_status`` carries the
        computed status."""
        return {
            "id": self.id,
            "name": self.name,
            "display_name": self.display_name,
            "actual_model": self.actual_model,
            "type": self.type,
            "pricing_type": self.pricing_type,
            "pricing_value": self.pricing_value,
            "sort_order": self.sort_order,
            "last_test_status": self.status,
            "last_tested_at": self.last_tested_at,
            "last_test_code": self.last_test_code,
            "is_local": self.is_local,
        }

    def mapping_entry(self) -> dict[str, Any]:
        """``platform_models[name]`` (``AiPlatformModelEntry``)."""
        return {
            "actual_model": self.actual_model,
            "type": self.type,
            "status": self.status,
            "is_local": self.is_local,
            "pricing_type": self.pricing_type,
            "pricing_value": self.pricing_value,
            "context_window_tokens": self.context_window_tokens,
            "generatable": self.generatable,
        }


@dataclass(frozen=True)
class PlatformEngine:
    reachable: bool
    stale: bool
    checked_at: Optional[datetime]

    def as_dict(self) -> dict[str, Any]:
        return {
            "reachable": self.reachable,
            "stale": self.stale,
            "checked_at": self.checked_at,
        }


@dataclass(frozen=True)
class PlatformProviderView:
    enabled: bool
    models: tuple[PlatformModel, ...]
    enabled_models: tuple[str, ...]
    disabled_models: tuple[str, ...]
    engine: Optional[PlatformEngine]

    def provider_entry(self) -> dict[str, Any]:
        """``ai_providers.nous`` (``AiPlatformProviderEntry``)."""
        return {
            "enabled": self.enabled,
            "managed": True,
            "models": [m.name for m in self.models],
            "enabled_models": list(self.enabled_models),
            "disabled_models": list(self.disabled_models),
        }

    def platform_models(self) -> dict[str, dict[str, Any]]:
        return {m.name: m.mapping_entry() for m in self.models}


def _row_status(
    row: Mapping[str, Any], snapshot: Optional[EngineSnapshot]
) -> Optional[str]:
    """Status of one row, or ``None`` when it must not be listed."""
    if snapshot is None:
        stored = row.get("last_test_status")
        if stored in _FAILED_STATUSES:
            return None
        return stored if stored in _STORED_LIVE_STATUSES else "not_probed"
    model = str(row.get("actual_model") or "")
    listed = snapshot.lists(model)
    if listed is None:
        return "not_probed"
    if listed is False:
        return None
    service = snapshot.service(model)
    return "ok" if service is not None and service.ready else "idle"


def _to_model(
    row: Mapping[str, Any], status: str, snapshot: Optional[EngineSnapshot]
) -> PlatformModel:
    from app.repositories.nous_model_repository import LOCAL_ENGINE_PROVIDERS
    from app.services.generation.model_capabilities import generates_from_prompt

    provider = str(row.get("actual_provider") or "")
    tested_at = row.get("last_tested_at")
    code = row.get("last_test_code")
    if snapshot is not None:
        # Live status: the stored probe time/code describe an older answer.
        tested_at = snapshot.fetched_at.isoformat() if snapshot.fetched_at else None
        code = None
    return PlatformModel(
        id=row["id"],
        name=row["name"],
        display_name=row.get("display_name") or row["name"],
        actual_model=str(row.get("actual_model") or ""),
        type=str(row.get("type") or ""),
        status=status,
        is_local=provider in LOCAL_ENGINE_PROVIDERS,
        actual_provider=provider,
        pricing_type=str(row.get("pricing_type") or "per_token"),
        pricing_value=float(row.get("pricing_value") or 0),
        context_window_tokens=row.get("context_window_tokens"),
        # Same predicate the generation pickers' server row set uses; derived
        # from the private provider, published as a plain bool.
        generatable=generates_from_prompt(row.get("type"), provider),
        sort_order=int(row.get("sort_order") or 0),
        last_tested_at=str(tested_at) if tested_at else None,
        last_test_code=code,
    )


def _engine_state(snapshots: Iterable[EngineSnapshot]) -> Optional[PlatformEngine]:
    snaps = list({id(s): s for s in snapshots}.values())
    if not snaps:
        return None
    times = [s.fetched_at for s in snaps if s.fetched_at is not None]
    return PlatformEngine(
        reachable=all(s.reachable for s in snaps),
        stale=any(s.stale for s in snaps),
        checked_at=min(times) if times else None,
    )


def generation_picker_models(view: PlatformProviderView) -> tuple[PlatformModel, ...]:
    """The rows a generation picker may list, server side (spec §3.8).

    Twin of the frontend mapping (``platformModelRows`` over
    ``enabled_models`` + ``useGenerationModels``' ``generatable`` gate), minus
    the per-moment daemon overlay the client applies from
    ``GET /ai/platform-status``. ``generation-capabilities`` keys its answer by
    exactly these rows, so every model a picker offers has a capability entry.
    """
    if not view.enabled:
        return ()
    enabled = set(view.enabled_models)
    return tuple(m for m in view.models if m.generatable and m.name in enabled)


async def overlay_live_status(
    rows: Sequence[Mapping[str, Any]],
) -> tuple[list[PlatformModel], Optional[PlatformEngine]]:
    """Steps 3–5 over full catalog rows (api_key revealed). Pure apart from
    the engine read; each distinct engine credential is read once."""
    credentials = {
        r["name"]: engine_credential(r)
        for r in rows
        if r.get("actual_provider") == NOUS_ENGINE_PROVIDER
    }
    snapshots = await snapshots_for(credentials)
    models: list[PlatformModel] = []
    for row in rows:
        snap = snapshots.get(row["name"])
        status = _row_status(row, snap)
        if status is not None:
            models.append(_to_model(row, status, snap))
    return models, _engine_state(snapshots.values())


async def live_platform_rows(
    viewer_user_id: Optional[str] = None,
) -> tuple[list[PlatformModel], Optional[PlatformEngine]]:
    """Enabled catalog rows with live status, before any user gate. Raises
    when the catalog cannot be read."""
    from app.repositories.nous_model_repository import get_nous_model_repository

    rows = await get_nous_model_repository().list_enabled_private(viewer_user_id)
    return await overlay_live_status(rows)


async def platform_rows_with_status(
    type_filter: Optional[str] = None,
) -> list[dict[str, Any]]:
    """Platform-wide rows (no owner rows) as public dicts plus ``status``, for
    callers picking an implicit default (``default_model_pick``). Degrades to
    ``[]`` with an ERROR log when the catalog cannot be read."""
    try:
        models, _ = await live_platform_rows(None)
    except Exception as exc:  # noqa: BLE001 — callers fall back to their default
        logger.error(f"[platform_provider] catalog read failed: {exc!r}")
        return []
    return [
        {**m.public_row(), "status": m.status}
        for m in models
        if type_filter is None or m.type == type_filter
    ]


async def platform_provider_view(
    user_id: str, *, stored_nous: Optional[Mapping[str, Any]] = None
) -> PlatformProviderView:
    """The platform card for ``user_id`` (§3.1). ``stored_nous`` is the
    caller's already-loaded ``ai_providers.nous`` entry, if any.

    ``enabled`` is the STORED user switch, not ANDed with governance: the
    card echoes it back on save, and folding governance in would persist a
    switch the user never turned off. Governance shows as an empty list.
    """
    from app.services.ai.governance.ai_governance import is_nous_globally_enabled
    from app.services.ai.platform_model_visibility import (
        blacklisted,
        disabled_names,
        stored_nous_settings,
    )

    nous = (
        dict(stored_nous)
        if stored_nous is not None
        else await stored_nous_settings(user_id)
    )
    enabled = nous.get("enabled") is not False
    disabled = disabled_names(nous)
    if not await is_nous_globally_enabled():
        return PlatformProviderView(enabled, (), (), disabled, None)
    models, engine = await live_platform_rows(user_id)
    named = [{"name": m.name} for m in models]
    hidden = blacklisted(named, disabled)
    enabled_models = tuple(
        m.name for m, row in zip(models, named) if id(row) not in hidden
    )
    return PlatformProviderView(
        enabled=enabled,
        models=tuple(models),
        enabled_models=enabled_models,
        disabled_models=disabled,
        engine=engine,
    )


async def platform_status(user_id: str) -> dict[str, Any]:
    """``GET /ai/platform-status`` (§3.3): the view's statuses plus the
    user's own-machine readiness for local rows, same rule the generation
    picker applies (``local_readiness.local_verdict``)."""
    from app.services.generation.local_readiness import (
        SERVER_TWIN_OF,
        LocalReadiness,
        local_engine_readiness,
        local_verdict,
    )

    view = await platform_provider_view(user_id)
    needs_daemon = any(
        m.is_local or m.actual_provider.lower() in SERVER_TWIN_OF for m in view.models
    )
    readiness = (
        await local_engine_readiness(user_id) if needs_daemon else LocalReadiness()
    )
    models: dict[str, dict[str, Any]] = {}
    for m in view.models:
        verdict = local_verdict(m.actual_provider, readiness)
        models[m.name] = {
            "status": m.status,
            "local_ready": verdict.local_ready,
            "superseded": verdict.superseded,
        }
    return {
        "models": models,
        "engine": view.engine.as_dict() if view.engine else None,
    }


__all__ = [
    "PlatformEngine",
    "PlatformModel",
    "PlatformProviderView",
    "generation_picker_models",
    "live_platform_rows",
    "overlay_live_status",
    "platform_provider_view",
    "platform_rows_with_status",
    "platform_status",
]
