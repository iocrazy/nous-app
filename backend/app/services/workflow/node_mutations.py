"""Instance node add/delete for a project workflow (W3-1).

Adding a node copies a node-bank stage (or a blank name) into the live
``project_stage_nodes`` chain. Deleting one is fenced by three guards so a
removal can never desync the execution mirror or the active cursor:

  * the node must still be ``pending`` — a started / reviewed / done node
    carries history (skip, don't delete; skip ≠ delete per spec §5);
  * it must carry no mirror issue (``list_by_origin`` empty) — a node with a
    live issue is an execution fact, not a plan edit;
  * it must not belong to the current active group — you cannot delete what the
    workspace is standing on.

Each failed guard raises ``NodeDeleteBlocked`` with a machine reason the router
maps to 409. Dependencies resolve through the ``get_*_repository`` seams so the
unit tests fake every branch without a database (mirrors ``advance_service``).
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from loguru import logger

from app.schemas.workflow import (
    DELETE_BLOCK_ACTIVE,
    DELETE_BLOCK_HAS_ISSUE,
    DELETE_BLOCK_NOT_PENDING,
    NodeDeleteBlocked,
)
from app.services.library.project_stage_issues import (
    ORIGIN_KIND,
    build_stage_origin_id,
)


async def add_project_node(
    project_id: str,
    *,
    source_stage_id: Optional[str] = None,
    name: Optional[str] = None,
    sort_order: int,
    parallel_group: Optional[int] = None,
) -> Dict[str, Any]:
    """Add one node to a live instance (from the bank or blank). Returns the
    created node row."""
    from app.repositories.project_stage_nodes_repository import (
        get_project_stage_nodes_repository,
    )

    repo = get_project_stage_nodes_repository()

    # B3: add_node stamps no episode_id (→ NULL). On a per-episode project that
    # NULL node would silently drop out of every episode's advance/autopilot
    # (B2 selects the per-episode path once ANY node is episode-scoped, and
    # list_nodes_by_episode excludes NULL nodes) — the silent-stall state the
    # all-or-nothing constraint forbids. Refuse loudly instead. Per-episode
    # stage editing is B4's job; the router maps this ValueError to 422.
    if await repo.has_episode_scoped_nodes(str(project_id)):
        raise ValueError(
            "cannot add a project-level stage to a per-episode workflow "
            "project — per-episode stage editing lands in B4"
        )

    return await repo.add_node(
        str(project_id),
        source_stage_id=source_stage_id,
        name=name,
        sort_order=sort_order,
        parallel_group=parallel_group,
    )


async def delete_project_node(project_id: str, node_id: str) -> bool:
    """Delete a node after clearing all three removal guards.

    Returns True on delete, None-equivalent False only if the row vanished
    between the guard read and the delete (treated as already-gone). Raises
    ``NodeDeleteBlocked`` (→ 409) or ``LookupError`` (→ 404, node not in
    project).
    """
    from app.repositories.issue_repository import get_issue_repository
    from app.repositories.project_stage_nodes_repository import (
        get_project_stage_nodes_repository,
    )

    repo = get_project_stage_nodes_repository()
    node = await repo.get_node(str(node_id), str(project_id))
    if node is None:
        raise LookupError("node not found")

    # Guard 1: only a still-pending node is a plan edit; anything further along
    # carries execution history (skip it instead — skip ≠ delete).
    if node.get("status") != "pending":
        raise NodeDeleteBlocked(DELETE_BLOCK_NOT_PENDING)

    # Guard 2: a node the workspace is standing on cannot be removed.
    active = await repo.get_active_group(str(project_id))
    if any(str(n["id"]) == str(node_id) for n in active):
        raise NodeDeleteBlocked(DELETE_BLOCK_ACTIVE)

    # Guard 3: a node with a live mirror issue is an execution fact, not a plan.
    origin_id = build_stage_origin_id(project_id, node_id)
    try:
        mirror = await get_issue_repository().list_by_origin(ORIGIN_KIND, origin_id)
    except Exception as exc:  # noqa: BLE001 — fail safe: refuse when we can't tell
        logger.warning(
            f"[node_mutations] mirror lookup failed for project {project_id} "
            f"node {node_id}: {exc!r} — refusing delete"
        )
        raise NodeDeleteBlocked(DELETE_BLOCK_HAS_ISSUE) from exc
    if mirror:
        raise NodeDeleteBlocked(DELETE_BLOCK_HAS_ISSUE)

    return await repo.delete_node(str(node_id), str(project_id))
