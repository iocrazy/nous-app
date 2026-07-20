"""Lazy stage-folder materialization for workflow nodes (M2-W1).

A workflow node's filed deliverables live in a project folder named after the
node (spec §2: "阶段文件夹，文件夹名=节点名"). The link is
``project_stage_nodes.folder_id`` (mig 383). Rather than pre-creating a folder
for every node at instantiation, we materialize one lazily when a node is
*arrived at* (project creation's first group, or an advance's next/prev group)
so a No-workflow-ish project never accretes empty folders.

``ensure_node_folder`` is idempotent and best-effort:
  * a node that already carries ``folder_id`` is returned as-is;
  * otherwise a root-level folder whose name equals the node name is reused
    (case-insensitive) — the user may have made it by hand;
  * failing that, a new root folder is created;
  * the id is backfilled onto the node.

Any failure degrades to ``None`` (a warning, never a raise) — the deliverable
predicate then falls back to its name-match heuristic, and the arrival that
triggered this is never blocked.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from loguru import logger


async def ensure_node_folder(
    project_id: str, node: Dict[str, Any], user_id: Optional[str]
) -> Optional[str]:
    """Return the node's deliverable-folder id, materializing it if needed.

    Best-effort: returns None (with a warning) on any error, and on a node that
    already carries a ``folder_id`` returns that id without touching the store.
    """
    existing = node.get("folder_id")
    if existing:
        return str(existing)

    node_name = (node.get("name") or "").strip()
    node_id = node.get("id")
    if not node_name or node_id is None:
        return None

    from app.repositories.project_stage_nodes_repository import (
        get_project_stage_nodes_repository,
    )
    from app.repositories.projects_repository import get_projects_repository

    projects_repo = get_projects_repository()
    nodes_repo = get_project_stage_nodes_repository()
    try:
        # Reuse a hand-made root folder of the same name before creating one.
        folders = await projects_repo.get_folders(str(project_id))
        match = next(
            (
                f
                for f in folders
                if (f.get("name") or "").strip().lower() == node_name.lower()
            ),
            None,
        )
        if match is not None:
            folder_id = str(match["id"])
        else:
            created = await projects_repo.create_folder(
                {
                    "project_id": str(project_id),
                    "name": node_name,
                    "created_by": str(user_id) if user_id else None,
                }
            )
            folder_id = str(created["id"])

        await nodes_repo.set_node_folder_id(str(node_id), folder_id)
        return folder_id
    except Exception as exc:  # noqa: BLE001 — folder link is enrichment, never blocks
        logger.warning(
            f"[node_folders] ensure folder failed for project {project_id} "
            f"node {node_id}: {exc!r}"
        )
        return None


async def ensure_node_folders(
    project_id: str, nodes: List[Dict[str, Any]], user_id: Optional[str]
) -> None:
    """Materialize a deliverable folder for each non-skipped node in a group.

    Best-effort per node — one node's failure never blocks the others, and the
    whole helper never raises (arrival enrichment, not the primary op)."""
    for node in nodes:
        if node.get("skipped"):
            continue
        await ensure_node_folder(project_id, node, user_id)
