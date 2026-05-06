"""Liveness + readiness endpoints.

Distinct from the existing `/health` family:

* `/api/v1/healthz` — **liveness**. 200 the instant lifespan reaches
  `yield`. No DB calls, no external probes. If you can hit this and get
  200, the FastAPI worker is alive and accepting connections; whatever
  your client problem is, it isn't "the backend died".

* `/api/v1/readyz` — **readiness**. 200 only when every background task
  spawned during lifespan has finished. Used by load balancers / orchestrators
  that want to delay routing traffic until first-time setup (seed loader,
  schema probe, etc.) settles. Returns 503 + per-task status while bg work
  is still pending so dashboards can see what's holding it up.

The split lets the dev supervisor distinguish "starting up — wait" from
"wedged — kill". Matches k8s probe semantics.
"""
from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, Request, Response, status

router = APIRouter(tags=["Health"])


@router.get("/healthz", summary="Liveness probe (always 200 if process is alive)")
async def healthz() -> Dict[str, str]:
    """Always returns 200 with `{"status": "alive"}`.

    No DB, no external calls. The point is that *if you got a response*,
    lifespan completed and the worker is accepting connections.
    """
    return {"status": "alive"}


@router.get("/readyz", summary="Readiness probe (200 once bg startup settles)")
async def readyz(request: Request, response: Response) -> Dict[str, Any]:
    """200 once every BackgroundTaskRegistry task is done; 503 otherwise.

    Payload includes per-task `done`/`duration_seconds`/`error` so it's
    useful both as a probe target and a debug endpoint.
    """
    registry = getattr(request.app.state, "bg_tasks", None)
    if registry is None:
        # Lifespan didn't run / aborted — fail loud.
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return {
            "status": "not_ready",
            "reason": "background task registry missing on app.state",
            "tasks": [],
        }

    snapshot = registry.status_snapshot()
    all_done = registry.all_done()
    if not all_done:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return {
        "status": "ready" if all_done else "starting",
        "tasks": snapshot,
    }
