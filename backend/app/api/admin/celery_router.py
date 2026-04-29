"""Worker / queue monitoring API for admin dashboard.

PR-D7 phase 3: Celery is gone. Endpoints renamed in spirit
(workers/queues) but now report DBOS workflow + Redis state. Routes
preserved so the admin frontend doesn't 404 — payload shape kept
compatible.
"""

from __future__ import annotations

from fastapi import APIRouter
from loguru import logger

router = APIRouter()


@router.get("/workers")
async def get_celery_workers():
    """Return DBOS worker info. Single in-process 'worker' since DBOS
    runs in the FastAPI host process. Payload mirrors the legacy
    Celery-shape so the admin UI keeps rendering."""
    from app.services import dbos_orchestrator

    if not dbos_orchestrator.is_enabled():
        return {"online": 0, "total": 0, "workers": []}

    try:
        from dbos import DBOS

        running = DBOS.list_workflows(status="RUNNING") or []
    except Exception as e:
        logger.warning(f"DBOS workflow list failed: {e}")
        running = []

    workers = [
        {
            "name": "dbos@local",
            "status": "online",
            "active": len(running),
            "processed": None,  # DBOS doesn't track lifetime counts here
            "concurrency": 8,  # WORKFORCE_QUEUE_CONCURRENCY default
            "uptime": None,
        }
    ]
    return {"online": 1, "total": 1, "workers": workers}


@router.get("/queues")
async def get_celery_queues():
    """Return queue depths. PR-D7: there are no Celery queues. We
    report the DBOS `agent_workforce` queue depth instead, plus an
    empty list of legacy queue names for back-compat with the admin
    frontend that may still iterate them."""
    from app.services import dbos_orchestrator

    queues: list[dict] = []
    if dbos_orchestrator.is_enabled():
        try:
            from dbos import DBOS

            enqueued = (
                DBOS.list_workflows(queue_name="agent_workforce", status="ENQUEUED")
                or []
            )
            queues.append({"name": "agent_workforce", "messages": len(enqueued)})
        except Exception as e:
            logger.warning(f"DBOS queue depth read failed: {e}")
    return {"queues": queues}
