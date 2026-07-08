"""Distribution module switches (processing + visibility).

Mirrors ``services/topics/module_config.py`` — two global switches for the
whole Distribution feature, stored together in
``system_settings['distribution.module']``:

- ``enabled`` — the ACCESS switch. Gates the backend account/OAuth API
  (``require_distribution`` 404s every endpoint when off) AND is reported to
  the frontend.
- ``visible`` — the DISPLAY switch. Controls whether the frontend shows the
  nav entry + routes.

Unlike Topic Inspiration (which fails OPEN because it ships enabled), the
Distribution module is OPT-IN: both switches default OFF and fail CLOSED
(missing/garbage/read-error → off) so a brand-new, not-yet-launched module —
one that handles encrypted platform tokens — never exposes its surface or API
until an admin explicitly turns it on via ``system_settings``. Admin-tunable,
instant, no redeploy. NEVER reads env.
"""

from __future__ import annotations

import json
from typing import Any

from loguru import logger

MODULE_CONFIG_KEY = "distribution.module"
DEFAULT_MODULE_ENABLED = False
DEFAULT_MODULE_VISIBLE = False


def _coerce_dict(raw: Any) -> Any:
    """The jsonb ``value`` column can come back from the engine as a JSON
    string rather than an already-decoded dict (same as
    ``distribution.credentials`` / ``thumbnail`` config reads). Decode it so
    the bool parsers see a real dict; anything undecodable stays as-is and
    falls through to the default."""
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except ValueError:
            return None
    return raw


def _parse_bool_field(raw: Any, field: str, default: bool) -> bool:
    """Extract a bool field from the stored jsonb blob. Missing/garbage →
    default (off) so a config-read hiccup never silently exposes the module."""
    raw = _coerce_dict(raw)
    if isinstance(raw, dict):
        v = raw.get(field)
        if isinstance(v, bool):
            return v
    return default


def parse_module_enabled(raw: Any) -> bool:
    """Access switch from the stored blob (fail-closed)."""
    return _parse_bool_field(raw, "enabled", DEFAULT_MODULE_ENABLED)


def parse_module_visible(raw: Any) -> bool:
    """Display switch from the stored blob (fail-closed)."""
    return _parse_bool_field(raw, "visible", DEFAULT_MODULE_VISIBLE)


async def _read_raw() -> Any:
    """Service-role engine read of the config blob; never raises — any
    failure returns None so the parsers fall back to the fail-closed
    defaults."""
    try:
        from app.db import engine as db_engine

        if not db_engine.is_configured():
            return None
        return await db_engine.fetch_val(
            "SELECT value FROM public.system_settings WHERE key = :k",
            {"k": MODULE_CONFIG_KEY},
        )
    except Exception:  # noqa: BLE001
        logger.warning("[distribution-module] config read failed — using defaults")
        return None


async def is_module_enabled() -> bool:
    """Whether the Distribution API is reachable (access switch)."""
    return parse_module_enabled(await _read_raw())


async def is_module_visible() -> bool:
    """Whether the Distribution surface is shown in the frontend (display switch)."""
    return parse_module_visible(await _read_raw())
