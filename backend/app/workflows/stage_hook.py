"""stage_hook_dispatch — prepares an agent run behind the confirm gate (M3
PR-H2, Project Workflow M3 spec §1).

A node arriving at the active cursor (forward advance / a project's first
group at creation) can carry ``events.prepare_agent_run: true`` alongside an
agent owner (mig 389, PR-H1). This hook is the arrival-side half of that
flag: it does NOT launch the agent run — it only tells the humans who could
confirm one ("Agent run ready") by reusing the node's mirror issue as the
link target, then stamps ``metadata.run_prepared_at`` so a duplicate arrival
(retry, re-enqueue, replay) is a no-op.

Discipline (never violate):
  * This workflow (and ``enqueue_stage_hook_dispatch``, its call-site helper)
    must NEVER reach a dispatch/execute entry point — an agent run is
    launched only when a human explicitly confirms it (H3/H4 territory).
    Tests assert this by monkeypatching the dispatch entry and asserting it
    is never called.
  * Best-effort throughout: a missing node / stale config / missing mirror
    issue / notify failure all degrade to a logged warning and a plain
    return — this hook must never raise into the arrival flow that
    triggered it (advance / project creation).
  * Recipients mirror ``stage_notifications.notify_stage_event``'s rule
    (owner_user_id + user members, deduped) minus the actor-exclusion step —
    a hook-prepared run has no acting user, so every human who could confirm
    it gets notified.
  * ``_stage_hook_dispatch_impl`` is a plain, undecorated async function so
    tests can drive it directly without a DBOS runtime (mirrors the
    ``run_backfill`` / thin-``@DBOS.workflow``-shell pattern in
    ``backfill_project_stage_issue_team_ids.py``); ``stage_hook_dispatch`` is
    the thin DBOS wrapper the dispatch bundle registers.
"""

from __future__ import annotations

import datetime
from typing import Any, Dict, List, Optional

from dbos import DBOS
from loguru import logger


async def _stage_hook_dispatch_impl(project_id: str, node_id: str) -> None:
    """Prepare (never dispatch) an agent run for one arrived node.

    Steps (spec §1 / task-H2 brief):
      1. Re-fetch the node and double-check ``events.prepare_agent_run`` AND
         ``owner_agent_id`` — guards a race where the node's config changed
         between enqueue and execution.
      2. ``metadata.run_prepared_at`` already set → idempotent no-op.
      3. Look up the node's mirror issue (best-effort empty on error) — no
         issue means nothing to link the notification to, so bail with a
         warning rather than notifying without a target.
      4. Notify the humans who could confirm the run.
      5. Stamp ``metadata.run_prepared_at`` so a duplicate arrival short-
         circuits at step 2.
    """
    from app.repositories.project_stage_nodes_repository import (
        get_project_stage_nodes_repository,
    )

    nodes_repo = get_project_stage_nodes_repository()
    node = await nodes_repo.get_node(node_id, project_id)
    if node is None:
        logger.warning(
            f"[stage_hook] node {node_id} not found for project {project_id} "
            "— skipping prepare"
        )
        return

    events = node.get("events") or {}
    if not events.get("prepare_agent_run") or not node.get("owner_agent_id"):
        # Config changed after enqueue (flag toggled off, owner reassigned to
        # a human) — nothing to prepare any more.
        return

    metadata = node.get("metadata") or {}
    if metadata.get("run_prepared_at"):
        return  # already prepared — idempotent

    identifier = await _mirror_issue_identifier(project_id, node_id)
    if identifier is None:
        logger.warning(
            f"[stage_hook] no mirror issue for project {project_id} node "
            f"{node_id} — skipping prepare"
        )
        return

    await _notify_prepared(node, identifier)

    await nodes_repo.set_node_metadata(
        node_id,
        {"run_prepared_at": datetime.datetime.now(datetime.timezone.utc).isoformat()},
    )


async def _mirror_issue_identifier(project_id: str, node_id: str) -> Optional[str]:
    """The node's mirror issue human identifier (e.g. ``MH-42``), or None —
    best-effort, never raises (a missing/unreadable mirror reads as "none")."""
    from app.repositories.issue_repository import get_issue_repository
    from app.services.library.project_stage_issues import (
        ORIGIN_KIND,
        build_stage_origin_id,
    )

    origin_id = build_stage_origin_id(project_id, node_id)
    try:
        issues = await get_issue_repository().list_by_origin(ORIGIN_KIND, origin_id)
    except Exception as exc:  # noqa: BLE001 — a missing mirror reads as "none"
        logger.warning(
            f"[stage_hook] mirror lookup failed for project {project_id} node "
            f"{node_id}: {exc!r}"
        )
        return None
    for issue in issues:
        identifier = issue.get("identifier")
        if identifier:
            return str(identifier)
    return None


async def _notify_prepared(node: Dict[str, Any], identifier: str) -> None:
    """Notify the humans who could confirm this node's agent run.

    Recipients: owner_user_id + user members, deduped (same shape as
    ``stage_notifications.notify_stage_event``) — no actor to exclude here,
    since a hook-prepared run has no acting user. An empty recipient set
    (e.g. an agent owner with no user members) is a silent no-op, never a
    fallback ping to some other party.
    """
    from app.services.notifications import notify

    recipients: set[str] = set()
    owner_user_id = node.get("owner_user_id")
    if owner_user_id:
        recipients.add(str(owner_user_id))
    for member in node.get("members") or []:
        member_user_id = member.get("user_id")
        if member_user_id:
            recipients.add(str(member_user_id))
    if not recipients:
        return

    node_name = node.get("name") or "Stage"
    title = f'Agent run ready — "{node_name}"'
    for user_id in recipients:
        await notify(
            user_id,
            "workflow_stage",
            title,
            body=None,
            link_kind="issue",
            link_id=identifier,
        )


@DBOS.workflow()
async def stage_hook_dispatch(project_id: str, node_id: str) -> None:
    """Thin DBOS shell over ``_stage_hook_dispatch_impl`` — see module
    docstring. Recommended workflow_id: ``f"stage-hook-{project_id}-{node_id}"``
    so a duplicate enqueue for the same node short-circuits via DBOS replay,
    on top of the ``run_prepared_at`` idempotency the impl itself carries."""
    await _stage_hook_dispatch_impl(project_id, node_id)


async def enqueue_stage_hook_dispatch(
    project_id: str, nodes: List[Dict[str, Any]]
) -> None:
    """Best-effort enqueue of ``stage_hook_dispatch`` for each agent-owned,
    ``prepare_agent_run``-enabled node in a just-arrived group.

    Called from the same two arrival sites as ``notify_stage_event``
    (``advance_service.execute_advance``'s forward branch,
    ``instantiation.instantiate_project_workflow``'s first group). Never
    raises — one node's enqueue failure never blocks the others or the
    arrival flow itself; the workflow re-checks the same two gates
    (``owner_agent_id`` / ``events.prepare_agent_run``) on execution to
    guard a race where config changed in between.
    """
    for node in nodes:
        if node.get("skipped"):
            continue
        if not node.get("owner_agent_id"):
            continue
        events = node.get("events") or {}
        if not events.get("prepare_agent_run"):
            continue
        node_id = node.get("id")
        if node_id is None:
            continue
        try:
            from app.services.infra.dbos_orchestrator import start_workflow_routed

            await start_workflow_routed(
                "stage_hook_dispatch",
                dbos_workflow_callable=stage_hook_dispatch,
                dbos_workflow_kwargs={
                    "project_id": str(project_id),
                    "node_id": str(node_id),
                },
                workflow_id=f"stage-hook-{project_id}-{node_id}",
            )
        except Exception as exc:  # noqa: BLE001 — best-effort, never blocks arrival
            logger.warning(
                f"[stage_hook] enqueue failed for project {project_id} node "
                f"{node_id}: {exc!r}"
            )
