"""Admin-controlled switch for the unified-storage WRITE path.

The Module Control Center (Admin → System → Modules) owns this switch:
``storage.unified_storage`` in ``system_settings``, default OFF, fail-CLOSED
(unset / read error keeps every write on the legacy filesystem track).
Toggling it is live — writers consult the DB per call, so no restart is
needed and rollback is instant.

The ``FEATURE_UNIFIED_STORAGE`` env setting survives ONLY as a dev/test
short-circuit: when true it forces the unified track without a DB
round-trip (local stacks, unit tests that monkeypatch settings). Prod
never sets it, so the admin switch is the single control there.

READERS never consult this switch — ``resolve_media_source`` dispatches on
the stored path shape, so already-written ``sb://`` rows stay readable
regardless of the switch position.
"""

from __future__ import annotations

from app.core.config import settings
from app.services.modules.registry import MODULES_BY_ID, read_module_state

_MODULE = MODULES_BY_ID["unified-storage"]


async def unified_storage_enabled() -> bool:
    """True when new library writes should go to the `library` bucket."""
    if settings.FEATURE_UNIFIED_STORAGE:
        return True
    state = await read_module_state(_MODULE)
    return state.enabled
