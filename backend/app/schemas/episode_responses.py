"""Response shapes of the episode routes (``app/api/episodes_router.py``).

These declare what the router already sent; nothing here changes the wire.

Two id conventions live side by side on this surface, and both are real:

- ``EpisodeRow`` is the repository's strategy-C parity dict (``_row``):
  bigint ids/FKs stay JSON **numbers**, timestamps are already ISO strings
  (``_parity`` ran ``isoformat()``), the ``owner_id`` uuid is a string.
- ``EpisodeProgressRow`` is built field by field in ``_progress_row``, which
  stringifies ``episode_id`` and ``workflow.current_node_id``.

``tests/api/test_episodes_wire.py`` pins ``EpisodeRow`` to the ORM column
set, so a column added to ``episodes`` fails until it is declared here.

Spec: docs/superpowers/specs/2026-09-24-openapi-typed-frontend-design.md
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

# ``episode_repository._derive_episode_status`` is the only producer.
EpisodeStatus = Literal["planned", "drafting", "boarding", "boarded", "rendered"]


class EpisodeRow(BaseModel):
    """One ``episodes`` row as ``EpisodeRepository._row`` shapes it."""

    id: int
    project_id: int
    title: str
    sort_order: int
    created_at: str
    updated_at: str
    current_node_id: int | None
    owner_id: str | None


class EpisodeListRow(EpisodeRow):
    """A list row: the episode plus its live (non-deleted) script count.

    The UI gates deletion on ``script_count``: the ``script_projects``
    FK is ON DELETE RESTRICT, so a non-empty episode cannot be removed.
    """

    script_count: int


class EpisodeWorkflowRollup(BaseModel):
    """This episode's workflow node counts and cursor.

    ``current_node_id`` is stringified (bigint), null with no cursor.
    """

    nodes_total: int
    nodes_done: int
    current_node_id: str | None
    needs_input_count: int


class EpisodeSurfaceState(BaseModel):
    """The completion criteria derived from this episode's content.

    They can legitimately disagree with a node's persisted status (the
    produced content was deleted after the node auto-completed).
    """

    script: bool
    storyboard: bool


class EpisodeProgressRow(BaseModel):
    """One row of ``GET /projects/{id}/episodes/progress`` (spec G12)."""

    episode_id: str
    title: str
    sort_order: int
    owner_id: str | None
    script_count: int
    scene_count: int
    shots_total: int
    shots_done: int
    renders_count: int
    status: EpisodeStatus
    workflow: EpisodeWorkflowRollup
    surface_state: EpisodeSurfaceState


class EpisodeDeleted(BaseModel):
    """``DELETE /episodes/{id}`` answers ``{"success": true}`` and nothing else."""

    success: bool = True
