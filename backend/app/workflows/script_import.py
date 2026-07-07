"""script_import DBOS workflow — create a script's scenes from imported text.

Two import paths, one workflow:

* **fountain** — the deterministic ``parse_fountain`` parser (zero LLM, zero
  wait) produces ``SceneDraft``s that ``persist_fountain_scenes`` writes via
  ``ScriptSceneRepository.create_with_content`` (scene + genesis op ledger, one
  transaction per scene). Unlike the convert path, the parsed
  ``heading_int_ext`` is preserved verbatim (``EST`` / ``INT/EXT`` / ``I/E`` all
  survive) — the deterministic parser is authoritative, so there is nothing to
  sanitize.
* **prose** — reuses the existing ``script_scene_convert`` **step functions**
  directly (not a nested dispatch): a chapter is created to hold the prose, then
  ``convert_chapter_to_scenes`` (LLM) + ``persist_scenes`` run as steps of THIS
  workflow. One workflow == one task row; composing the steps avoids minting a
  second task_tracking row that a nested workflow dispatch would create.

Route-C discipline: failure paths ``raise`` (never ``return {"status":
"failed"}``) so DBOS records the workflow FAILED and the task_tracking mirror
never marks a broken import completed. The task_tracking row is created by the
endpoint (``mgr.create(dbos_workflow_id=wf_id)``); its phase is mirrored from
the DBOS lifecycle by the DB trigger — the workflow itself never PATCHes phase.
"""

from __future__ import annotations

from typing import Any, List, Optional

from dbos import DBOS
from loguru import logger

from app.services.script.fountain_parser import SceneDraft, parse_fountain
from app.workflows.script_scene_convert import (
    _build_elements,
    convert_chapter_to_scenes,
    persist_scenes,
)

# Actor stamped on the genesis op ledger for imported scenes.
_ACTOR = "import"
# Title of the holding chapter created for a prose import.
_PROSE_CHAPTER_TITLE = "Imported"


@DBOS.step()
async def persist_fountain_scenes(
    script_id: str, scene_drafts: List[SceneDraft]
) -> dict[str, Any]:
    """Persist each parsed ``SceneDraft`` + its genesis op ledger row.

    One ``create_with_content`` call per scene (each its own transaction).
    Scenes attach directly to the script (``chapter_id=None``). A draft with no
    usable elements AND no heading is skipped; a heading-only scene (no body) is
    still created so the user gets the stub. The parsed ``heading_int_ext`` is
    written verbatim — the deterministic parser is authoritative."""
    from app.repositories.script_scene_repository import ScriptSceneRepository

    repo = ScriptSceneRepository()
    created_ids: List[str] = []
    for draft in scene_drafts:
        elements = _build_elements(draft.get("elements"))
        heading = draft.get("heading_int_ext") or ""
        if not elements and not heading:
            continue
        data = {
            "script_id": script_id,
            "chapter_id": None,
            "heading_int_ext": heading,
            "location_text": draft.get("location_text") or "",
            "time_of_day": draft.get("time_of_day") or "",
        }
        row = await repo.create_with_content(data, elements, _ACTOR)
        created_ids.append(str(row.get("id", "")))
    logger.info(f"[script_import][fountain] persisted {len(created_ids)} scenes")
    return {
        "status": "success",
        "scene_count": len(created_ids),
        "scene_ids": created_ids,
    }


@DBOS.step()
async def create_prose_chapter(script_id: str, content: str) -> str:
    """Create a holding chapter carrying the raw prose, returning its id.

    The prose lands in the chapter's ``content`` column (mig 120 plain-text
    field) so the reused ``convert_chapter_to_scenes`` step can read it exactly
    as it does for the manual convert-to-scenes flow."""
    from app.services.storyboard.script.script_service import ScriptService

    chapter = await ScriptService().create_chapter(
        script_id,
        {"title": _PROSE_CHAPTER_TITLE, "content": content, "chapter_number": 1},
    )
    return str(chapter.get("id", ""))


async def _run_import(
    script_id: str, mode: str, content: str, user_id: Optional[str]
) -> dict[str, Any]:
    """Mode dispatch, extracted as a plain coroutine so it is unit-testable
    without a live DBOS runtime (a ``@DBOS.workflow`` cannot be invoked before
    DBOS is initialized). Steps called here still run within the workflow's DBOS
    context because it drives this coroutine."""
    if mode == "fountain":
        # Deterministic + bounded (≤ MAX_FOUNTAIN_CHARS); safe to run inline
        # (re-runs identically on replay).
        drafts = parse_fountain(content)
        return await persist_fountain_scenes(script_id, drafts)
    if mode == "prose":
        chapter_id = await create_prose_chapter(script_id, content)
        scenes = await convert_chapter_to_scenes(chapter_id, user_id)
        return await persist_scenes(script_id, chapter_id, scenes)
    raise ValueError(f"Unknown import mode: {mode!r}")


@DBOS.workflow()
async def script_import_workflow(
    script_id: str,
    mode: str,
    content: str,
    *,
    user_id: Optional[str] = None,
) -> dict[str, Any]:
    """DBOS orchestrator: imported text → persisted screenplay scenes.

    - input: script_id (bigint str) + mode (``fountain``|``prose``) + content
    - output: {status, scene_count, scene_ids}
    - side-effects: inserts N rows into script_scenes + N genesis rows into
      script_ops (prose also inserts one holding chapter).

    ``user_id`` is keyword-only + optional so a replayed workflow started before
    this field existed keeps working (DBOS input is frozen at workflow start)."""
    return await _run_import(script_id, mode, content, user_id)
