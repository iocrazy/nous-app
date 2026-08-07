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
    from app.repositories.episode_repository import get_episode_repository
    from app.repositories.project_stage_nodes_repository import (
        get_project_stage_nodes_repository,
    )
    from app.repositories.projects_repository import get_projects_repository

    repo = get_project_stage_nodes_repository()

    # B3: fan the template out into one node chain PER episode (atomic — see
    # ``instantiate_episode_chains``). ``episode_ids`` is every current episode
    # of the project; a project with none yet gets no chain now but still has
    # its binding stored below, so its first future episode instantiates one.
    episodes = await get_episode_repository().list_by_project(str(project_id))
    episode_ids = [str(e["id"]) for e in episodes]

    chains = await repo.instantiate_episode_chains(
        project_id,
        template_id,
        episode_ids,
        method=method,
        overrides=overrides,
        expect_fresh=expect_fresh,
    )

    # Store the project's workflow binding (mig 409) so a LATER-added episode
    # reuses the same template + method. Best-effort: the chains above are the
    # correctness-critical write; a failed binding only costs future episodes
    # their auto-chain (they can still be attached explicitly), it must not
    # fail project creation — same discipline as the arrival hooks below.
    try:
        await get_projects_repository().update_project(
            str(project_id),
            {
                "workflow_template_id": int(str(template_id)),
                "workflow_method": method,
            },
        )
    except Exception as exc:  # noqa: BLE001 — binding is enrichment, never fails create
        logger.error(
            f"[workflow] binding write failed for project {project_id} "
            f"template {template_id}: {exc!r}"
        )

    if not chains:
        return []

    all_nodes: List[Dict[str, Any]] = []
    for episode_id, nodes in chains.items():
        all_nodes.extend(nodes)
        await _fire_episode_arrival(
            project_id=str(project_id),
            episode_id=str(episode_id),
            nodes=nodes,
            user_id=str(user_id),
        )

    # M4 Autopilot (task O2, spec §2 trigger 2): a just-arrived group may
    # contain auto_start nodes. One project-level tick is enough — the tick
    # loops per episode internally (B2 T4). Best-effort, never fails create.
    try:
        from app.workflows.autopilot import enqueue_autopilot_tick

        await enqueue_autopilot_tick(str(project_id))
    except Exception as exc:  # noqa: BLE001 — enrichment only, never fails create
        logger.warning(
            f"[workflow] autopilot tick enqueue failed for project "
            f"{project_id}: {exc!r}"
        )

    return all_nodes


async def _fire_episode_arrival(
    *,
    project_id: str,
    episode_id: str,
    nodes: List[Dict[str, Any]],
    user_id: str,
) -> None:
    """Set one episode's cursor to its first active group and fire that group's
    arrival hooks (mirror issues, folders, notification, stage-hook prepare).

    Every hook is best-effort (each swallows its own errors or is documented
    never to raise), so a hiccup on one episode never fails instantiation or
    blocks the other episodes. Extracted so the fan-out loop above and the
    single-episode entry (``instantiate_single_episode_workflow``) share one
    arrival implementation.
    """
    from app.repositories.episode_repository import get_episode_repository
    from app.services.library.project_stage_issues import (
        ORIGIN_KIND,
        build_stage_origin_id,
        ensure_node_issues,
    )
    from app.services.workflow.node_folders import ensure_node_folders
    from app.services.workflow.stage_notifications import notify_stage_event
    from app.workflows.stage_hook import enqueue_stage_hook_dispatch

    group = _first_active_group(nodes)
    if not group:
        # Every node skipped — no active cursor, nothing to mirror.
        return

    await get_episode_repository().set_current_node_id(episode_id, group[0]["id"])
    # Arrival hooks (all best-effort, never raise): mirror issues, folders.
    await ensure_node_issues(int(project_id), group, user_id)
    await ensure_node_folders(project_id, group, user_id)

    # Best-effort arrival notification (E2).
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
            f"[workflow] arrival notification failed for project "
            f"{project_id} episode {episode_id}: {exc!r}"
        )

    # Best-effort stage-hook prepare (M3 PR-H2). enqueue_stage_hook_dispatch()
    # never raises (per-node try/except).
    await enqueue_stage_hook_dispatch(str(project_id), group)


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


async def instantiate_single_episode_workflow(
    project_id: str,
    episode_id: str,
    *,
    user_id: str,
) -> List[Dict[str, Any]]:
    """New-episode trigger (B3 §5): instantiate ONE newly added episode's chain
    from the project's stored binding, then fire that episode's arrival hooks.

    Returns ``[]`` (no-op) when the project has no workflow binding — the
    common case for a project created without a workflow. Reuses
    ``_fire_episode_arrival`` so a new episode arrives with exactly the same
    cursor + mirror-issue + folder + stage-hook treatment a fan-out episode
    gets.
    """
    from app.repositories.project_stage_nodes_repository import (
        get_project_stage_nodes_repository,
    )

    repo = get_project_stage_nodes_repository()
    nodes = await repo.instantiate_single_episode_chain(
        str(project_id), str(episode_id)
    )
    if not nodes:
        return []

    await _fire_episode_arrival(
        project_id=str(project_id),
        episode_id=str(episode_id),
        nodes=nodes,
        user_id=str(user_id),
    )

    try:
        from app.workflows.autopilot import enqueue_autopilot_tick

        await enqueue_autopilot_tick(str(project_id))
    except Exception as exc:  # noqa: BLE001 — enrichment only, never fails create
        logger.warning(
            f"[workflow] autopilot tick enqueue failed for project "
            f"{project_id}: {exc!r}"
        )

    return nodes


async def maybe_instantiate_episode_workflow(
    project_id: str,
    episode_id: str,
    *,
    user_id: str,
) -> None:
    """Episode-create entry point: instantiate the new episode's chain when the
    project has a workflow binding, else a no-op. Swallows all errors —
    enrichment, must never fail episode creation (same discipline as
    ``maybe_instantiate_project_workflow``)."""
    try:
        await instantiate_single_episode_workflow(
            project_id, episode_id, user_id=user_id
        )
    except Exception as exc:  # noqa: BLE001 — must never fail episode creation
        logger.error(
            f"[workflow] episode instantiation failed for project "
            f"{project_id} episode {episode_id}: {exc!r}"
        )
