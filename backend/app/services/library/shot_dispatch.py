"""Dispatch one ``index_shots`` run (a Task Center task + its DBOS workflow).

The one place that creates the task row and starts the workflow, shared by
the Shots tab / Settings backfill endpoints (``ai_router``), the download
chain (``download_helpers.maybe_chain_index_shots``) and the backfill
sweeper (``workflows.shots_backfill_sweep``).

Must be awaited from a workflow BODY or a request handler, never from inside
a ``@DBOS.step`` — ``start_workflow_routed`` asserts there (same rule as
``download.chain_followups_step``).
"""

from __future__ import annotations

import uuid
from typing import Optional

from loguru import logger


async def dispatch_index_shots(
    *, user_id: str, resource_id: str, title: str, flow_id: Optional[str]
) -> str:
    """Create the ``index_shots`` task row and start its workflow; the row is
    failed (``DISPATCH_ERROR``) when the start itself fails so nothing sits
    queued forever. Returns the workflow id (= task id)."""
    from app.services.infra.dbos_orchestrator import start_workflow_routed
    from app.services.infra.unified_task_manager import get_task_manager
    from app.workflows.index_shots import TASK_TYPE as INDEX_SHOTS_TASK_TYPE
    from app.workflows.index_shots import index_shots_workflow

    manager = get_task_manager()
    wf_id = str(uuid.uuid4())
    await manager.create(
        user_id=user_id,
        task_type=INDEX_SHOTS_TASK_TYPE,
        title=f"Index shots · {title}"[:200],
        subtitle="Queued",
        resource_id=str(resource_id),
        dbos_workflow_id=wf_id,
        flow_id=flow_id,
        metadata={"shots": {"resource_id": str(resource_id)}},
    )
    try:
        await start_workflow_routed(
            INDEX_SHOTS_TASK_TYPE,
            dbos_workflow_callable=index_shots_workflow,
            dbos_workflow_kwargs={"resource_id": str(resource_id), "user_id": user_id},
            workflow_id=wf_id,
        )
    except Exception as e:
        try:
            await manager.fail(
                wf_id, f"Dispatch failed: {str(e)[:180]}", error_code="DISPATCH_ERROR"
            )
        except Exception as fail_err:  # noqa: BLE001
            logger.error(f"index-shots: could not fail orphan task {wf_id}: {fail_err}")
        raise
    return wf_id


__all__ = ["dispatch_index_shots"]
