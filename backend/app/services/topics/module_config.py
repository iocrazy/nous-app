"""Topic Inspiration module master switch.

A single global kill switch for the whole feature, separate from the per-function
toggles (pre-filter / scoring / content-fetch) and from per-team module
permissions. When off: the scheduled tick does nothing (no fetch / score / embed
/ cluster) and the frontend hides the page. Default on. Admin-tunable via
``system_settings['topics.module']``, instant, no redeploy. NEVER reads env.
"""

from __future__ import annotations

from typing import Any

from loguru import logger

MODULE_CONFIG_KEY = "topics.module"
DEFAULT_MODULE_ENABLED = True


def parse_module_enabled(raw: Any) -> bool:
    """Extract ``enabled`` from the stored jsonb blob. Missing/garbage → default
    (on) so a config-read hiccup never silently disables the module."""
    if isinstance(raw, dict):
        v = raw.get("enabled")
        if isinstance(v, bool):
            return v
    return DEFAULT_MODULE_ENABLED


async def is_module_enabled() -> bool:
    """Whether the Topic Inspiration module is enabled. Service-role engine read;
    never raises — any failure / missing key returns the default (on)."""
    try:
        from app.db import engine as db_engine

        if not db_engine.is_configured():
            return DEFAULT_MODULE_ENABLED
        raw = await db_engine.fetch_val(
            "SELECT value FROM public.system_settings WHERE key = :k",
            {"k": MODULE_CONFIG_KEY},
        )
        return parse_module_enabled(raw)
    except Exception:  # noqa: BLE001
        logger.warning("[topic-module] config read failed — defaulting to enabled")
        return DEFAULT_MODULE_ENABLED
