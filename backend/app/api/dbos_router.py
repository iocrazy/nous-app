"""DBOS health + introspection endpoints.

Operational use:
    GET /api/v1/dbos/health    → liveness + worker status
    GET /api/v1/dbos/routing   → current dbos_workflow_routing snapshot (admin)
"""

from __future__ import annotations

from fastapi import APIRouter

from app.core.admin_deps import AdminAuthDep
from app.schemas.probes import DbosHealthResponse, DbosRoutingResponse
from app.services.infra import dbos_orchestrator

router = APIRouter(prefix="/dbos", tags=["DBOS"])


@router.get("/health", response_model=DbosHealthResponse)
async def dbos_health() -> dict:
    """Returns DBOS singleton state. Used by the deploy verifier + canary."""
    return {
        "enabled": dbos_orchestrator.is_enabled(),
    }


@router.get("/routing", response_model=DbosRoutingResponse)
async def dbos_routing(_admin: AdminAuthDep) -> dict:
    """Snapshot of routing decisions per task_type. Useful for ops dashboard
    to see what's celery / shadow / dbos at a glance.

    Platform admins only: every call forces a database refresh of the routing
    cache, and the task-type table is internal configuration.
    """
    # Force a refresh so admins see live state, not stale cache.
    await dbos_orchestrator._refresh_routing_cache()
    return {
        "loaded_at": dbos_orchestrator._routing_loaded_at,
        "task_types": sorted(dbos_orchestrator._routing_cache.items()),
    }
