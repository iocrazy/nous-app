"""FastAPI dependency gate for Module Control Center switches.

One factory for every gated router (spec 2026-08-03 §2.1). Module OFF →
typed 503 so the frontend can show a "feature disabled" notice instead of a
generic error (repo discipline: 触发路径必须类型化失败回显). Fail mode
follows each module's registry default — the six 2026-08 rollout modules
are default-ON / fail-open.

NOT used by Distribution: that unlaunched module keeps its own 404
fail-closed gate (``require_distribution``) so its existence stays hidden.
"""

from __future__ import annotations

from typing import Awaitable, Callable

from fastapi import HTTPException

from app.core.cache import module_gate_cache
from app.services.modules.registry import MODULES_BY_ID, ModuleState, read_module_state


def require_module(module_id: str) -> Callable[[], Awaitable[None]]:
    """Dependency factory: raise typed 503 when ``module_id`` is disabled.

    Unknown ids raise ``KeyError`` here, at router-definition (import) time —
    a typo must crash startup, never silently un-gate an endpoint.

    The switch read is cached for 5s (``module_gate_cache``): this dependency
    runs on every request to a gated router and resolves BEFORE auth, so an
    uncached read would let unauthenticated traffic drive one
    ``system_settings`` query per request.
    """
    module = MODULES_BY_ID[module_id]

    async def _load() -> ModuleState:
        return await read_module_state(module)

    async def _check() -> None:
        state: ModuleState = await module_gate_cache.get_or_load(module.key, _load)
        if not state.enabled:
            raise HTTPException(
                status_code=503,
                detail={"code": "MODULE_DISABLED", "module": module_id},
            )

    return _check
