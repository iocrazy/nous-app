"""Authorization predicates for node CONFIG edits vs. workflow/episode
ARRANGEMENT mutations (Task 7, workspace IA redesign spec §5).

Two distinct write surfaces, deliberately split:

- **Node config** (an existing node's ``owner_user_id`` / ``owner_agent_id`` /
  ``members`` / ``planned_start`` / ``planned_due`` / ``brief``) — the
  project manager OR the node's episode's ``owner_id`` (Task 6,
  ``episodes.owner_id``) may edit. A node with no ``episode_id`` (legacy
  project-level node, predates B3 per-episode instantiation) has no episode
  owner to fall back to — manager only.
- **Arrangement** (add/remove a node; create/delete an episode) — manager
  only, full stop. Deliberately no episode-owner carve-out: an episode
  owner configures the content of their own episode's nodes, they don't
  get to restructure the workflow itself (that stays a project-manager
  decision, spec §5).

Both predicates resolve role via ``resolve_effective_role`` (the single
source of truth for project/team role — see ``app.core.workflow_roles``),
which already folds the project-owner-as-manager fallback in, so a personal
project's owner is never accidentally locked out of either surface.
"""

from __future__ import annotations

from typing import Optional

from fastapi import HTTPException


async def can_edit_node_config(
    project_id: str, episode_id: Optional[str], user_id: str
) -> bool:
    """True iff ``user_id`` may edit a node's config fields: project manager,
    or (when the node belongs to an episode) that episode's ``owner_id``."""
    from app.core.workflow_roles import MANAGER, resolve_effective_role

    role = await resolve_effective_role(user_id, project_id=project_id)
    if role == MANAGER:
        return True
    if not episode_id:
        return False

    from app.repositories.episode_repository import get_episode_repository

    episode = await get_episode_repository().get_by_id(episode_id)
    return bool(episode and str(episode.get("owner_id") or "") == str(user_id))


async def require_arrangement_role(project_id: str, user_id: str) -> None:
    """Raise 403 ``arrangement_forbidden`` unless ``user_id`` is the project
    manager. Used to gate workflow-node add/delete and episode create/delete
    — structural moves, never delegated to an episode owner."""
    from app.core.workflow_roles import MANAGER, resolve_effective_role

    role = await resolve_effective_role(user_id, project_id=project_id)
    if role != MANAGER:
        raise HTTPException(
            status_code=403, detail={"code": "arrangement_forbidden"}
        )
