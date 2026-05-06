# backend/app/api/task_router.py

"""
Legacy task management routes — redirects to DBOS workflow API.

PR-D7 phase 3: Celery is gone. The legacy /tasks/* endpoints used to
hit `celery.result.AsyncResult` + `celery_app.control.inspect`. With
Celery removed, these routes return 410 Gone with a pointer to the
new DBOS workflow endpoints under /api/v1/workflows/.

The router is kept (not deleted from app/api/__init__.py) so that
older frontends or external scripts hitting these paths get a clear
410 instead of a 404 they might mistake for a network failure.

Migration map for callers:
    GET  /tasks/{id}          → GET  /workflows/{id}/status
    GET  /tasks/{id}/events   → GET  /workflows/{id}/events  (SSE)
    POST /tasks/{id}/cancel   → POST /workflows/{id}/cancel
    GET  /tasks/active        → /admin/workers (workers/queues split)
    GET  /tasks/stats         → /admin/workers
"""

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

from app.core.deps import AuthDep

router = APIRouter(prefix="/tasks")

TAGS = ["任务管理"]


class TaskStatusResponse(BaseModel):
    task_id: str
    status: str


def _gone(redirect_to: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_410_GONE,
        detail=(
            "The /tasks/* legacy Celery API has been removed in PR-D7. "
            f"Use {redirect_to} for equivalent functionality."
        ),
    )


@router.get("/{task_id}", tags=TAGS, response_model=TaskStatusResponse)
async def get_task_status(task_id: str, auth: AuthDep):
    raise _gone(f"GET /api/v1/workflows/{task_id}/status")


@router.get("/{task_id}/events", tags=TAGS)
async def get_task_events(task_id: str, auth: AuthDep):
    raise _gone(f"GET /api/v1/workflows/{task_id}/events (SSE)")


@router.post("/{task_id}/cancel", tags=TAGS)
async def cancel_task(task_id: str, auth: AuthDep):
    raise _gone(f"POST /api/v1/workflows/{task_id}/cancel")


@router.get("/active", tags=TAGS)
async def list_active_tasks(auth: AuthDep):
    raise _gone("GET /api/v1/admin/workers")


@router.get("/stats", tags=TAGS)
async def get_task_stats(auth: AuthDep):
    raise _gone("GET /api/v1/admin/workers")
