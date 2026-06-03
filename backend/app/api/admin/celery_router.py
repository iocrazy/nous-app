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


def _list_workflows(**kwargs):
    """List DBOS workflows, client-aware.

    Gateway-client prep (DORMANT): when the gateway DBOSClient handle is
    set, the synchronous list goes through the client; otherwise the
    in-process `DBOS.list_workflows` path is used (unchanged). The client
    is None everywhere today, so the existing branch is always taken —
    ZERO behavior change."""
    from app.services.infra.dbos_orchestrator import get_dbos_client

    client = get_dbos_client()
    if client is not None:
        return client.list_workflows(**kwargs)
    from dbos import DBOS

    return DBOS.list_workflows(**kwargs)


@router.get("/workers")
async def get_celery_workers():
    """Return DBOS worker info. Single in-process 'worker' since DBOS
    runs in the FastAPI host process. Payload mirrors the legacy
    Celery-shape so the admin UI keeps rendering."""
    from app.services.infra import dbos_orchestrator

    if not dbos_orchestrator.is_enabled():
        return {"online": 0, "total": 0, "workers": []}

    try:
        running = _list_workflows(status="RUNNING") or []
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
    from app.services.infra import dbos_orchestrator

    queues: list[dict] = []
    if dbos_orchestrator.is_enabled():
        try:
            enqueued = (
                _list_workflows(queue_name="agent_workforce", status="ENQUEUED") or []
            )
            queues.append({"name": "agent_workforce", "messages": len(enqueued)})
        except Exception as e:
            logger.warning(f"DBOS queue depth read failed: {e}")
    return {"queues": queues}
