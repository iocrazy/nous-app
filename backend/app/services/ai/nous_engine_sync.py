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
* listed, row exists → only ``context_window_tokens`` is written, when the
  engine sends a valid ``context_window`` that differs;
* engine types the catalog has no type for (``app`` / ``workflow`` / …) →
  reported as skipped, never guessed.

Absence is not a negative result
--------------------------------
Today ``/v1/models`` lists only services that are authorized AND LOADED. A row
whose service is missing is therefore never disabled or deleted here — a cold
model would otherwise vanish from the catalog every time the card swapped.
TODO(engine include_unloaded): once the engine honours ``?include_unloaded=1``
(already sent; unknown query params are ignored today) and marks each entry
``loaded``, a service missing from the FULL list means its grant was revoked;
only then may a follow-up disable such rows.

Forward-compatible fields: ``context_window`` is used when present;
``capabilities.vision`` seeds the new row's price-table ``supports_vision``
flag (the column ``model_capabilities`` reads). ``capabilities.tools`` /
``thinking`` and ``loaded`` have no catalog column yet and are ignored.
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


def normalize_base_url(base_url: str | None) -> str:
    return (base_url or "").strip().rstrip("/")


def _valid_window(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if 0 < value <= INT4_MAX else None


def _wants_vision(entry: Mapping[str, Any]) -> bool:
    caps = entry.get("capabilities")
    return isinstance(caps, Mapping) and caps.get("vision") is True


async def _fetch_services(
    base_url: str, api_key: str
) -> tuple[list[Any] | None, str | None]:
    url = f"{normalize_base_url(base_url)}/models"
    try:
        async with httpx.AsyncClient(timeout=_LIST_TIMEOUT_S) as client:
            resp = await client.get(
                url,
                params={"include_unloaded": "1"},
                headers={"Authorization": f"Bearer {api_key}"},
            )
    except Exception as exc:  # noqa: BLE001 — reported as a typed error
        return None, f"{type(exc).__name__}: {str(exc) or '<no message>'}"
    if resp.status_code != 200:
        return None, f"HTTP {resp.status_code}: {resp.text[:160]}"
    try:
        payload = resp.json()
    except ValueError:
        return None, "engine /models response is not JSON"
    data = payload.get("data") if isinstance(payload, Mapping) else None
    if not isinstance(data, list):
        return None, "engine /models response has no 'data' list"
    return data, None


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
) -> None:
    window = _valid_window(entry.get("context_window"))
    if window is None or window == row.get("context_window_tokens"):
        return
    if await repo.set_context_window(row["id"], window):
        tally.updated.append(row["name"])


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
        await _update_existing(repo, existing, entry, tally)
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
    data, error = await _fetch_services(base_url, api_key)
    if error is not None:
        return SyncReport(error=error[:_ERROR_TEXT_MAX])
    rows = await repo.list_engine_rows()
    ctx: dict[str, Any] = {
        "by_model": {r["actual_model"]: r for r in rows},
        "source": _source_row(rows, base_url),
        "sort_order": await repo.max_sort_order(),
        "seen": set(),
    }
    tally = _Tally()
    for entry in data or []:
        try:
            await _sync_one(repo, entry, ctx, tally)
        except Exception as exc:  # noqa: BLE001 — one bad write must not stop the rest
            service_id = entry.get("id") if isinstance(entry, Mapping) else None
            logger.error(
                f"[nous_engine_sync] writing service {service_id!r} failed: {exc!r}"
            )
            tally.skipped.append(SkippedService(str(service_id), "write_failed"))
    return SyncReport(
        discovered=len(data or []),
        created=tuple(tally.created),
        updated=tuple(tally.updated),
        skipped=tuple(tally.skipped),
    )


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
