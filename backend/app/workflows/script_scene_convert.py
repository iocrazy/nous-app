"""script_scene_convert DBOS workflow — AI conversion of chapter prose into
screenplay scenes (spec v3 §2, Phase B Task 6).

Two-step DBOS shape mirrors ``script_outline`` / ``script_ai_workflows``:
    - step1 ``convert_chapter_to_scenes`` is the retryable LLM call: it loads
      the chapter's derived plain-text ``content`` (mig 120 column), resolves
      the DB-governed provider config, and asks the model to split the prose
      into scenes. A transient HTTP failure gets one retry; the workflow_id
      memoizes the eventual success so a downstream replay never re-charges the
      model.
    - step2 ``persist_scenes`` writes each scene + its genesis op ledger row via
      ``ScriptSceneRepository.create_with_content`` (one transaction per scene).
      Separated so a half-written scene set doesn't re-invoke the LLM on replay.

Route-C discipline: failure paths ``raise`` (never ``return {"status":
"failed"}``) so DBOS records the workflow as FAILED and the task_tracking
mirror never marks a broken convert as completed.
"""

from __future__ import annotations

import uuid as _uuid
from typing import Any, Optional

from dbos import DBOS
from loguru import logger

from app.services.script.scene_ops import ELEMENT_TYPES

# Unknown element types coerce to this (spec §2.2 — never persist an element
# type the op protocol can't round-trip).
_DEFAULT_ELEMENT_TYPE = "action"

# heading_int_ext is String(10); normalize anything the model returns to one of
# these, defaulting to INT when the model is vague.
_VALID_HEADINGS = frozenset({"INT", "EXT"})
_DEFAULT_HEADING = "INT"

_ACTOR = "copilot"


def _element_id() -> str:
    """Generate a fresh scene-element id (``el_`` + 8 hex chars)."""
    return f"el_{_uuid.uuid4().hex[:8]}"


def _build_elements(raw_elements: Any) -> list[dict[str, Any]]:
    """Coerce the model's element list into id-bearing, type-validated elements.

    Unknown/absent types coerce to ``action`` (spec §2.2). Elements with no
    usable text are dropped. Every element gets a fresh ``el_`` id."""
    if not isinstance(raw_elements, list):
        return []
    out: list[dict[str, Any]] = []
    for el in raw_elements:
        if not isinstance(el, dict):
            continue
        text = el.get("text")
        if not isinstance(text, str) or not text.strip():
            continue
        el_type = el.get("type")
        if el_type not in ELEMENT_TYPES:
            el_type = _DEFAULT_ELEMENT_TYPE
        out.append({"id": _element_id(), "type": el_type, "text": text})
    return out


@DBOS.step(retries_allowed=True, max_attempts=2)
async def convert_chapter_to_scenes(
    chapter_id: str,
    user_id: Optional[str] = None,
) -> list[dict[str, Any]]:
    """Load the chapter's prose and run the LLM scene-split call.

    Returns the raw scene dict list from the model (heading/location/time +
    an elements array). Shape validation happens here (raises on a non-array or
    unparseable body) so persistence never sees garbage.
    """
    from app.services.ai.providers.ai_provider_helpers import (
        resolve_script_provider_config,
    )
    from app.services.storyboard.script.script_ai_service import ScriptAIService
    from app.services.storyboard.script.script_service import ScriptService

    script_svc = ScriptService()
    chapter = await script_svc.chapter_repo.get_by_id(chapter_id)
    if not chapter:
        raise ValueError(f"Chapter not found: {chapter_id}")

    provider_key, provider_config, _model, agent_slug = (
        await resolve_script_provider_config(user_id)
    )
    ai_svc = ScriptAIService(
        user_id=user_id,
        agent_slug=agent_slug,
        provider_key=provider_key,
        provider_config=provider_config,
    )
    scenes = await ai_svc.split_chapter_to_screenplay_scenes(
        title=chapter.get("title", "") or "",
        summary=chapter.get("summary", "") or "",
        content=chapter.get("content"),
    )
    logger.info(f"[script_scene_convert][step] LLM returned {len(scenes)} scenes")
    return scenes


@DBOS.step()
async def persist_scenes(
    script_id: str,
    chapter_id: str,
    scenes: list[dict[str, Any]],
) -> dict[str, Any]:
    """Persist each scene + its genesis op ledger row.

    One ``create_with_content`` call per scene (each its own transaction).
    Idempotent at the workflow_id level: a replay returns the cached created
    ids rather than re-inserting."""
    from app.repositories.script_scene_repository import ScriptSceneRepository

    repo = ScriptSceneRepository()
    created_ids: list[str] = []
    for scene in scenes:
        if not isinstance(scene, dict):
            continue
        elements = _build_elements(scene.get("elements"))
        if not elements:
            # A scene with no usable elements is skipped rather than persisting
            # an empty content_json the editor can't render.
            continue
        heading = scene.get("heading_int_ext")
        if heading not in _VALID_HEADINGS:
            heading = _DEFAULT_HEADING
        data = {
            "script_id": script_id,
            "chapter_id": chapter_id,
            "heading_int_ext": heading,
            "location_text": scene.get("location_text") or "",
            "time_of_day": scene.get("time_of_day") or "",
        }
        row = await repo.create_with_content(data, elements, _ACTOR)
        created_ids.append(str(row.get("id", "")))
    return {
        "status": "success",
        "scene_count": len(created_ids),
        "scene_ids": created_ids,
    }


@DBOS.workflow()
async def script_scene_convert_workflow(
    script_id: str,
    chapter_id: str,
    *,
    user_id: Optional[str] = None,
) -> dict[str, Any]:
    """DBOS orchestrator: chapter prose → persisted screenplay scenes.

    - input: script_id (bigint str) + chapter_id (bigint str) + user_id
    - output: {status, scene_count, scene_ids}
    - side-effects: inserts N rows into script_scenes + N genesis rows into
      script_ops

    ``user_id`` is optional so replayed workflows started before this field
    existed keep working (DBOS input is frozen at workflow start).
    """
    scenes = await convert_chapter_to_scenes(chapter_id, user_id)
    return await persist_scenes(script_id, chapter_id, scenes)
