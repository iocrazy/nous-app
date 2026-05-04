"""lanes_router — admin snapshot of the per-process lane queue.

Backed by `app/services/lane_queue.py` (A9). Returns per-lane saturation
+ wait-time stats so the admin dashboard can spot when a specific lane
is saturated (e.g., background AI piling up while user lane is idle).

Process-local: each backend replica returns its own state. To see
cluster-wide saturation, frontend should query every replica.
"""
from __future__ import annotations

from typing import Any, Dict, List

from fastapi import APIRouter
from pydantic import BaseModel

from app.core.admin_deps import AdminAuthDep


router = APIRouter(prefix="/lanes", tags=["Lanes"])


class LaneStatus(BaseModel):
    name: str
    capacity: int
    in_flight: int
    queued: int
    total_acquired: int
    last_wait_ms: float
    saturation_pct: float


class LanesSnapshotResponse(BaseModel):
    lanes: List[LaneStatus]


@router.get("/snapshot", response_model=LanesSnapshotResponse)
async def lanes_snapshot(auth: AdminAuthDep) -> LanesSnapshotResponse:
    """Per-lane in-flight / queued / saturation. Admin-only."""
    from app.services.lane_queue import get_lane_queue

    return LanesSnapshotResponse(
        lanes=[LaneStatus(**s) for s in get_lane_queue().snapshot()]
    )


__all__ = ["router"]
