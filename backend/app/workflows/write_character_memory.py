"""write_character_memory DBOS workflow (Phase 4 M7).

Dual-writes a storyboard character into the Graphiti knowledge graph
as an episode under ``project-{project_id}`` — the project-facts side
of the canvas plan's memory design (chat turns live under
``user-{id}``, see ``write_memory``).

Dispatched fire-and-forget from ``StoryboardService.create_character``
/ ``update_character`` via ``start_workflow_routed("memory_tasks",...)``
so the (LLM-backed) graph ingestion never delays the API response.

Same safety contract as the chat harvest: flag-gated, no retries
(a retry double-ingests), failures stay inside the step.
"""

from __future__ import annotations

import json
from typing import Any, Optional

from dbos import DBOS
from loguru import logger

_MAX_BODY_CHARS = 4000


def _render_character_body(
    name: str,
    description: Optional[str],
    visual_traits: Optional[dict[str, Any]],
) -> str:
    """Render the character as a fact-dense episode body."""
    parts = [f"Storyboard character: {name}."]
    if description and description.strip():
        parts.append(description.strip())
    if visual_traits:
        traits = ", ".join(
            f"{key}: {value}" for key, value in visual_traits.items() if value
        )
        if traits:
            parts.append(f"Visual traits — {traits}.")
    return " ".join(parts)[:_MAX_BODY_CHARS]


async def _write_character_episode(
    *,
    project_id: str,
    character_id: str,
    name: str,
    description: Optional[str],
    visual_traits: Optional[dict[str, Any]],
) -> bool:
    """Plain function so tests run without a DBOS workflow context."""
    from app.services.ai.memory.graph_memory import get_graph_memory_service

    service = get_graph_memory_service()
    if not service.config.enabled or not name.strip():
        return False
    return await service.add_chat_episode(
        group_id=f"project-{project_id}",
        name=f"character-{character_id}",
        body=_render_character_body(name, description, visual_traits),
        source_description="storyboard character",
    )


@DBOS.step()
async def write_character_episode_step(
    *,
    project_id: str,
    character_id: str,
    name: str,
    description: Optional[str],
    visual_traits_json: str,
) -> dict[str, Any]:
    """Thin DBOS wrapper; traits travel as JSON for serializer safety."""
    try:
        visual_traits = json.loads(visual_traits_json) if visual_traits_json else None
    except (TypeError, ValueError):
        visual_traits = None
    written = await _write_character_episode(
        project_id=project_id,
        character_id=character_id,
        name=name,
        description=description,
        visual_traits=visual_traits,
    )
    return {"episode_written": written}


@DBOS.workflow()
async def write_character_episode_workflow(
    *,
    project_id: str,
    character_id: str,
    name: str,
    description: Optional[str] = None,
    visual_traits_json: str = "",
) -> dict[str, Any]:
    """Ingest one storyboard character into the project's graph."""
    if not project_id or not character_id:
        logger.warning("[write_character_memory] missing ids; skipping")
        return {"episode_written": False, "reason": "missing_ids"}
    return await write_character_episode_step(
        project_id=project_id,
        character_id=character_id,
        name=name,
        description=description,
        visual_traits_json=visual_traits_json,
    )
