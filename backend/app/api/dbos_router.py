"""DBOS health + introspection endpoints.

Operational use:
    GET /api/v1/dbos/health    → liveness + worker status
    GET /api/v1/dbos/routing   → current dbos_workflow_routing snapshot
"""

from __future__ import annotations

from fastapi import APIRouter

from app.services.infra import dbos_orchestrator
router = APIRouter(prefix="/dbos", tags=["DBOS"])


@router.get("/health")
async def dbos_health() -> dict:
    """Returns DBOS singleton state. Used by the deploy verifier + canary."""
    return {
        "enabled": dbos_orchestrator.is_enabled(),
    }


@router.get("/routing")
async def dbos_routing() -> dict:
    """Snapshot of routing decisions per task_type. Useful for ops dashboard
    to see what's celery / shadow / dbos at a glance.
    """
    # Force a refresh so admins see live state, not stale cache.
    await dbos_orchestrator._refresh_routing_cache()
    return {
        "loaded_at": dbos_orchestrator._routing_loaded_at,
        "task_types": sorted(dbos_orchestrator._routing_cache.items()),
    }
