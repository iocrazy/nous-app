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
4. every other row keeps the status the hourly probe stored (the probe
   records nous-engine rows as ``not_probed``; their stored value is unused);
5. ``fail`` rows are dropped;
6. ``enabled_models`` = models − the user's ``disabled_models`` blacklist.

Every backend decision over platform models reads the SAME computation:
``platform_provider_view`` for the settings card, ``platform_rows`` for
dispatch, implicit default picks, the scorer pool, the vector catalog and the
asset bundle (spec 2026-09-25 P4 "全部统一"). Nothing else reads the
``nous_models`` table to decide what a user may see or use.
``platform_status`` adds the user's local daemon readiness. Nothing here
writes to the catalog.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Iterable, Literal, Mapping, Optional, Sequence

from loguru import logger

from app.core.exceptions import AppError
from app.services.ai.engine_catalog import (
    NOUS_ENGINE_PROVIDER,
    EngineSnapshot,
    engine_credential,
    snapshots_for,
)

_FAILED_STATUSES = frozenset({"fail", "failed", "error"})
_STORED_LIVE_STATUSES = frozenset({"ok", "idle", "not_probed"})
_GENERATION_TYPES = frozenset({"image", "video"})

PLATFORM_MODEL_NOT_AVAILABLE = "platform_model_not_available"

#: Who is asking, which decides what the rows may contain and how a failed
#: read surfaces (see :func:`platform_rows`).
PlatformRowsPurpose = Literal["picker", "dispatch", "system"]


class PlatformModelNotAvailableError(AppError, RuntimeError):
    """The named platform model exists but this user may not use it: another
    user's owner-scoped row, the user's platform card switched off, or the
    model on the user's blacklist.

    Same pattern as ``EngineServiceUnavailableError``: an :class:`AppError`
    so an uncaught one reaches the client typed (``details.code`` /
    ``details.model``), a ``RuntimeError`` so every caller that already
    handles "platform model is no longer available" handles this too. 409:
    the request conflicts with the user's own settings, which they can
    change; it is not an authorization failure.
    """

    status_code = 409
    code = PLATFORM_MODEL_NOT_AVAILABLE

    _WHY = {
        "owner_scope": "it is private to another user",
        "platform_card_disabled": "the platform card is switched off in your Settings",
        "user_disabled": "you disabled it in your Settings",
        "not_served": "it is not currently served",
    }

    def __init__(self, model_name: str, reason: str) -> None:
        why = self._WHY.get(reason, reason)
        super().__init__(
            f"Platform model '{model_name}' is not available to you: {why}.",
            details={
                "code": PLATFORM_MODEL_NOT_AVAILABLE,
                "model": model_name,
                "reason": reason,
            },
        )
        self.model = model_name
        self.reason = reason


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


@dataclass(frozen=True)
class PlatformRow:
    """One row of :func:`platform_rows`: the computed model plus the full
    catalog row it came from.

    ``catalog_row`` is PRIVATE (api_key revealed, base_url, actual_provider):
    dispatch builds providers from it, nothing serializes it.
    """

    model: PlatformModel
    catalog_row: Mapping[str, Any]

    def dispatch_row(self) -> dict[str, Any]:
        """The catalog row with the computed ``status`` — what a resolver
        builds a provider from."""
        return {**self.catalog_row, "status": self.model.status}

    def public_row(self) -> dict[str, Any]:
        """Public shape (``AiNousModelPublic`` fields) plus ``status``."""
        return {**self.model.public_row(), "status": self.model.status}


async def _overlay(
    rows: Sequence[Mapping[str, Any]],
) -> tuple[list[PlatformRow], Optional[PlatformEngine]]:
    """Steps 3–5 over full catalog rows (api_key revealed). Pure apart from
    the engine read; each distinct engine credential is read once."""
    credentials = {
        r["name"]: engine_credential(r)
        for r in rows
        if r.get("actual_provider") == NOUS_ENGINE_PROVIDER
    }
    snapshots = await snapshots_for(credentials)
    out: list[PlatformRow] = []
    for row in rows:
        snap = snapshots.get(row["name"])
        status = _row_status(row, snap)
        if status is not None:
            out.append(PlatformRow(_to_model(row, status, snap), row))
    return out, _engine_state(snapshots.values())


@dataclass(frozen=True)
class _UserGate:
    """The user's stored platform-card choices. ``enabled`` is the master
    switch; ``disabled`` the per-model blacklist (stored names)."""

    enabled: bool
    disabled: tuple[str, ...]

    @staticmethod
    def of(nous: Mapping[str, Any]) -> "_UserGate":
        from app.services.ai.platform_model_visibility import disabled_names

        return _UserGate(nous.get("enabled") is not False, disabled_names(dict(nous)))

    def hidden_names(self, names: Sequence[str]) -> frozenset[str]:
        """Which of ``names`` the blacklist hides (rename alias aware)."""
        from app.services.ai.platform_model_visibility import blacklisted

        named = [{"name": n} for n in names]
        hit = blacklisted(named, self.disabled)
        return frozenset(r["name"] for r in named if id(r) in hit)


_OPEN_GATE = _UserGate(True, ())


@dataclass(frozen=True)
class _Computed:
    gate: _UserGate
    governance_on: bool
    rows: tuple[PlatformRow, ...]
    enabled_names: frozenset[str]
    engine: Optional[PlatformEngine]


async def _compute(
    user_id: Optional[str], *, stored_nous: Optional[Mapping[str, Any]] = None
) -> _Computed:
    """THE computation (spec §3.1 steps 1–6). ``user_id=None`` is the system
    view: platform-wide rows only, governance and engine applied, no user
    gate. Raises when the catalog or the user's settings cannot be read."""
    from app.repositories.nous_model_repository import get_nous_model_repository
    from app.services.ai.governance.ai_governance import is_nous_globally_enabled
    from app.services.ai.platform_model_visibility import stored_nous_settings

    if user_id is None:
        gate = _OPEN_GATE
    else:
        nous = (
            stored_nous
            if stored_nous is not None
            else await stored_nous_settings(user_id)
        )
        gate = _UserGate.of(nous)
    if not await is_nous_globally_enabled():
        return _Computed(gate, False, (), frozenset(), None)
    catalog = await get_nous_model_repository().list_enabled_private(user_id)
    rows, engine = await _overlay(catalog)
    hidden = gate.hidden_names([r.model.name for r in rows])
    enabled_names = frozenset(r.model.name for r in rows) - hidden
    return _Computed(gate, True, tuple(rows), enabled_names, engine)


async def platform_rows(
    user_id: Optional[str],
    *,
    type: Optional[str] = None,  # noqa: A002 — the catalog column's name
    purpose: PlatformRowsPurpose,
) -> list[PlatformRow]:
    """:func:`platform_rows_and_engine` without the engine state."""
    rows, _ = await platform_rows_and_engine(user_id, type=type, purpose=purpose)
    return rows


async def platform_rows_and_engine(
    user_id: Optional[str],
    *,
    type: Optional[str] = None,  # noqa: A002 — the catalog column's name
    purpose: PlatformRowsPurpose,
) -> tuple[list[PlatformRow], Optional[PlatformEngine]]:
    """The platform rows ``user_id`` may use, in catalog order — the same
    computation as :func:`platform_provider_view`'s ``enabled_models``.

    Gates, in order: admin governance ``nous.user_enabled``; owner scope (a
    user sees platform rows plus their own); engine state for nous-engine rows
    (a service the engine no longer lists is gone); ``fail`` rows dropped; and
    with a ``user_id`` the platform-card master switch and the per-model
    blacklist. ``user_id=None`` is the system view: platform-wide rows,
    governance and engine only.

    ``purpose``:
      * ``"picker"`` — what a user may pick: image/video rows that cannot
        generate from a prompt (upscale-only) are left out, like the
        generation pickers' ``generatable`` gate. A failed read degrades to
        ``[]`` with an ERROR log.
      * ``"dispatch"`` — a resolver choosing a row to build a provider from:
        every row, upscale-only ones included (the resolver decides). A
        failed read RAISES — "could not read" must not become "no model".
      * ``"system"`` — background callers with no user (``user_id`` must be
        ``None``): degrades to ``[]`` with an ERROR log.
    """
    if purpose == "system" and user_id is not None:
        raise ValueError("platform_rows(purpose='system') takes no user_id")
    try:
        comp = await _compute(user_id)
    except Exception as exc:
        if purpose == "dispatch":
            raise
        logger.error(
            f"[platform_provider] platform rows ({purpose}) failed for "
            f"{user_id}: {exc!r}"
        )
        return [], None
    if not comp.gate.enabled:
        return [], comp.engine
    out: list[PlatformRow] = []
    for r in comp.rows:
        m = r.model
        if m.name not in comp.enabled_names:
            continue
        if type is not None and m.type != type:
            continue
        if purpose == "picker" and m.type in _GENERATION_TYPES and not m.generatable:
            continue
        out.append(r)
    return out, comp.engine


async def platform_rows_with_status(
    type_filter: Optional[str] = None,
) -> list[dict[str, Any]]:
    """System view (:func:`platform_rows` with no user) as public dicts plus
    ``status``, for background callers picking an implicit default. Degrades
    to ``[]`` with an ERROR log."""
    rows = await platform_rows(None, type=type_filter, purpose="system")
    return [r.public_row() for r in rows]


async def user_may_use(user_id: str, catalog_row: Mapping[str, Any]) -> None:
    """Raise :class:`PlatformModelNotAvailableError` unless ``user_id`` may
    use this catalog row by name: owner scope, platform-card master switch,
    blacklist — the user gates of :func:`platform_rows`, applied to one row a
    caller already resolved (``resolve_nous_model``). Governance per module
    and the engine check stay with the caller."""
    from app.services.ai.platform_model_visibility import stored_nous_settings

    name = str(catalog_row.get("name") or "")
    owner = catalog_row.get("owner_user_id")
    if owner and str(owner) != str(user_id):
        raise PlatformModelNotAvailableError(name, "owner_scope")
    gate = _UserGate.of(await stored_nous_settings(user_id))
    if not gate.enabled:
        raise PlatformModelNotAvailableError(name, "platform_card_disabled")
    if name in gate.hidden_names([name]):
        raise PlatformModelNotAvailableError(name, "user_disabled")


async def platform_provider_view(
    user_id: str, *, stored_nous: Optional[Mapping[str, Any]] = None
) -> PlatformProviderView:
    """The platform card for ``user_id`` (§3.1). ``stored_nous`` is the
    caller's already-loaded ``ai_providers.nous`` entry, if any.

    ``enabled`` is the STORED user switch, not ANDed with governance: the
    card echoes it back on save, and folding governance in would persist a
    switch the user never turned off. Governance shows as an empty list.
    """
    comp = await _compute(user_id, stored_nous=stored_nous)
    models = tuple(r.model for r in comp.rows)
    return PlatformProviderView(
        enabled=comp.gate.enabled,
        models=models,
        enabled_models=tuple(m.name for m in models if m.name in comp.enabled_names),
        disabled_models=comp.gate.disabled,
        engine=comp.engine,
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
    "PLATFORM_MODEL_NOT_AVAILABLE",
    "PlatformEngine",
    "PlatformModel",
    "PlatformModelNotAvailableError",
    "PlatformProviderView",
    "PlatformRow",
    "PlatformRowsPurpose",
    "generation_picker_models",
    "platform_provider_view",
    "platform_rows",
    "platform_rows_and_engine",
    "platform_rows_with_status",
    "platform_status",
    "user_may_use",
]
