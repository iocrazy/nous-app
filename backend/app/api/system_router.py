# backend/app/api/system_router.py

"""
System Status Monitoring API

Provides queue status, disk space, and network status.
Now delegates to system_monitor_service for shared logic.
"""

import time

from fastapi import APIRouter, HTTPException
from loguru import logger
from pydantic import BaseModel

from app.core.deps import AuthDep
from app.services.system_monitor_service import (
    get_active_tasks,
    get_network_status,
    get_queue_status,
    get_storage_status,
    get_worker_stats,
)

router = APIRouter(prefix="/system")

TAGS = ["系统监控"]


class QueueStatus(BaseModel):
    active: int = 0
    pending: int = 0
    scheduled: int = 0
    status: str = "offline"


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
        queue_data = get_queue_status()
        storage_data = get_storage_status()
        network_data = get_network_status()

        return SystemStatusResponse(
            queue=QueueStatus(**queue_data),
            storage=StorageStatus(**storage_data),
            network=NetworkStatus(**network_data),
            timestamp=time.time(),
        )

    except Exception as e:
        logger.error(f"Failed to get system status: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to get system status: {str(e)}")
