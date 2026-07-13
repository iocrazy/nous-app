"""Editor module-status endpoints (user-facing, read-only).

Mirrors the per-module ``/module-status`` pattern (distribution): a module's
own tiny router exposes its admin-controlled registry state so the frontend
can read the (default-off) switch — NEVER an env flag. Lives OUTSIDE
``script_scenes_router`` on purpose: that router's authz contract
(test_episodes_scenes_authz_wiring) requires a resource scope guard on every
route, and a global per-user status read has no resource to scope.
"""

from typing import Any, Dict

from fastapi import APIRouter

from app.core.deps import AuthDep

router = APIRouter()


@router.get("/editor/tiptap-module-status")
async def tiptap_module_status(auth: AuthDep) -> Dict[str, Any]:
    """TipTap editing-surface switch (admin-controlled via the module
    registry, ``system_settings['editor.tiptap_surface']``). Opt-in, fails
    CLOSED: unset / read error → the editor serves the legacy engine.
    Reachable regardless of the switch so the frontend can always read the
    (default-off) state."""
    from app.services.modules.registry import MODULES_BY_KEY, read_module_state

    module = MODULES_BY_KEY["editor.tiptap_surface"]
    state = await read_module_state(module)
    return {"success": True, "data": {"enabled": state.enabled}}
