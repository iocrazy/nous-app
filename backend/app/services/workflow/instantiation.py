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


async def project_has_workflow_nodes(project_id: Any) -> bool:
    """Whether a project owns workflow node instances (W2-1 suppression signal).

    A project carrying live ``project_stage_nodes`` rows mirrors its node chain
    (``ensure_node_issues``) and drives its stage display from those nodes, so
    the legacy global-SOP mirror / forward-derivation must stand down for it.

    Fail-OPEN: if the probe itself errors (engine unconfigured in a unit test,
    transient DB blip) this returns ``False`` so a non-workflow project keeps its
    legacy behaviour rather than silently losing its SOP mirror.
    """
    try:
        from app.repositories.project_stage_nodes_repository import (
            get_project_stage_nodes_repository,
        )

        return await get_project_stage_nodes_repository().has_nodes(str(project_id))
    except Exception as exc:  # noqa: BLE001 — degrade to legacy SOP behaviour
        logger.warning(
            f"[workflow] has-workflow probe failed for project {project_id}: {exc!r}"
        )
        return False


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
) -> List[Dict[str, Any]]:
    """Instantiate + set the cursor + open the first group's mirror issues.

    Returns the instantiated node list (empty on No-workflow / empty template).
    """
    from app.repositories.project_stage_nodes_repository import (
        get_project_stage_nodes_repository,
    )
    from app.services.library.project_stage_issues import ensure_node_issues
    from app.services.workflow.node_folders import ensure_node_folders

    repo = get_project_stage_nodes_repository()
    nodes = await repo.instantiate_from_template(
        project_id, template_id, method=method, overrides=overrides
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
