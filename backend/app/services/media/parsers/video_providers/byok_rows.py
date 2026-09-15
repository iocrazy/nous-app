"""The BYOK tier of image-provider resolution: a user's OWN provider config
(Settings → AI) projected into ``mediahub_models``-shaped rows.

House rule: the providers page is the only model-management entry, and agents
consume whatever it makes available. The LLM side has honoured that for a long
time (``ai_provider_helpers`` resolves ``origin="byok"``); the image side read
the admin catalog and nothing else, so a user's own
``doubao-seedream-5-0-pro-260628`` was unreachable by ``GenerateImage`` /
shot generation no matter what the settings page said.

This module closes that gap WITHOUT a second dispatch path: every row it emits
has the shape ``db_registry``'s existing machinery already consumes
(``_visible_rows`` → ``_pick_row`` → ``protocol.build_image_provider(row)``),
so the BYOK tier is just more rows.

Admission is deliberately narrow, and every exclusion is a fail-closed one:

  - no ``user_id`` → no rows, and the settings loader is never called (there is
    no user whose credential this could legitimately be);
  - only providers the user marked ``enabled`` and that carry an api key
    (``api_key`` may be a ``str`` OR a ``list[str]`` — the real shape, see
    ``app.core.secure_settings``; the first non-empty entry wins);
  - only protocols that generate images over plain HTTP FROM THIS PROCESS
    (``"image" in model_types`` and ``supports_http_image_probe``). CLI/daemon
    families (codex, codex-local, jimeng-cli, openai-images) are excluded on
    purpose: their credential is a local OAuth session or a paired device, not
    an api key, so a BYOK key says nothing about whether this process can dial
    them;
  - only model ids that LOOK like image models (``suspected_non_chat_kind``,
    mirrored from the frontend guard). A BYOK catalog is raw upstream ids with
    no type metadata, so the id string is the only signal there is.

``actual_provider`` is the canonical PROTOCOL key (``"ark"``), never the
settings key (``"doubao"``) — it is what ``db_registry`` dispatches on and what
``ai_model_prices`` is keyed by, so a row that carried the settings key would
resolve nothing and cost nothing.
"""

from __future__ import annotations

from typing import Any

from loguru import logger

from app.services.ai.media.non_chat_model import suspected_non_chat_kind
from app.services.ai.providers.ai_provider_helpers import get_ai_settings

# Where a provider's image endpoint lives when the user left ``base_url`` blank
# — the same default the LLM side applies (``providers/ai_provider.py``) and the
# same one the settings page shows as the placeholder (``AISettings.tsx``).
# A provider absent from this map and without an explicit base_url is skipped:
# every HTTP image provider raises on an empty base_url, and a row that can
# only explode is worse than a row that never existed.
BYOK_IMAGE_BASE_URLS: dict[str, str] = {
    "doubao": "https://ark.cn-beijing.volces.com/api/v3",
}

# Catalog rows carry a ``sort_order``; these carry one too so the row shape
# lines up. NOTHING sorts by it — the tier order that actually decides an
# unspecified call is the list concatenation in
# ``db_registry.resolve_image_provider`` (``[*catalog, *byok]``), pinned by
# test_jimeng_dispatch's tier-order case. The base keeps the numbers from
# colliding with real catalog values if anyone ever does sort.
_BYOK_SORT_BASE = 10_000


def _first_api_key(raw: Any) -> str:
    """``api_key`` is a ``str`` OR a ``list[str]`` (multi-key rotation). The
    first non-empty stripped entry is the credential; nothing → ``""``."""
    values = raw if isinstance(raw, list) else [raw]
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _image_protocol(provider_key: str) -> Any | None:
    """The generation protocol this settings key maps to, if it can generate
    images over HTTP from this process. ``"doubao"`` resolves to ``ArkProtocol``
    through its aliases."""
    from app.services.ai.provider_protocols import resolve_generation_protocol

    protocol = resolve_generation_protocol(provider_key)
    if protocol is None:
        return None
    if "image" not in protocol.model_types or not protocol.supports_http_image_probe:
        return None
    return protocol


def _image_model_ids(entry: dict) -> list[str]:
    """The user's chosen models, in the user's order, keeping only the ids that
    look like image models."""
    enabled = entry.get("enabled_models")
    if isinstance(enabled, list) and enabled:
        candidates = enabled
    elif entry.get("selected_model"):
        candidates = [entry["selected_model"]]
    else:
        candidates = []
    return [
        model_id
        for model_id in candidates
        if isinstance(model_id, str) and suspected_non_chat_kind(model_id) == "image"
    ]


def _provider_rows(
    provider_key: str, entry: dict, user_id: str, start_index: int
) -> list[dict]:
    """One provider card → zero or more catalog-shaped rows. Builds new dicts;
    the settings mapping is never mutated."""
    if not entry.get("enabled"):
        return []
    api_key = _first_api_key(entry.get("api_key"))
    if not api_key:
        return []
    protocol = _image_protocol(provider_key)
    if protocol is None:
        return []
    model_ids = _image_model_ids(entry)
    if not model_ids:
        return []
    # ``str(...)`` because the value is whatever the user's PATCH stored: the
    # settings schema types ai_providers as ``Dict[str, Any]`` and
    # ``merge_ai_providers`` does not coerce, so a numeric base_url reaches
    # here as an int.
    base_url = str(entry.get("base_url") or "").strip() or BYOK_IMAGE_BASE_URLS.get(
        provider_key, ""
    )
    if not base_url:
        logger.warning(
            "BYOK image provider {!r} has no base_url and no known default; "
            "skipping {} model(s) for this user",
            provider_key,
            len(model_ids),
        )
        return []
    return [
        {
            "name": model_id,
            "type": "image",
            "actual_provider": protocol.key,
            "actual_model": model_id,
            "api_key": api_key,
            "base_url": base_url,
            "is_enabled": True,
            "owner_user_id": str(user_id),
            "sort_order": _BYOK_SORT_BASE + start_index + offset,
            "source": "byok",
            "byok_provider": provider_key,
        }
        for offset, model_id in enumerate(model_ids)
    ]


def _rows_from_settings(ai_settings: Any, user_id: str) -> list[dict]:
    """Project a settings mapping into rows. Tolerates a missing/empty
    ``ai_providers`` and skips non-dict provider entries, so ONE junk card does
    not cost the user the models they configured correctly."""
    if not isinstance(ai_settings, dict):
        raise TypeError(f"ai_settings is {type(ai_settings).__name__}, not a mapping")
    providers = ai_settings.get("ai_providers")
    if providers is None or providers == {}:
        return []  # the ordinary "configured nothing" case — not a fault
    if not isinstance(providers, dict):
        raise TypeError(f"ai_providers is {type(providers).__name__}, not a mapping")
    rows: list[dict] = []
    for provider_key, entry in providers.items():
        if not isinstance(entry, dict):
            continue
        rows.extend(_provider_rows(str(provider_key), entry, user_id, len(rows)))
    return rows


async def byok_image_rows(user_id: str | None) -> list[dict]:
    """The user's own image models as catalog-shaped rows (may be empty).

    Never raises — and that covers the PARSING, not just the load. This is
    awaited by ``resolve_image_provider`` BEFORE it can use its own catalog
    rows, so anything escaping here takes the catalog tier down with it. The
    stored shapes are not hypothetical: the settings schema types
    ``ai_providers`` as ``Dict[str, Any]`` and ``merge_ai_providers`` stores
    values as-is, so one user PATCH can put a list where a mapping belongs.

    Degrades loudly: a warning, then ``[]``. Silence would be wrong in the
    other direction — a bare ``[]`` is indistinguishable from "this user
    configured nothing".
    """
    if not user_id:
        return []
    try:
        ai_settings = await get_ai_settings(str(user_id))
        return _rows_from_settings(ai_settings, str(user_id))
    except Exception as exc:  # noqa: BLE001 — degrade loudly, never propagate
        logger.warning(
            "BYOK image tier unavailable for user {}: {!r}; "
            "falling back to the mediahub_models catalog only",
            user_id,
            exc,
        )
        return []
