"""Topic Inspiration module switches (processing + visibility).

Thin shim over ``app.services.modules.registry`` — the single source of truth.
Two independent global switches stored together in
``system_settings['topics.module']``:

- ``enabled`` — the PROCESSING switch. When off the scheduled tick does nothing.
- ``visible`` — the DISPLAY switch. Controls the frontend nav entry + page.

Both default ON and fail OPEN (missing/garbage/read-error → on). Admin-tunable,
instant, no redeploy. NEVER reads env.
"""

from __future__ import annotations

from app.services.modules.registry import read_state_for_key

MODULE_CONFIG_KEY = "topics.module"
DEFAULT_MODULE_ENABLED = True
DEFAULT_MODULE_VISIBLE = True


async def is_module_enabled() -> bool:
    """Whether the Topic Inspiration pipeline is enabled (processing switch)."""
    state = await read_state_for_key(
        MODULE_CONFIG_KEY, DEFAULT_MODULE_ENABLED, DEFAULT_MODULE_VISIBLE
    )
    return state.enabled


async def is_module_visible() -> bool:
    """Whether the Topic Inspiration surface is shown (display switch)."""
    state = await read_state_for_key(
        MODULE_CONFIG_KEY, DEFAULT_MODULE_ENABLED, DEFAULT_MODULE_VISIBLE
    )
    return state.visible
