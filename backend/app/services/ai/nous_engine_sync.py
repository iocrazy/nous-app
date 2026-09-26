# backend/app/services/ai/nous_engine_sync.py

"""Create catalog rows for what nous-engine serves (admin "Sync from nous-engine").

Since spec 2026-09-25 §3.4 the platform list reads the engine on demand
(``engine_catalog``): whether a service is authorized and loaded is never
copied into ``nous_models`` any more, and nothing disables a row
automatically. What is left here is the one thing the catalog still needs a
row for — a service the engine lists that the catalog has no row for yet —
run only when an admin presses the button:

* listed, no row (key: ``actual_provider='nous' AND actual_model=<id>``) →
  INSERT ``nous-<id>``, endpoint/credential/pricing/owner copied from an
  existing nous row on the same base_url, plus a zero ``ai_model_prices`` row;
* listed, row exists → ``context_window_tokens`` is written when the engine
  sends a valid ``context_window`` that differs (``refresh_catalog_windows``
  reads that column);
* engine types the catalog has no type for (``app`` / ``workflow`` / …) →
  reported as skipped, never guessed.

The list is ``engine_catalog.engine_snapshot`` — the same cached read the
platform view uses. A list that cannot be read (401, transport, 5xx, or only a
stale carried-over snapshot) is an ``error`` and writes nothing. ``null``
fields never overwrite a stored value: an invalid or null window is not
written, and ``capabilities.vision`` only seeds a NEW row's price-table
``supports_vision`` flag.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol, Sequence

from loguru import logger

from app.core.catalog_names import catalog_name_candidates
from app.repositories.nous_engine_sync_repository import (
    NOUS_ENGINE_PROVIDER,
    get_nous_engine_sync_repository,
)
from app.schemas.nous_model import INT4_MAX
from app.services.ai.engine_catalog import (
    EngineService,
    EngineSnapshot,
    engine_snapshot,
    normalize_base_url,
)

# engine service ``type`` → catalog ``type`` (schemas.nous_model.NousModelType).
ENGINE_TYPE_TO_CATALOG: Mapping[str, str] = {
    "llm": "llm",
    "inference": "llm",
    "embedding": "embedding",
    "asr": "asr",
    "image": "image",
    "comfy_template": "image",
}

CATALOG_NAME_PREFIX = "nous-"
_ERROR_TEXT_MAX = 200


@dataclass(frozen=True)
class SkippedService:
    id: str
    reason: str


@dataclass(frozen=True)
class SyncReport:
    """Outcome of one sync against one engine endpoint.

    ``error`` set ⇒ the list could not be read and nothing was written.
    ``created`` / ``updated`` hold catalog names.
    """

    discovered: int = 0
    created: tuple[str, ...] = ()
    updated: tuple[str, ...] = ()
    skipped: tuple[SkippedService, ...] = ()
    error: str | None = None


@dataclass
class _Tally:
    created: list[str] = field(default_factory=list)
    updated: list[str] = field(default_factory=list)
    skipped: list[SkippedService] = field(default_factory=list)


class EngineSyncRepo(Protocol):
    async def list_engine_rows(self) -> list[dict[str, Any]]: ...

    async def names_taken(self, names: Sequence[str]) -> set[str]: ...

    async def max_sort_order(self) -> int: ...

    async def insert_engine_row(
        self, values: dict[str, Any], *, supports_vision: bool
    ) -> dict[str, Any]: ...

    async def set_context_window(self, row_id: int, tokens: int) -> bool: ...


def display_name_for(service_id: str) -> str:
    """``qwen3-8-27b-huihui`` → ``Nous Qwen3 8 27B Huihui`` (mig 502 style)."""
    words = []
    for token in re.split(r"[-_\s]+", service_id.strip()):
        if not token:
            continue
        if re.fullmatch(r"\d+(?:\.\d+)?[a-zA-Z]", token):
            words.append(token.upper())
        else:
            words.append(token[:1].upper() + token[1:])
    return " ".join(["Nous", *words])


def _valid_window(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if 0 < value <= INT4_MAX else None


def _wants_vision(service: EngineService) -> bool:
    caps = service.capabilities
    return isinstance(caps, Mapping) and caps.get("vision") is True


def _source_row(
    rows: Sequence[Mapping[str, Any]], base_url: str
) -> Mapping[str, Any] | None:
    """The row a new one copies endpoint + credential from: a platform-wide
    nous row on the same base_url with a stored key, enabled rows first."""
    target = normalize_base_url(base_url)
    candidates = [
        r
        for r in rows
        if normalize_base_url(r.get("base_url")) == target
        and r.get("owner_user_id") is None
        and (r.get("api_key") or "").strip()
    ]
    candidates.sort(key=lambda r: (not r.get("is_enabled"), r.get("sort_order") or 0))
    return candidates[0] if candidates else None


async def _update_existing(
    repo: EngineSyncRepo,
    row: Mapping[str, Any],
    service: EngineService,
    tally: _Tally,
) -> None:
    window = _valid_window(service.context_window)
    if window is not None and window != row.get("context_window_tokens"):
        if await repo.set_context_window(row["id"], window):
            tally.updated.append(row["name"])


async def _create_row(
    repo: EngineSyncRepo,
    service_id: str,
    catalog_type: str,
    service: EngineService,
    source: Mapping[str, Any],
    sort_order: int,
) -> str:
    values = {
        "name": CATALOG_NAME_PREFIX + service_id,
        "display_name": display_name_for(service_id),
        "type": catalog_type,
        "actual_provider": NOUS_ENGINE_PROVIDER,
        "actual_model": service_id,
        "api_key": source["api_key"],
        "app_id": source.get("app_id"),
        "base_url": source.get("base_url"),
        "pricing_type": source.get("pricing_type") or "per_hour",
        "pricing_value": source.get("pricing_value") or 0,
        "owner_user_id": source.get("owner_user_id"),
        "is_enabled": True,
        "sort_order": sort_order,
        "description": f"Auto-synced from nous-engine service '{service_id}'",
        "context_window_tokens": _valid_window(service.context_window),
    }
    created = await repo.insert_engine_row(
        values, supports_vision=_wants_vision(service)
    )
    return created["name"]


async def _sync_one(
    repo: EngineSyncRepo,
    service: EngineService,
    ctx: dict[str, Any],
    tally: _Tally,
) -> None:
    service_id = service.id
    catalog_type = ENGINE_TYPE_TO_CATALOG.get(service.type)
    if catalog_type is None:
        tally.skipped.append(
            SkippedService(service_id, f"unsupported_type:{service.type}")
        )
        return
    existing = ctx["by_model"].get(service_id)
    if existing is not None:
        await _update_existing(repo, existing, service, tally)
        return
    if ctx["source"] is None:
        tally.skipped.append(SkippedService(service_id, "no_credential_source"))
        return
    name = CATALOG_NAME_PREFIX + service_id
    if await repo.names_taken(list(catalog_name_candidates(name))):
        tally.skipped.append(SkippedService(service_id, "name_taken"))
        return
    ctx["sort_order"] += 1
    tally.created.append(
        await _create_row(
            repo, service_id, catalog_type, service, ctx["source"], ctx["sort_order"]
        )
    )


def _read_error(snapshot: EngineSnapshot) -> str | None:
    """Why this snapshot may not be written from, or ``None`` when it may.

    A stale snapshot is the last good list carried over a failed read: fine
    for showing a status, not for creating rows an admin asked for NOW.
    """
    if snapshot.services is None:
        return snapshot.error or "engine list unavailable"
    if snapshot.stale:
        return snapshot.error or "engine list is stale"
    return None


async def sync_engine_models(
    *, base_url: str, api_key: str, repo: EngineSyncRepo | None = None
) -> SyncReport:
    """Create rows for listed services the catalog lacks; update windows.

    Never raises for engine-side problems (``SyncReport.error``); a failure
    writing one service is logged and reported as skipped, the rest continue.
    """
    repo = repo or get_nous_engine_sync_repository()
    snapshot = await engine_snapshot(base_url, api_key)
    error = _read_error(snapshot)
    if error is not None:
        return SyncReport(error=error[:_ERROR_TEXT_MAX])
    services = list((snapshot.services or {}).values())
    rows = await repo.list_engine_rows()
    ctx: dict[str, Any] = {
        "by_model": _rows_by_model(rows, base_url),
        "source": _source_row(rows, base_url),
        "sort_order": await repo.max_sort_order(),
    }
    tally = _Tally()
    for service in services:
        try:
            await _sync_one(repo, service, ctx, tally)
        except Exception as exc:  # noqa: BLE001 — one bad write must not stop the rest
            logger.error(
                f"[nous_engine_sync] writing service {service.id!r} failed: {exc!r}"
            )
            tally.skipped.append(SkippedService(service.id, "write_failed"))
    return SyncReport(
        discovered=len(services),
        created=tuple(tally.created),
        updated=tuple(tally.updated),
        skipped=tuple(tally.skipped),
    )


def _rows_by_model(
    rows: Sequence[Mapping[str, Any]], base_url: str
) -> dict[str, Mapping[str, Any]]:
    """``actual_model`` → row; a platform row on THIS base_url wins over any
    other row with the same model id, so the window lands on the right one."""
    target = normalize_base_url(base_url)

    def rank(r: Mapping[str, Any]) -> tuple[bool, bool]:
        return (
            normalize_base_url(r.get("base_url")) == target,
            r.get("owner_user_id") is None,
        )

    by_model: dict[str, Mapping[str, Any]] = {}
    for r in sorted(rows, key=rank):
        by_model[r["actual_model"]] = r
    return by_model


def engine_endpoints(rows: Sequence[Mapping[str, Any]]) -> list[tuple[str, str]]:
    """``(base_url, api_key)`` per distinct base_url of the ENABLED platform
    nous rows, from ``NousModelRepository.list_all()`` (key already revealed).
    Same preference order as the credential source a new row copies from."""
    endpoints: dict[str, str] = {}
    ordered = sorted(rows, key=lambda r: r.get("sort_order") or 0)
    for row in ordered:
        if row.get("actual_provider") != NOUS_ENGINE_PROVIDER or not row.get(
            "is_enabled"
        ):
            continue
        if row.get("owner_user_id") is not None:
            continue
        base = normalize_base_url(row.get("base_url"))
        key = (row.get("api_key") or "").strip()
        if base and key and base not in endpoints:
            endpoints[base] = key
    return list(endpoints.items())


async def sync_all_engines(
    rows: Sequence[Mapping[str, Any]], repo: EngineSyncRepo | None = None
) -> list[tuple[str, SyncReport]]:
    """Run ``sync_engine_models`` once per engine endpoint. Never raises: an
    endpoint that fails (engine error or a crash) is logged at WARNING and
    reported with ``error`` set."""
    results: list[tuple[str, SyncReport]] = []
    for base_url, api_key in engine_endpoints(rows):
        try:
            report = await sync_engine_models(
                base_url=base_url, api_key=api_key, repo=repo
            )
        except Exception as exc:  # noqa: BLE001 — sync is best-effort
            report = SyncReport(error=f"{type(exc).__name__}: {exc}"[:_ERROR_TEXT_MAX])
        if report.error:
            logger.warning(f"[nous_engine_sync] {base_url}: {report.error}")
        elif report.created or report.updated:
            logger.info(
                f"[nous_engine_sync] {base_url}: discovered={report.discovered} "
                f"created={list(report.created)} updated={list(report.updated)}"
            )
        results.append((base_url, report))
    return results


def merge_reports(reports: Sequence[SyncReport]) -> SyncReport:
    """One report for the admin toast; errors joined, lists concatenated."""
    errors = [r.error for r in reports if r.error]
    return SyncReport(
        discovered=sum(r.discovered for r in reports),
        created=tuple(n for r in reports for n in r.created),
        updated=tuple(n for r in reports for n in r.updated),
        skipped=tuple(s for r in reports for s in r.skipped),
        error="; ".join(errors) if errors else None,
    )
