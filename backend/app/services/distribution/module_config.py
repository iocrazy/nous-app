"""Distribution module switches (access + visibility).

Thin shim over ``app.services.modules.registry`` — the single source of truth.
Two independent global switches stored together in
``system_settings['distribution.module']``:

- ``enabled`` — the ACCESS switch. Gates the backend account/OAuth API
  (``require_distribution`` 404s every endpoint when off).
- ``visible`` — the DISPLAY switch. Controls the frontend nav entry + routes.

Opt-in: both default OFF and fail CLOSED so a not-yet-launched module never
exposes its surface or API until an admin turns it on. Admin-tunable, instant,
no redeploy. NEVER reads env.
"""

from __future__ import annotations

from app.services.modules.registry import read_state_for_key

MODULE_CONFIG_KEY = "distribution.module"
DEFAULT_MODULE_ENABLED = False
DEFAULT_MODULE_VISIBLE = False


async def is_module_enabled() -> bool:
    """Whether the Distribution API is reachable (access switch)."""
    state = await read_state_for_key(
        MODULE_CONFIG_KEY, DEFAULT_MODULE_ENABLED, DEFAULT_MODULE_VISIBLE
    )
    return state.enabled


async def is_module_visible() -> bool:
    """Whether the Distribution surface is shown in the frontend (display switch)."""
    state = await read_state_for_key(
        MODULE_CONFIG_KEY, DEFAULT_MODULE_ENABLED, DEFAULT_MODULE_VISIBLE
    )
    return state.visible
