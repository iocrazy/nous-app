"""Shared "start this node now" helper (M4 Autopilot, task O2).

One node's transition out of ``pending`` — ensure its mirror issue exists,
move that issue ``todo`` → ``in_progress`` (which the issue→node status hook,
``issue_repository._fire_stage_node_sync``, projects back onto the node), then
either dispatch its agent owner or hand it to the M3 confirm-gate "upshot"
path (``stage_hook.prepare_agent_run``).

Two callers share this EXACT behavior (never a parallel re-implementation):
  * ``app.workflows.autopilot._autopilot_tick_impl`` — the auto-start step
    (design spec §2 step 2), with ``actor_user_id=None`` (a system action) and
    ``dispatch`` decided by the daily quota.
  * ``POST /projects/{id}/workflow/nodes/{node_id}/start-early`` — the manual
    "先行开工" endpoint (task-O2 brief), with the calling user as
    ``actor_user_id`` and ``dispatch=False`` ALWAYS (an agent owner still
    goes through the M3 confirm gate — starting early never bypasses it).

Best-effort throughout, matching every other arrival-hook module in this
package (``stage_hook`` / ``stage_notifications``): a failure here must never
raise into the tick / advance / request flow that triggered it.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from loguru import logger

# Issue statuses that mean "already started or past that point" — a node
# whose mirror issue is in one of these needs no further transition.
_ALREADY_STARTED = frozenset({"in_progress", "in_review", "done", "cancelled"})

# Review fix I4: a node whose mirror issue is cancelled or done must NEVER be
# (re)started — `_ISSUE_TO_NODE_STATUS` (issue_repository.py) projects a
# cancelled mirror back onto the node as `status='pending'` (there is no
# node-level "cancelled" state), which makes a user-cancelled node look
# indistinguishable from a genuinely-not-started one to the auto-start
# eligibility check (`status == 'pending'`). Without this guard, the next
# sweep/tick would see it as a fresh auto_start candidate and re-dispatch —
# silently overriding the human's cancellation. `done` is guarded too
# defensively (should never reach here via the 'pending' projection, but a
# resurrected done node would be equally wrong).
_NEVER_RESURRECT = frozenset({"cancelled", "done"})


async def _mirror_issues(project_id: str, node_id: str) -> list[Dict[str, Any]]:
    """The mirror issues for one node's origin (best-effort empty on error) —
    mirrors ``advance_service._mirror_issues`` / ``stage_hook``'s lookup."""
    from app.repositories.issue_repository import get_issue_repository
    from app.services.library.project_stage_issues import (
        ORIGIN_KIND,
        build_stage_origin_id,
    )

    origin_id = build_stage_origin_id(project_id, node_id)
    try:
        return await get_issue_repository().list_by_origin(ORIGIN_KIND, origin_id)
    except Exception as exc:  # noqa: BLE001 — a missing mirror reads as "none"
        logger.warning(
            f"[node_start] mirror lookup failed for project {project_id} node "
            f"{node_id}: {exc!r}"
        )
        return []


async def _open_mirror_issue(project_id: str, node_id: str) -> Optional[Dict[str, Any]]:
    """The node's mirror issue, or None. ``ensure_stage_issue`` guarantees at
    most one mirror per origin (idempotent create), so the first hit is the
    only one there ever is."""
    issues = await _mirror_issues(project_id, node_id)
    return issues[0] if issues else None


async def start_node_now(
    project_id: str,
    node: Dict[str, Any],
    *,
    actor_user_id: Optional[str],
    dispatch: bool,
    dispatch_auto: bool = False,
    prepare_title: Optional[str] = None,
) -> Dict[str, Any]:
    """Start one node ahead of the normal cursor flow. Returns the refreshed
    node dict (best-effort — the input ``node`` is returned unchanged if the
    refresh read fails).

    ``actor_user_id`` is the acting human, or ``None`` for a system/autopilot
    action (falls back to the project owner so the mirror issue's
    creator-required CHECK is still satisfied — same fallback
    ``project_stage_issues._resolve_issue_team_id`` uses for team_id).

    ``dispatch``: when True AND the node has an agent owner, actually start
    the agent run (marked ``dispatch_auto`` for the quota counter). When
    False, an agent-owned node is instead routed through
    ``stage_hook.prepare_agent_run`` (M3 confirm gate) — never dispatched;
    ``prepare_title`` lets the caller substitute distinct copy for that path
    (autopilot's quota-exceeded branch uses "Autopilot paused: daily limit
    reached" instead of the default "Agent run ready"). A node with no agent
    owner is simply started; there is nothing else to do (a human works it
    manually).
    """
    from app.repositories.project_stage_nodes_repository import (
        get_project_stage_nodes_repository,
    )
    from app.repositories.projects_repository import get_projects_repository
    from app.services.library.project_stage_issues import ensure_node_issues
    from app.services.workflow.node_folders import ensure_node_folders
    from app.services.workflow.stage_notifications import notify_stage_event

    node_id = str(node["id"])
    project = await get_projects_repository().get_project_by_id(int(str(project_id)))
    project_name = (project or {}).get("name") or "Project"
    team_id = (project or {}).get("team_id")

    creator_user_id = actor_user_id or (
        str(project["owner_id"]) if project and project.get("owner_id") else None
    )
    if creator_user_id is None:
        logger.warning(
            f"[node_start] no user_id available (actor or project owner) for "
            f"project {project_id} node {node_id} — skipping start"
        )
        return node

    # Idempotent: a mirror issue may already exist from a prior attempt.
    await ensure_node_issues(int(str(project_id)), [node], creator_user_id)
    await ensure_node_folders(str(project_id), [node], creator_user_id)

    issue = await _open_mirror_issue(project_id, node_id)
    if issue is None:
        logger.warning(
            f"[node_start] no mirror issue for project {project_id} node "
            f"{node_id} after ensure_node_issues — skipping start"
        )
        return node

    if issue.get("status") in _NEVER_RESURRECT:
        # Cancelled (or already-done) — refuse to resurrect: no transition,
        # no dispatch, no notify. See _NEVER_RESURRECT's docstring (I4).
        logger.info(
            f"[node_start] node {node_id} project {project_id} mirror issue "
            f"is {issue.get('status')!r} — refusing to (re)start"
        )
        return node

    from app.repositories.issue_repository import get_issue_repository

    issues_repo = get_issue_repository()
    if issue.get("status") not in _ALREADY_STARTED:
        await issues_repo.transition_status(int(issue["id"]), "in_progress")
        identifier = issue.get("identifier")
        await notify_stage_event(
            event="arrival",
            project_id=str(project_id),
            project_name=project_name,
            node=node,
            issue_identifier=str(identifier) if identifier else None,
            team_id=team_id,
            actor_user_id=actor_user_id,
        )

    if node.get("owner_agent_id"):
        if dispatch:
            await _dispatch_node(int(issue["id"]), auto=dispatch_auto)
        else:
            from app.workflows.stage_hook import prepare_agent_run

            # Re-fetch so prepare_agent_run's idempotency stamp check sees the
            # freshest metadata (this call may run moments after the issue
            # transition above updated the node's projected status).
            nodes_repo = get_project_stage_nodes_repository()
            fresh = await nodes_repo.get_node(node_id, project_id) or node
            await prepare_agent_run(
                str(project_id), node_id, fresh, title=prepare_title
            )

    nodes_repo = get_project_stage_nodes_repository()
    refreshed = await nodes_repo.get_node(node_id, project_id)
    return refreshed if refreshed is not None else node


async def _dispatch_node(issue_id: int, *, auto: bool) -> None:
    """Kick off the confirm-gate dispatch for one mirror issue's execute_issue
    workflow — the SAME seam ``POST /issues/{id}/dispatch`` uses (#1400
    discipline: never a parallel dispatch path). ``auto`` marks the run so
    the quota counter (``agent_runs.trigger == 'issue_dispatch_auto'``) can
    tell it apart from a manual "Run now".

    That shared seam is ``_dispatch_execute_issue``, not the endpoint's
    ``start_execute_issue`` wrapper — this path builds its own workflow_id and
    skips the wrapper's persist step. The phase 2b-2 ``dispatching`` marker
    therefore lives on ``_dispatch_execute_issue`` itself, which is why an
    autopilot dispatch is guarded against a concurrent fork exactly as a
    manual one is."""
    import uuid as _uuid

    from app.api.issues_router import _dispatch_execute_issue

    workflow_id = f"issue-{issue_id}-{_uuid.uuid4().hex[:12]}"
    try:
        await _dispatch_execute_issue(issue_id, workflow_id, auto=auto)
    except Exception as exc:  # noqa: BLE001 — duplicate workflow_id is a soft success
        low = repr(exc).lower()
        if "already exists" not in low and "duplicate" not in low:
            logger.warning(
                f"[node_start] dispatch failed for issue {issue_id}: {exc!r}"
            )
