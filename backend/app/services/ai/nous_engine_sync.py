# backend/app/services/ai/nous_engine_sync.py

"""Mirror what nous-engine serves into the platform catalog (``nous_models``).

The BYOK provider cards ask a provider's ``/v1/models`` and show what they
found. The local nous-engine lives in the admin platform catalog instead, and
its rows used to be typed in by hand. This module reads the engine's
``GET {base_url}/models`` (bearer = the platform key of an existing nous row)
and makes the catalog follow it:

* listed, no row (key: ``actual_provider='nous' AND actual_model=<id>``) →
  INSERT ``nous-<id>``, endpoint/credential/pricing/owner copied from an
  existing nous row on the same base_url, plus a zero ``ai_model_prices`` row;
* listed, row exists → ``context_window_tokens`` is written when the engine
  sends a valid ``context_window`` that differs, and the entry's ``ready`` is
  mirrored into ``last_test_status`` (``ok`` / ``idle``) when it changed;
* engine types the catalog has no type for (``app`` / ``workflow`` / …) →
  reported as skipped, never guessed.

The engine contract (nous-engine ``/v1/models``)
------------------------------------------------
The list is read with ``?include_unready=1``, so it holds EVERY service this
key is authorized for, loaded or not; each entry carries ``ready`` (callable
right now — an LLM idle for an hour is unloaded and turns ``false``; workflow
and image services are always ``true``). Without the parameter unloaded models
are left out, which would read as a mass revocation.

* A service whose grant was paused/deleted DISAPPEARS from that list. So on a
  successful read (200 with a ``data`` list) every ENABLED platform row
  (``owner_user_id IS NULL``) on this base_url whose ``actual_model`` is
  missing is disabled and reported in ``disabled``, one WARNING each. Rows an
  admin already disabled, rows on other base_urls and BYOK rows are not
  touched, and a service that reappears is NOT re-enabled: turning it back on
  would override an admin's manual disable, so that is the admin's call.
* The whole key revoked/deleted answers 401: every enabled platform row on the
  base_url is disabled and the report says ``unauthorized``. After that
  ``engine_endpoints`` no longer yields the base_url, so neither this sync nor
  the probe touches it until an admin enables one row with a new key.
* Any other failure (5xx, timeout, transport, malformed body) is only an
  ``error``: a probe that cannot reach the engine is not a revocation.
* ``ready=false`` is NEVER read as revoked — it only means not loaded.

Fields that may be ``null`` (``context_window`` / ``capabilities`` on non-model
services) never overwrite a stored value: an invalid or null window is not
written, and ``capabilities.vision`` only seeds a NEW row's price-table
``supports_vision`` flag. ``capabilities.tools`` / ``thinking`` have no catalog
column yet and are ignored.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol, Sequence

import httpx
from loguru import logger

from app.core.catalog_names import catalog_name_candidates
from app.repositories.nous_engine_sync_repository import (
    NOUS_ENGINE_PROVIDER,
    get_nous_engine_sync_repository,
)
from app.schemas.nous_model import INT4_MAX
from app.services.ai.nous_model_health import PROBEABLE_TYPES

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
_LIST_TIMEOUT_S = 15.0
_ERROR_TEXT_MAX = 200
UNAUTHORIZED_ERROR = "HTTP 401: platform key rejected by nous-engine"
# Row types whose ``last_test_status`` follows the engine's ``ready``. Image
# rows are excluded on purpose: the hourly probe writes ``not_probed`` for them
# and two writers would flip the light back and forth.
READY_TRACKED_TYPES = frozenset(PROBEABLE_TYPES - {"image"})


@dataclass(frozen=True)
class SkippedService:
    id: str
    reason: str


@dataclass(frozen=True)
class SyncReport:
    """Outcome of one sync against one engine endpoint.

    ``error`` set ⇒ the list could not be read; nothing was written except,
    when ``unauthorized`` (401), the rows in ``disabled``. ``created`` /
    ``updated`` / ``disabled`` hold catalog names; ``ready_changed`` counts
    rows whose ok/idle status followed the engine's ``ready``.
    """

    discovered: int = 0
    created: tuple[str, ...] = ()
    updated: tuple[str, ...] = ()
    skipped: tuple[SkippedService, ...] = ()
    disabled: tuple[str, ...] = ()
    ready_changed: int = 0
    unauthorized: bool = False
    error: str | None = None


@dataclass
class _Tally:
    created: list[str] = field(default_factory=list)
    updated: list[str] = field(default_factory=list)
    skipped: list[SkippedService] = field(default_factory=list)
    ready_changed: int = 0


@dataclass(frozen=True)
class _Fetch:
    data: list[Any] | None = None
    error: str | None = None
    unauthorized: bool = False


class EngineSyncRepo(Protocol):
    async def list_engine_rows(self) -> list[dict[str, Any]]: ...

    async def names_taken(self, names: Sequence[str]) -> set[str]: ...

    async def max_sort_order(self) -> int: ...

    async def insert_engine_row(
        self, values: dict[str, Any], *, supports_vision: bool
    ) -> dict[str, Any]: ...

    async def set_context_window(self, row_id: int, tokens: int) -> bool: ...

    async def disable_rows(self, ids: Sequence[int]) -> list[str]: ...

    async def record_ready(self, row_id: int, ready: bool) -> bool: ...


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


def normalize_base_url(base_url: str | None) -> str:
    return (base_url or "").strip().rstrip("/")


def _valid_window(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if 0 < value <= INT4_MAX else None


def _wants_vision(entry: Mapping[str, Any]) -> bool:
    caps = entry.get("capabilities")
    return isinstance(caps, Mapping) and caps.get("vision") is True


async def _fetch_services(base_url: str, api_key: str) -> _Fetch:
    url = f"{normalize_base_url(base_url)}/models"
    try:
        async with httpx.AsyncClient(timeout=_LIST_TIMEOUT_S) as client:
            resp = await client.get(
                url,
                params={"include_unready": "1"},
                headers={"Authorization": f"Bearer {api_key}"},
            )
    except Exception as exc:  # noqa: BLE001 — reported as a typed error
        return _Fetch(error=f"{type(exc).__name__}: {str(exc) or '<no message>'}")
    if resp.status_code == 401:
        return _Fetch(error=UNAUTHORIZED_ERROR, unauthorized=True)
    if resp.status_code != 200:
        return _Fetch(error=f"HTTP {resp.status_code}: {resp.text[:160]}")
    try:
        payload = resp.json()
    except ValueError:
        return _Fetch(error="engine /models response is not JSON")
    data = payload.get("data") if isinstance(payload, Mapping) else None
    if not isinstance(data, list):
        return _Fetch(error="engine /models response has no 'data' list")
    return _Fetch(data=data)


def _platform_rows_on(
    rows: Sequence[Mapping[str, Any]], base_url: str
) -> list[Mapping[str, Any]]:
    """ENABLED platform-wide (non-BYOK) rows on ``base_url``."""
    target = normalize_base_url(base_url)
    return [
        r
        for r in rows
        if normalize_base_url(r.get("base_url")) == target
        and r.get("owner_user_id") is None
        and r.get("is_enabled")
    ]


async def _disable(
    repo: EngineSyncRepo,
    base_url: str,
    rows: Sequence[Mapping[str, Any]],
    reason: str,
) -> tuple[str, ...]:
    if not rows:
        return ()
    names = tuple(await repo.disable_rows([r["id"] for r in rows]))
    for name in names:
        logger.warning(f"[nous_engine_sync] {base_url}: disabled {name} — {reason}")
    return names


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
    entry: Mapping[str, Any],
    tally: _Tally,
    base_url: str,
) -> None:
    window = _valid_window(entry.get("context_window"))
    if window is not None and window != row.get("context_window_tokens"):
        if await repo.set_context_window(row["id"], window):
            tally.updated.append(row["name"])
    if row in _platform_rows_on([row], base_url):
        await _follow_ready(repo, row, entry, tally)


async def _follow_ready(
    repo: EngineSyncRepo,
    row: Mapping[str, Any],
    entry: Mapping[str, Any],
    tally: _Tally,
) -> None:
    """Mirror ``ready`` into ok/idle — enabled, ready-tracked rows only, and
    only when the status would change (a no-op write every minute is noise)."""
    ready = entry.get("ready")
    if not isinstance(ready, bool) or not row.get("is_enabled"):
        return
    if row.get("type") not in READY_TRACKED_TYPES:
        return
    if row.get("last_test_status") == ("ok" if ready else "idle"):
        return
    if await repo.record_ready(row["id"], ready):
        tally.ready_changed += 1


async def _create_row(
    repo: EngineSyncRepo,
    service_id: str,
    catalog_type: str,
    entry: Mapping[str, Any],
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
        "context_window_tokens": _valid_window(entry.get("context_window")),
    }
    created = await repo.insert_engine_row(values, supports_vision=_wants_vision(entry))
    return created["name"]


async def _sync_one(
    repo: EngineSyncRepo,
    entry: Any,
    ctx: dict[str, Any],
    tally: _Tally,
) -> None:
    service_id = entry.get("id") if isinstance(entry, Mapping) else None
    if not isinstance(service_id, str) or not service_id.strip():
        tally.skipped.append(SkippedService("<missing id>", "invalid_entry"))
        return
    if service_id in ctx["seen"]:
        return
    ctx["seen"].add(service_id)
    engine_type = str(entry.get("type") or "")
    catalog_type = ENGINE_TYPE_TO_CATALOG.get(engine_type)
    if catalog_type is None:
        tally.skipped.append(
            SkippedService(service_id, f"unsupported_type:{engine_type}")
        )
        return
    existing = ctx["by_model"].get(service_id)
    if existing is not None:
        await _update_existing(repo, existing, entry, tally, ctx["base_url"])
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
            repo, service_id, catalog_type, entry, ctx["source"], ctx["sort_order"]
        )
    )


async def sync_engine_models(
    *, base_url: str, api_key: str, repo: EngineSyncRepo | None = None
) -> SyncReport:
    """Read ``{base_url}/models`` and create/update catalog rows. See module doc.

    Never raises for engine-side problems (``SyncReport.error``); a failure
    writing one service is logged and reported as skipped, the rest continue.
    """
    repo = repo or get_nous_engine_sync_repository()
    fetched = await _fetch_services(base_url, api_key)
    if fetched.unauthorized:
        rows = await repo.list_engine_rows()
        disabled = await _disable(
            repo, base_url, _platform_rows_on(rows, base_url), "platform key rejected"
        )
        return SyncReport(error=fetched.error, unauthorized=True, disabled=disabled)
    if fetched.error is not None:
        return SyncReport(error=fetched.error[:_ERROR_TEXT_MAX])
    data = fetched.data or []
    rows = await repo.list_engine_rows()
    ctx: dict[str, Any] = {
        "by_model": _rows_by_model(rows, base_url),
        "source": _source_row(rows, base_url),
        "sort_order": await repo.max_sort_order(),
        "seen": set(),
        "base_url": base_url,
    }
    tally = _Tally()
    for entry in data:
        try:
            await _sync_one(repo, entry, ctx, tally)
        except Exception as exc:  # noqa: BLE001 — one bad write must not stop the rest
            service_id = entry.get("id") if isinstance(entry, Mapping) else None
            logger.error(
                f"[nous_engine_sync] writing service {service_id!r} failed: {exc!r}"
            )
            tally.skipped.append(SkippedService(str(service_id), "write_failed"))
    listed = {e.get("id") for e in data if isinstance(e, Mapping)}
    revoked = [
        r for r in _platform_rows_on(rows, base_url) if r["actual_model"] not in listed
    ]
    disabled = await _disable(
        repo, base_url, revoked, "service no longer authorized for this key"
    )
    return SyncReport(
        discovered=len(data),
        created=tuple(tally.created),
        updated=tuple(tally.updated),
        skipped=tuple(tally.skipped),
        disabled=disabled,
        ready_changed=tally.ready_changed,
    )


def _rows_by_model(
    rows: Sequence[Mapping[str, Any]], base_url: str
) -> dict[str, Mapping[str, Any]]:
    """``actual_model`` → row; a platform row on THIS base_url wins over any
    other row with the same model id, so ready/window land on the right one."""
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
        elif report.created or report.updated or report.disabled:
            logger.info(
                f"[nous_engine_sync] {base_url}: discovered={report.discovered} "
                f"created={list(report.created)} updated={list(report.updated)} "
                f"disabled={list(report.disabled)}"
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
        disabled=tuple(n for r in reports for n in r.disabled),
        ready_changed=sum(r.ready_changed for r in reports),
        unauthorized=any(r.unauthorized for r in reports),
        error="; ".join(errors) if errors else None,
    )
