# app/services/library/project_entities.py

"""Pure derivation for the project Characters/Locations ASSETS view (spec
G13 — 主库 + 出场).

Entities are NOT authored anywhere; they're derived from every non-deleted
script's scenes in the project — character cues from ``content_json``
elements, locations from ``script_scenes.location_text`` — exactly the
"current state is derived, zero manual sync" model the editor's own rail
already uses (``frontend/editor/railDerive.ts``), mirrored server-side so a
project-wide view doesn't need every editor mounted to read it.

The DEDUP-KEY semantics mirror railDerive: ``text.strip().upper()``
(case-insensitive), first-seen casing kept as the display name, empty text
skipped. The COUNT semantics do NOT fully mirror railDerive, though:
railDerive's ``sceneCount`` is a distinct-scene count (a character cued
twice in the same scene, or a location's text repeated, still only counts
that scene once). This module's ``locations[].scene_count`` matches that
(one row per scene, at most one location per row), but
``characters[].cue_count`` does not — it increments once per character
``content_json`` element, so a character cued multiple times within a
single scene is counted per-occurrence here, not capped at one per scene
like the rail. ``content_json`` may arrive as a JSON string depending on
the asyncpg codec setup — parsed defensively, the same trap
``project_stages_repository.py`` guards for ``tools_recommended``.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List


def _load_elements(content_json: Any) -> List[dict]:
    """Defensively coerce a scene's content_json to a list of element dicts."""
    if isinstance(content_json, str):
        try:
            content_json = json.loads(content_json)
        except (TypeError, ValueError):
            return []
    return content_json if isinstance(content_json, list) else []


def _sorted_entries(bucket: Dict[str, Dict[str, Any]], count_key: str) -> List[dict]:
    """Bucket values -> a list sorted by ``count_key`` descending, with the
    dedup working set (a mutable ``set``) rendered to a sorted list."""
    return sorted(
        (
            {
                "name": v["name"],
                count_key: v[count_key],
                "episode_ids": sorted(v["episode_ids"]),
            }
            for v in bucket.values()
        ),
        key=lambda entry: entry[count_key],
        reverse=True,
    )


def derive_project_entities(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Roll up per-scene rows into project-wide Characters/Locations lists.

    ``rows``: ``[{episode_id, location_text, content_json}, ...]``, one per
    scene (as returned by
    ``ScriptSceneRepository.list_scene_rows_for_project``).

    Returns ``{"characters": [...], "locations": [...]}``, each entry
    ``{"name", "cue_count"|"scene_count", "episode_ids": [str, ...]}``,
    sorted by count descending. A scene whose script has no episode
    assignment (``episode_id`` nullable — unassigned script) still
    contributes to the count, it just adds nothing to ``episode_ids``.
    """
    characters: Dict[str, Dict[str, Any]] = {}
    locations: Dict[str, Dict[str, Any]] = {}

    for row in rows:
        episode_id = row.get("episode_id")
        episode_id_str = str(episode_id) if episode_id is not None else None

        loc_text = (row.get("location_text") or "").strip()
        if loc_text:
            entry = locations.setdefault(
                loc_text.upper(),
                {"name": loc_text, "scene_count": 0, "episode_ids": set()},
            )
            entry["scene_count"] += 1
            if episode_id_str:
                entry["episode_ids"].add(episode_id_str)

        for element in _load_elements(row.get("content_json")):
            if not isinstance(element, dict) or element.get("type") != "character":
                continue
            text = (element.get("text") or "").strip()
            if not text:
                continue
            entry = characters.setdefault(
                text.upper(),
                {"name": text, "cue_count": 0, "episode_ids": set()},
            )
            entry["cue_count"] += 1
            if episode_id_str:
                entry["episode_ids"].add(episode_id_str)

    return {
        "characters": _sorted_entries(characters, "cue_count"),
        "locations": _sorted_entries(locations, "scene_count"),
    }
