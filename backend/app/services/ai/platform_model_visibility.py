"""The user's stored platform-card choices (``ai_providers.nous``) and the
blacklist matcher.

Two stored values, both user-owned: the card's master switch
``enabled`` and the per-model blacklist ``disabled_models``. They are applied
in exactly one place — ``services/ai/platform_provider`` (the platform
provider view and ``platform_rows``) — together with admin governance, owner
scope and live engine state, so the Settings card, every picker and every
dispatch decision read one computation. This module only reads the stored
values and resolves blacklisted names to rows.
"""

from __future__ import annotations

from typing import Any, Iterable

from app.core.catalog_names import find_row_by_catalog_name

_AI_SETTINGS_KEY = "ai_settings"


async def stored_nous_settings(user_id: str) -> dict[str, Any]:
    """The user's stored ``ai_providers.nous`` entry (``{}`` when absent)."""
    from app.repositories.user_settings_repository import UserSettingsRepository

    row = await UserSettingsRepository().get_by_user_id(user_id)
    settings_json = (row or {}).get("settings_json") or {}
    ai_settings = settings_json.get(_AI_SETTINGS_KEY) or {}
    nous = (ai_settings.get("ai_providers") or {}).get("nous")
    return dict(nous) if isinstance(nous, dict) else {}


def disabled_names(nous: dict[str, Any]) -> tuple[str, ...]:
    """The stored per-model blacklist, cleaned (order kept)."""
    raw = nous.get("disabled_models") or []
    if not isinstance(raw, (list, tuple)):
        return ()
    return tuple(dict.fromkeys(str(n) for n in raw if n))


def blacklisted(rows: list[dict[str, Any]], disabled: Iterable[str]) -> set[int]:
    """``id()`` of each row the blacklist hides. Pure.

    Each persisted name is resolved to the row it means — exact name first,
    then the mediahub-/nous- rename alias — so a blacklist written before the
    rename keeps hiding the renamed row, and a name that exactly matches one
    row never also hides its alias twin.
    """
    return {
        id(hit)
        for name in disabled
        if (hit := find_row_by_catalog_name(rows, name)) is not None
    }
