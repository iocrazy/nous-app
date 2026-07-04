"""Topic Inspiration module switches (processing + visibility).

Two independent global switches for the whole feature, separate from the
per-function toggles (pre-filter / scoring / content-fetch) and from per-team
module permissions, stored together in ``system_settings['topics.module']``:

- ``enabled`` — the PROCESSING switch. When off the scheduled tick does
  nothing (no fetch / score / embed / cluster). Never consulted by the
  frontend for rendering.
- ``visible`` — the DISPLAY switch. Controls only whether the frontend shows
  the nav entry + page. Pausing the pipeline no longer hides the surface
  (the 2026-06-30 incident: an admin paused processing and the nav vanished).

Both default on and fail open (missing/garbage → on) so a config-read hiccup
never silently disables or hides the module. Admin-tunable, instant, no
redeploy. NEVER reads env.
"""

from __future__ import annotations

from typing import Any

from loguru import logger

MODULE_CONFIG_KEY = "topics.module"
DEFAULT_MODULE_ENABLED = True
DEFAULT_MODULE_VISIBLE = True


def _parse_bool_field(raw: Any, field: str, default: bool) -> bool:
    """Extract a bool field from the stored jsonb blob. Missing/garbage →
    default (on) so a config-read hiccup never silently flips a switch."""
    if isinstance(raw, dict):
        v = raw.get(field)
        if isinstance(v, bool):
            return v
    return default


def parse_module_enabled(raw: Any) -> bool:
    """Processing switch from the stored blob (fail-open)."""
    return _parse_bool_field(raw, "enabled", DEFAULT_MODULE_ENABLED)


def parse_module_visible(raw: Any) -> bool:
    """Display switch from the stored blob (fail-open). The pre-split stored
    shape ``{"enabled": ...}`` has no ``visible`` field → defaults to shown."""
    return _parse_bool_field(raw, "visible", DEFAULT_MODULE_VISIBLE)


async def _read_raw() -> Any:
    """Service-role engine read of the config blob; never raises — any
    failure returns None so the parsers fall back to the fail-open defaults."""
    try:
        from app.db import engine as db_engine

        if not db_engine.is_configured():
            return None
        return await db_engine.fetch_val(
            "SELECT value FROM public.system_settings WHERE key = :k",
            {"k": MODULE_CONFIG_KEY},
        )
    except Exception:  # noqa: BLE001
        logger.warning("[topic-module] config read failed — using defaults")
        return None


async def is_module_enabled() -> bool:
    """Whether the Topic Inspiration pipeline is enabled (processing switch)."""
    return parse_module_enabled(await _read_raw())


async def is_module_visible() -> bool:
    """Whether the Topic Inspiration surface is shown (display switch)."""
    return parse_module_visible(await _read_raw())
