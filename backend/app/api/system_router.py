# backend/app/api/system_router.py

"""
System Status Monitoring API

Provides queue status, disk space, and network status.
Now delegates to system_monitor_service for shared logic.
"""

import time
from typing import Any, Dict, List

from fastapi import APIRouter, HTTPException
from loguru import logger
from pydantic import BaseModel, Field

from app.core.admin_deps import AdminAuthDep
from app.core.deps import AuthDep
from app.db.supabase_client import get_async_supabase_admin
from app.services.system_monitor_service import (
    get_network_status,
    get_queue_status,
    get_storage_status,
)

router = APIRouter(prefix="/system")

TAGS = ["系统监控"]


class QueueStatus(BaseModel):
    active: int = 0
    pending: int = 0
    scheduled: int = 0
    status: str = "offline"  # offline | online | outdated
    missing_tasks: list[str] = []


class StorageStatus(BaseModel):
    total_bytes: int = 0
    used_bytes: int = 0
    free_bytes: int = 0
    percent_used: float = 0
    status: str = "unknown"
    path: str = ""


class NetworkStatus(BaseModel):
    speed: str = "0 B/s"
    status: str = "idle"


class SystemStatusResponse(BaseModel):
    queue: QueueStatus
    storage: StorageStatus
    network: NetworkStatus
    timestamp: float


@router.get("/status", tags=TAGS, response_model=SystemStatusResponse)
async def get_system_status(auth: AuthDep):
    """
    Get system status.

    Returns real-time queue, storage, and network status.
    Requires authentication: Bearer Token or API Key.
    """
    try:
        queue_data = await get_queue_status()
        storage_data = get_storage_status()
        network_data = get_network_status()

        return SystemStatusResponse(
            queue=QueueStatus(**queue_data),
            storage=StorageStatus(**storage_data),
            network=NetworkStatus(**network_data),
            timestamp=time.time(),
        )

    except Exception as e:
        logger.exception(f"Failed to get system status: {e}")
        raise HTTPException(
            status_code=500, detail=f"Failed to get system status: {str(e)}"
        )


# ---------------------------------------------------------------------------
# Backend Services Health — admin-only "is the backend wired up correctly"
# panel. Returns granular per-component status: DB extensions, trigram
# indexes, Celery worker, Redis, search endpoint smoke test.
# ---------------------------------------------------------------------------


class HealthCheck(BaseModel):
    """One row in the admin health panel."""

    name: str
    status: str  # "healthy" | "degraded" | "down" | "unknown"
    detail: str = ""
    metrics: Dict[str, Any] = Field(default_factory=dict)


class BackendHealthResponse(BaseModel):
    """Aggregate backend health snapshot. Admin-only."""

    overall: str  # "healthy" | "degraded" | "down"
    checks: List[HealthCheck]
    timestamp: float


async def _check_database() -> List[HealthCheck]:
    """DB connectivity probe (count = exact, head request)."""
    checks: List[HealthCheck] = []
    try:
        client = await get_async_supabase_admin()
        await client.table("parsed_media").select("id", head=True, count="exact").limit(
            1
        ).execute()
        checks.append(
            HealthCheck(
                name="Supabase / Postgres", status="healthy", detail="Connected"
            )
        )
    except Exception as e:
        checks.append(
            HealthCheck(name="Supabase / Postgres", status="down", detail=str(e)[:140])
        )
    return checks


async def _check_trigram_via_latency() -> HealthCheck:
    """Detect trigram index presence via ILIKE latency.

    If the GIN trigram index exists, ILIKE is a Bitmap Index Scan (~10-40ms
    on a small table). Without it, sequential scan grows linearly. We use
    the round-trip time as a binary "fast → indexed" / "slow → not indexed"
    signal — pgrest doesn't reliably expose pg_indexes from PostgREST so
    introspection is environment-specific and brittle.

    Threshold: 250ms. On a 5k-row library a Bitmap Index Scan is well under
    100ms; a Seq Scan on the same set is 500ms+.
    """
    try:
        client = await get_async_supabase_admin()
        t0 = time.time()
        await client.table("parsed_media").select("id").ilike(
            "title", "*__healthcheck_no_match__*"
        ).limit(1).execute()
        elapsed_ms = int((time.time() - t0) * 1000)
        status = "healthy" if elapsed_ms < 250 else "degraded"
        return HealthCheck(
            name="Trigram Indexes (search perf)",
            status=status,
            detail=(
                f"ILIKE roundtrip {elapsed_ms} ms"
                + ("" if status == "healthy" else " — likely missing pg_trgm GIN")
            ),
            metrics={"latency_ms": elapsed_ms, "threshold_ms": 250},
        )
    except Exception as e:
        return HealthCheck(
            name="Trigram Indexes (search perf)",
            status="unknown",
            detail=str(e)[:140],
        )


async def _check_dbos_engine() -> HealthCheck:
    """DBOS engine liveness via shared get_queue_status."""
    try:
        q = await get_queue_status()
        if q.get("status") == "online":
            return HealthCheck(
                name="DBOS Engine",
                status="healthy",
                detail=f"Running: {q.get('active', 0)}, Pending: {q.get('pending', 0)}",
                metrics=q,
            )
        return HealthCheck(
            name="DBOS Engine",
            status="down" if q.get("status") == "offline" else "degraded",
            detail=q.get("status", "unknown"),
            metrics=q,
        )
    except Exception as e:
        return HealthCheck(name="DBOS Engine", status="unknown", detail=str(e)[:140])


async def _check_redis() -> HealthCheck:
    """Redis ping via the shared async client."""
    try:
        from app.core.redis import get_async_redis

        r = await get_async_redis()
        pong = await r.ping()
        return HealthCheck(
            name="Redis",
            status="healthy" if pong else "degraded",
            detail="PONG" if pong else "no response",
        )
    except Exception as e:
        return HealthCheck(name="Redis", status="down", detail=str(e)[:140])


@router.get("/health", tags=TAGS, response_model=BackendHealthResponse)
async def get_backend_health(auth: AdminAuthDep):
    """Granular backend services health for the admin dashboard.

    Admin-only. Each check returns ``healthy`` / ``degraded`` / ``down`` /
    ``unknown``. The overall verdict is the worst individual status.

    Checks:
      * Supabase / Postgres connectivity
      * Trigram indexes (probed via ILIKE latency)
      * Celery worker liveness + queue depth
      * Redis ping
    """
    checks: List[HealthCheck] = []
    try:
        checks.extend(await _check_database())
        checks.append(await _check_trigram_via_latency())
        checks.append(await _check_dbos_engine())
        checks.append(await _check_redis())
    except Exception as e:
        logger.exception(f"Health check assembly failed: {e}")
        checks.append(
            HealthCheck(name="Health probe", status="down", detail=str(e)[:140])
        )

    rank = {"down": 3, "degraded": 2, "unknown": 1, "healthy": 0}
    worst = max((rank.get(c.status, 0) for c in checks), default=0)
    overall = {3: "down", 2: "degraded", 1: "degraded", 0: "healthy"}[worst]

    return BackendHealthResponse(overall=overall, checks=checks, timestamp=time.time())
