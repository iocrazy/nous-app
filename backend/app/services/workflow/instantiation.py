"""Instantiate a project's workflow (M1 PR-B; B3 per-episode fan-out).

Copies a team template's nodes onto the project (``project_stage_nodes``) — one
node chain PER episode (B3) — sets each episode's ``episodes.current_node_id``
cursor to its first non-skipped group, and fires that group's arrival hooks so
its mirror issues appear in the Todolist.

All best-effort: a workflow hiccup must never fail project (or episode)
creation, and one episode's arrival failure must never block the others (same
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
        # Per-episode isolation: one episode's cursor/hook failure must never
        # block the others (the nodes are already committed atomically above;
        # arrival is best-effort enrichment).
        try:
            await _fire_episode_arrival(
                project_id=str(project_id),
                episode_id=str(episode_id),
                nodes=nodes,
                user_id=str(user_id),
            )
        except Exception as exc:  # noqa: BLE001 — per-episode best-effort
            logger.warning(
                f"[workflow] arrival failed for project {project_id} episode "
                f"{episode_id}: {exc!r}"
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


async def _close_legacy_mirror_issues(
    project_id: str, node_ids: List[Any]
) -> None:
    """Cancel the mirror issues of now-deleted legacy nodes (B3 backfill).

    Mirror issues reference their node by string ``origin_id`` (not an FK), so
    dropping the legacy nodes leaves their Todolist issues orphaned. Transition
    each non-terminal one to ``cancelled``. Entirely best-effort — a cleanup
    hiccup must never undo a successful conversion.
    """
    if not node_ids:
        return
    try:
        from app.repositories.issue_repository import get_issue_repository
        from app.services.library.project_stage_issues import (
            ORIGIN_KIND,
            build_stage_origin_id,
        )

        issues_repo = get_issue_repository()
        terminal = {"done", "cancelled"}
        for nid in node_ids:
            try:
                origin_id = build_stage_origin_id(project_id, nid)
                for issue in await issues_repo.list_by_origin(ORIGIN_KIND, origin_id):
                    if (
                        issue.get("id") is not None
                        and issue.get("status") not in terminal
                    ):
                        await issues_repo.transition_status(
                            int(issue["id"]), "cancelled"
                        )
            except Exception as exc:  # noqa: BLE001 — per-node best-effort
                logger.warning(
                    f"[workflow] legacy mirror-issue close failed for project "
                    f"{project_id} node {nid}: {exc!r}"
                )
    except Exception as exc:  # noqa: BLE001 — cleanup is enrichment only
        logger.warning(
            f"[workflow] legacy mirror-issue cleanup failed for project "
            f"{project_id}: {exc!r}"
        )


async def reinstantiate_project_per_episode(
    project_id: str,
    *,
    user_id: str,
    method: Optional[str] = None,
) -> Dict[str, Any]:
    """Ignite a sleeping LEGACY project (B3 backfill §6): atomically convert its
    project-level node chain into per-episode chains, store the binding, set
    each episode's cursor, fire arrival hooks, and cancel the old mirror issues.

    Idempotent: a project already per-episode (no legacy nodes) returns
    ``{"converted": False, ...}`` and touches nothing. The atomic delete +
    fan-out lives in ``reinstantiate_legacy_as_episodes`` (one transaction, no
    mixed NULL/non-NULL state); everything else here is best-effort enrichment
    layered on top, exactly like ``instantiate_project_workflow``.

    ``method`` overrides the value inferred from the legacy chain's skip state
    (spec §6/§10) — pass it when igniting a project whose original method the
    inference might not recover (e.g. hybrid).
    """
    from app.repositories.episode_repository import get_episode_repository
    from app.repositories.project_stage_nodes_repository import (
        get_project_stage_nodes_repository,
    )
    from app.repositories.projects_repository import get_projects_repository

    repo = get_project_stage_nodes_repository()

    template_id, inferred_method = await repo.infer_legacy_binding(str(project_id))
    if template_id is None:
        return {"converted": False, "reason": "no_legacy_chain"}

    method = method or inferred_method

    episodes = await get_episode_repository().list_by_project(str(project_id))
    episode_ids = [str(e["id"]) for e in episodes]
    if not episode_ids:
        return {"converted": False, "reason": "no_episodes"}

    # Capture the legacy node ids BEFORE the conversion deletes them, so their
    # orphaned mirror issues can be cancelled once the conversion succeeds.
    all_nodes = await repo.list_nodes(str(project_id))
    legacy_node_ids = [n["id"] for n in all_nodes if n.get("episode_id") is None]

    chains = await repo.reinstantiate_legacy_as_episodes(
        str(project_id),
        str(template_id),
        episode_ids,
        method=method,
    )

    # Store the binding + clear the now-stale project-level cursor. Best-effort
    # (same discipline as instantiate_project_workflow).
    try:
        await get_projects_repository().update_project(
            str(project_id),
            {
                "workflow_template_id": int(template_id),
                "workflow_method": method,
                "current_node_id": None,
            },
        )
    except Exception as exc:  # noqa: BLE001 — binding is enrichment
        logger.error(
            f"[workflow] binding write failed during reinstantiation for "
            f"project {project_id}: {exc!r}"
        )

    # Now that conversion committed, cancel the deleted legacy nodes' issues.
    await _close_legacy_mirror_issues(str(project_id), legacy_node_ids)

    node_count = 0
    for episode_id, nodes in chains.items():
        node_count += len(nodes)
        # Per-episode isolation (see instantiate_project_workflow): one
        # episode's arrival failure must not block the rest of the ignition.
        try:
            await _fire_episode_arrival(
                project_id=str(project_id),
                episode_id=str(episode_id),
                nodes=nodes,
                user_id=str(user_id),
            )
        except Exception as exc:  # noqa: BLE001 — per-episode best-effort
            logger.warning(
                f"[workflow] arrival failed during reinstantiation for project "
                f"{project_id} episode {episode_id}: {exc!r}"
            )

    try:
        from app.workflows.autopilot import enqueue_autopilot_tick

        await enqueue_autopilot_tick(str(project_id))
    except Exception as exc:  # noqa: BLE001 — enrichment only
        logger.warning(
            f"[workflow] autopilot tick enqueue failed for project "
            f"{project_id}: {exc!r}"
        )

    return {
        "converted": True,
        "episodes": len(chains),
        "nodes": node_count,
        "template_id": str(template_id),
        "method": method,
    }
