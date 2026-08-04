"""Batch module-switch status for the frontend (spec 2026-08-03 §2.1).

One authenticated read returns every registered module's ``enabled`` +
``visible`` so the sidebar/router can gate all entries with a single
request — replaces the retired per-module ``/module-status`` endpoints
(topics, distribution).
"""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from app.core.cache import modules_status_cache
from app.core.deps import AuthDep
from app.services.modules.registry import list_module_summaries

router = APIRouter(prefix="/modules", tags=["Modules"])


class ModuleStatusItem(BaseModel):
    id: str
    enabled: bool
    visible: bool


class ModulesStatusResponse(BaseModel):
    modules: list[ModuleStatusItem]


@router.get("/status", response_model=ModulesStatusResponse)
async def get_modules_status(auth: AuthDep) -> ModulesStatusResponse:
    """Every registered module's switches, 60s server-side cache."""

    async def _loader() -> list[dict]:
        return await list_module_summaries()

    summaries = await modules_status_cache.get_or_load("all", _loader)
    return ModulesStatusResponse(
        modules=[
            ModuleStatusItem(id=s["id"], enabled=s["enabled"], visible=s["visible"])
            for s in summaries
        ]
    )
