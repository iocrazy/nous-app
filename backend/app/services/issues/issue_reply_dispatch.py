"""The one way to start ``respond_to_issue_reply`` under a pinned workflow id.

Moved out of ``issue_messages_router`` (phase 2b-2 Task 4). It had three
consumers already — the router's wake path, ``subissue_barrier.dispatch_wake``,
and now the shared ``inbox_or_dispatch`` — and two of them are services. A
service reaching back into an API router to call it is layering upside down,
and it dragged the whole FastAPI router module into the worker process; the
worker runs background sub-agents and has no business importing routers.

The router keeps the private alias it always had so its own tests and readers
still find the name there.
"""

from __future__ import annotations

from typing import Optional

from dbos import DBOS, SetWorkflowID

from app.workflows.issue_lifecycle import respond_to_issue_reply


def dispatch_respond_to_issue_reply(
    issue_id: int,
    owner_id: str,
    body: str,
    attachments: Optional[list],
    wf_id: str,
    source: Optional[dict] = None,
) -> None:
    """Dispatch the respond_to_issue_reply DBOS workflow under a pinned wf id.

    Client-aware (gateway→DBOSClient prep, currently DORMANT): when the gateway
    has constructed a DBOSClient, enqueue through it into the `dbos_dispatch`
    queue. Otherwise (client is None — today's reality) fall back to the
    in-process `SetWorkflowID + DBOS.start_workflow` path. Zero behavior change
    while the client stays None. Positional args preserved exactly:
    (issue_id, owner_id, body, attachments, source).

    ``source`` (Task 7a defect 6) is the reply text's provenance, carried to
    the workflow so the ONE user message it appends can say where it came
    from. Trailing and defaulted, so a workflow recovered from a row enqueued
    before this argument existed replays with ``None`` instead of failing.
    """
    from app.services.infra.dbos_orchestrator import (
        _resolve_pinned_app_version,
        get_dbos_client,
    )

    client = get_dbos_client()
    if client is not None:
        from dbos import EnqueueOptions

        opts: dict = {
            "workflow_name": "respond_to_issue_reply",
            "queue_name": "dbos_dispatch",
            "workflow_id": wf_id,
        }
        pinned = _resolve_pinned_app_version()
        if pinned:
            opts["app_version"] = pinned
        client.enqueue(
            EnqueueOptions(**opts), issue_id, owner_id, body, attachments, source
        )
        return

    with SetWorkflowID(wf_id):
        DBOS.start_workflow(
            respond_to_issue_reply,
            issue_id,
            owner_id,
            body,
            attachments,
            source,
        )


__all__ = ["dispatch_respond_to_issue_reply"]
