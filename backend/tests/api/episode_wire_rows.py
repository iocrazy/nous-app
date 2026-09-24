"""``episodes`` rows as ``EpisodeRepository`` hands them to the router.

Built from the ORM mapper through the repository's own converters, so a
fixture always carries every column in its real wire type (bigint ids as
JSON numbers, ISO timestamps, ``owner_id`` as a string).
"""

from __future__ import annotations

from typing import Any, Dict

from app.models import Episodes
from app.repositories import episode_repository as repo
from tests.api.wire_parity import sample_orm


def repo_episode_row(**overrides: Any) -> Dict[str, Any]:
    """``EpisodeRepository._row`` of a fully populated episode."""
    return {**repo._row(sample_orm(Episodes)), **overrides}


def repo_progress_row(**overrides: Any) -> Dict[str, Any]:
    """``_progress_row`` of a raw progress row with every aggregate set."""
    raw = {
        "episode_id": 7_300_000_000_000_000_001,
        "title": "Ep 1",
        "sort_order": 1,
        "owner_id": "00000000-0000-0000-0000-000000000009",
        "current_node_id": 7_300_000_000_000_000_002,
        "script_count": 2,
        "scene_count": 3,
        "scene_content_count": 3,
        "shots_total": 4,
        "shots_done": 4,
        "renders_count": 1,
    }
    rollup = {"nodes_total": 5, "nodes_done": 2, "needs_input_count": 1}
    return {**repo._progress_row(raw, rollup), **overrides}
