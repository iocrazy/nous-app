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


async def _episode_scoped_folder_name(node_name: str, episode_id: str) -> str:
    """Fold an episode discriminator into a node's folder name so that the same
    node (e.g. "Script") in different episodes gets *different* root folders.

    Without this, ``ensure_node_folder`` reuses a root folder by name
    (case-insensitive): Ep1 mints "Script", then Ep2/Ep3's "Script" node
    deterministically reuses Ep1's folder and shares its ``folder_id`` — which
    makes ``_deliverable_present`` (keyed off ``node.folder_id``) treat Ep1's
    single filed deliverable as satisfying every later episode's gate2, letting
    a per-episode advance jump three episodes on its first real use (P0 trap②).

    The name is anchored on ``episode_id`` for a guaranteed-unique, guaranteed-
    stable discriminator: episode ``title`` / ``sort_order`` are readable but
    NOT unique (users can name two episodes alike; ``sort_order`` can even
    collide under concurrent creates — see ``EpisodeRepository.create``), so
    keying on them would let the very bug we're fixing resurface for same-named
    episodes. The title is fetched best-effort purely for readability and, when
    present, prepended — but ``episode_id`` always remains in the name so
    uniqueness never depends on it. Any fetch failure degrades to an
    ``episode_id``-only label (never raises): the caller is enrichment, never a
    gate.

    Same call site, both the reuse *match* and the *create* use this one name —
    they must agree, else a first call creates "<label> · Script" and the next
    fails to match it and mints a duplicate every time.
    """
    label = f"Episode {episode_id}"
    try:
        from app.repositories.episode_repository import get_episode_repository

        episode = await get_episode_repository().get_by_id(str(episode_id))
        title = ((episode or {}).get("title") or "").strip()
        if title:
            # Keep episode_id in the name so two same-titled episodes still
            # diverge (uniqueness is anchored on the id, not the title).
            label = f"{title} [#{episode_id}]"
    except Exception as exc:  # noqa: BLE001 — readability enrichment, never blocks
        logger.warning(
            f"[node_folders] episode label lookup failed for {episode_id}: {exc!r}"
        )
    return f"{label} · {node_name}"


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

    # Per-episode scoping (B2 T2): an episode-bound node is filed under an
    # episode-scoped folder name so Ep2/Ep3's "Script" no longer collide with
    # Ep1's. A legacy project-level node (episode_id is None) keeps the bare
    # node name — byte-for-byte the pre-B2 behaviour.
    episode_id = node.get("episode_id")

    from app.repositories.project_stage_nodes_repository import (
        get_project_stage_nodes_repository,
    )
    from app.repositories.projects_repository import get_projects_repository

    projects_repo = get_projects_repository()
    nodes_repo = get_project_stage_nodes_repository()
    try:
        folder_name = node_name
        if episode_id is not None:
            folder_name = await _episode_scoped_folder_name(node_name, str(episode_id))

        # Reuse a hand-made root folder of the same name before creating one.
        folders = await projects_repo.get_folders(str(project_id))
        match = next(
            (
                f
                for f in folders
                if (f.get("name") or "").strip().lower() == folder_name.lower()
            ),
            None,
        )
        if match is not None:
            folder_id = str(match["id"])
        else:
            created = await projects_repo.create_folder(
                {
                    "project_id": str(project_id),
                    "name": folder_name,
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
