"""Instantiate a project's workflow at creation time (M1 PR-B).

Copies a team template's nodes onto the project (``project_stage_nodes``), sets
the ``projects.current_node_id`` cursor to the first non-skipped group, and
fires the arrival hook so that group's mirror issues appear in the Todolist.

All best-effort: a workflow hiccup must never fail project creation (same
discipline as the born-on-first-stage / default-episode enrichment blocks in
``ProjectsService.create_project``).
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from loguru import logger


def _first_active_group(nodes: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """The earliest non-skipped group (by sort_order).

    The first non-skipped node anchors the group; if it shares a
    ``parallel_group`` the group is every non-skipped node with that value.
    Returns [] when every node is skipped.
    """
    active = [n for n in nodes if not n.get("skipped")]
    if not active:
        return []
    active.sort(key=lambda n: n.get("sort_order", 0))
    head = active[0]
    pg = head.get("parallel_group")
    if pg is None:
        return [head]
    return [n for n in active if n.get("parallel_group") == pg]


async def instantiate_project_workflow(
    project_id: str,
    template_id: str,
    *,
    method: Optional[str] = None,
    overrides: Optional[List[Dict[str, Any]]] = None,
    user_id: str,
    expect_fresh: bool = False,
) -> List[Dict[str, Any]]:
    """Instantiate + set the cursor + open the first group's mirror issues.

    Returns the instantiated node list (empty on No-workflow / empty template).

    ``expect_fresh`` threads straight through to
    ``instantiate_from_template`` — default False keeps the create-project
    path's historical "idempotent, never raise" behavior; the M1.x attach-
    workflow endpoint passes True so a losing concurrent race (or a genuine
    already-has-a-workflow project) raises ``WorkflowAlreadyInstantiated``
    instead of silently no-op-succeeding. This function does not catch that
    exception — it propagates to the caller (the router maps it to 409;
    ``maybe_instantiate_project_workflow`` below never sets ``expect_fresh``
    so it never sees it).
    """
    from app.repositories.project_stage_nodes_repository import (
        get_project_stage_nodes_repository,
    )
    from app.services.library.project_stage_issues import (
        ORIGIN_KIND,
        build_stage_origin_id,
        ensure_node_issues,
    )
    from app.services.workflow.node_folders import ensure_node_folders
    from app.services.workflow.stage_notifications import notify_stage_event

    repo = get_project_stage_nodes_repository()
    nodes = await repo.instantiate_from_template(
        project_id,
        template_id,
        method=method,
        overrides=overrides,
        expect_fresh=expect_fresh,
    )
    if not nodes:
        return []

    group = _first_active_group(nodes)
    if not group:
        # Every node skipped — no active cursor, nothing to mirror.
        return nodes

    await repo.set_current_node_id(project_id, group[0]["id"])
    # Arrival hooks for the first active group (both best-effort, never raise):
    # the mirror issues, then the deliverable folders.
    await ensure_node_issues(int(str(project_id)), group, user_id)
    await ensure_node_folders(project_id, group, user_id)

    # Best-effort arrival notification (E2) for the project's first active
    # group — same discipline as the two hooks above; a hiccup here must never
    # fail project creation.
    try:
        from app.repositories.issue_repository import get_issue_repository
        from app.repositories.projects_repository import get_projects_repository

        project = await get_projects_repository().get_project_by_id(str(project_id))
        project_name = (project or {}).get("name") or "Project"
        team_id = (project or {}).get("team_id")
        issues_repo = get_issue_repository()
        for node in group:
            issue_identifier: Optional[str] = None
            try:
                origin_id = build_stage_origin_id(project_id, node["id"])
                for issue in await issues_repo.list_by_origin(ORIGIN_KIND, origin_id):
                    if issue.get("identifier"):
                        issue_identifier = str(issue["identifier"])
                        break
            except Exception as exc:  # noqa: BLE001 — link is enrichment only
                logger.warning(
                    f"[workflow] mirror-issue lookup failed for project "
                    f"{project_id} node {node.get('id')}: {exc!r}"
                )
            await notify_stage_event(
                event="arrival",
                project_id=str(project_id),
                project_name=project_name,
                node=node,
                issue_identifier=issue_identifier,
                team_id=team_id,
                actor_user_id=str(user_id),
            )
    except Exception as exc:  # noqa: BLE001 — notification is enrichment only
        logger.warning(
            f"[workflow] first-group arrival notification failed for project "
            f"{project_id}: {exc!r}"
        )

    # Best-effort stage-hook prepare (M3 PR-H2) for the project's first active
    # group — same discipline as the arrival notification above.
    # enqueue_stage_hook_dispatch() never raises (per-node try/except), so a
    # hiccup here must never fail project creation.
    from app.workflows.stage_hook import enqueue_stage_hook_dispatch

    await enqueue_stage_hook_dispatch(str(project_id), group)

    # M4 Autopilot (task O2, spec §2 trigger 2): the project's first active
    # group may itself contain auto_start nodes — best-effort tail enqueue,
    # same discipline as the hook above; a hiccup here must never fail
    # project creation.
    try:
        from app.workflows.autopilot import enqueue_autopilot_tick

        await enqueue_autopilot_tick(str(project_id))
    except Exception as exc:  # noqa: BLE001 — enrichment only, never fails create
        logger.warning(
            f"[workflow] autopilot tick enqueue failed for project "
            f"{project_id}: {exc!r}"
        )

    return nodes


async def maybe_instantiate_project_workflow(
    project_id: str,
    template_id: Optional[str],
    *,
    method: Optional[str] = None,
    user_id: str,
) -> None:
    """Create-path entry point: instantiate when a template was chosen, else
    a No-workflow no-op. Swallows all errors — enrichment, not core create."""
    if not template_id:
        return
    try:
        await instantiate_project_workflow(
            project_id, template_id, method=method, user_id=user_id
        )
    except Exception as exc:  # noqa: BLE001 — workflow init must never fail create
        logger.error(
            f"[workflow] instantiation failed for project {project_id} "
            f"template {template_id}: {exc!r}"
        )
