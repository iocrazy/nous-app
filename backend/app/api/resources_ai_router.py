# backend/app/api/resources_ai_router.py

"""Resources AI Router

Provider-routed AI operations on a single resource. Currently:
- POST /resources/{id}/gen-prompt/translate — translate the asset's
  generation prompt between EN and ZH via the user's assigned
  `translation` agent (task_assignment.translation, default `translate`).
- POST /resources/{id}/gen-prompt/generate — reverse-engineer a bilingual
  prompt from the image via the assigned `caption` vision agent
  (dispatched as the caption_asset DBOS workflow).
- POST /resources/{id}/classify — 12-dimension bilingual auto-tagging via
  the assigned `classify` vision agent (classify_asset DBOS workflow);
  on-demand only, uploads never auto-classify.
- POST /resources/ai/batch — dispatch caption/classify for ≤50 resources
  (one workflow + Task Center row per image).
- POST /resources/export/training-set — LoRA-format zip (image +
  same-stem .txt caption from gen_prompt/_zh).

Kept separate from resources_crud_router (already >1300 lines) per the
many-small-files rule.
"""

import uuid as _uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from loguru import logger

from app.core.deps import AuthDep
from app.core.scope_dep import scoped_request
from app.repositories.resources_repository import ResourcesRepository
from app.schemas.resources import (
    BatchAssetAiRequest,
    GenPromptTranslateRequest,
    TrainingSetExportRequest,
)

router = APIRouter(prefix="/resources", dependencies=[Depends(scoped_request)])


@router.post("/{resource_id}/gen-prompt/translate")
async def translate_gen_prompt(
    resource_id: str,
    data: GenPromptTranslateRequest,
    auth: AuthDep,
):
    """Translate the resource's generation prompt into the target language.

    target_lang='zh' reads gen_prompt → writes gen_prompt_zh;
    target_lang='en' reads gen_prompt_zh → writes gen_prompt.
    The source side is never modified.
    """
    from app.api.media_permissions import check_media_access
    from app.services.ai.providers.ai_provider_helpers import (
        resolve_translate_provider_config,
    )
    from app.services.ai.translate import TranslateService

    repo = ResourcesRepository()
    resource = await repo.get_resource_by_id(resource_id)
    if not resource:
        raise HTTPException(status_code=404, detail="Resource not found")
    if not await check_media_access(resource_id, auth.user_id, None):
        raise HTTPException(status_code=403, detail="Access denied")

    source_field = "gen_prompt" if data.target_lang == "zh" else "gen_prompt_zh"
    target_field = "gen_prompt_zh" if data.target_lang == "zh" else "gen_prompt"
    source_text = (resource.get(source_field) or "").strip()
    if not source_text:
        raise HTTPException(
            status_code=400, detail=f"No {source_field} to translate from"
        )

    try:
        provider_key, provider_config, _model, agent_slug = (
            await resolve_translate_provider_config(auth.user_id)
        )
        svc = TranslateService(
            provider_key=provider_key,
            provider_config=provider_config,
            agent_slug=agent_slug,
        )
        translated = await svc.translate(
            text=source_text,
            target_lang=data.target_lang,
            user_id=auth.user_id,
            resource_id=str(resource_id),
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Translate gen_prompt failed for {resource_id}: {e}")
        raise HTTPException(status_code=500, detail="Translation failed")

    if not translated:
        # The service logs the underlying cause; surface an actionable
        # message (most common: no provider key configured for the model).
        raise HTTPException(
            status_code=502,
            detail=(
                "Translation produced no result — check the translation "
                "agent's provider configuration in Settings → AI"
            ),
        )

    updated = await repo.update_resource(resource_id, {target_field: translated})
    return {
        "success": True,
        "data": {
            "gen_prompt": (updated or {}).get("gen_prompt"),
            "gen_prompt_zh": (updated or {}).get("gen_prompt_zh"),
        },
    }


def _resolve_ai_operation(operation: str) -> dict:
    """Map an asset-AI operation name to its dispatch parameters.

    Workflow callables are imported lazily so importing this router never
    drags the DBOS workflow modules in at module-import time.
    """
    if operation == "caption":
        from app.workflows.caption_asset import caption_asset_workflow

        return {
            "task_type": "prompt_caption",
            "title_prefix": "Prompt",
            "workflow": caption_asset_workflow,
            "log_tag": "CaptionAsset",
        }
    if operation == "classify":
        from app.workflows.classify_asset import classify_asset_workflow

        return {
            "task_type": "asset_classify",
            "title_prefix": "Auto Tag",
            "workflow": classify_asset_workflow,
            "log_tag": "ClassifyAsset",
        }
    raise ValueError(f"unknown asset AI operation '{operation}'")


def _image_gate_reason(resource: Optional[dict]) -> Optional[str]:
    """Why this resource can't run an image-AI operation (None = it can)."""
    if not resource:
        return "Resource not found"
    if (resource.get("file_type") or "") != "image":
        return "Only image resources are supported"
    if not resource.get("file_path"):
        return "Resource has no stored file"
    return None


async def _dispatch_asset_ai(resource: dict, user_id: str, operation: str) -> str:
    """Pre-create the task_tracking row and dispatch the workflow.

    The row's ``dbos_workflow_id`` MUST match the dispatched
    ``workflow_id`` (reference_dbos_dispatch_endpoint_wf_id) — the
    lifecycle trigger can't associate them otherwise and the task would
    never complete. Returns the task id (= workflow id).
    """
    from app.services.infra.dbos_orchestrator import start_workflow_routed
    from app.services.infra.unified_task_manager import get_task_manager

    op = _resolve_ai_operation(operation)
    resource_id = str(resource["id"])
    filename = (resource.get("filename") or resource_id)[:40]
    wf_id = str(_uuid.uuid4())
    try:
        await get_task_manager().create(
            user_id=user_id,
            task_type=op["task_type"],
            title=f"{op['title_prefix']} {filename}",
            resource_id=resource_id,
            dbos_workflow_id=wf_id,
        )
    except Exception as e:
        logger.warning(f"[{op['log_tag']}] pre-create task_tracking row: {e}")

    await start_workflow_routed(
        op["task_type"],
        dbos_workflow_callable=op["workflow"],
        dbos_workflow_kwargs={
            "resource_id": resource_id,
            "user_id": user_id,
        },
        workflow_id=wf_id,
    )
    return wf_id


async def _single_asset_ai(resource_id: str, user_id: str, operation: str) -> dict:
    """Shared body of the single-resource caption/classify endpoints."""
    from app.api.media_permissions import check_media_access

    op = _resolve_ai_operation(operation)
    resource = await ResourcesRepository().get_resource_by_id(resource_id)
    if not resource:
        raise HTTPException(status_code=404, detail="Resource not found")
    if not await check_media_access(resource_id, user_id, None):
        raise HTTPException(status_code=403, detail="Access denied")
    gate = _image_gate_reason(resource)
    if gate:
        raise HTTPException(status_code=400, detail=gate)

    try:
        task_id = await _dispatch_asset_ai(resource, user_id, operation)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[{op['log_tag']}] dispatch failed for {resource_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to start {operation}")
    return {"success": True, "task_id": task_id}


@router.post("/{resource_id}/gen-prompt/generate")
async def generate_gen_prompt(
    resource_id: str,
    auth: AuthDep,
):
    """Reverse-engineer a bilingual generation prompt from the image.

    Dispatches the ``caption_asset`` DBOS workflow with a pre-created
    task_tracking row whose ``dbos_workflow_id`` MATCHES the dispatched
    ``workflow_id`` (see ``_dispatch_asset_ai``). Returns the task id;
    the workflow writes gen_prompt / gen_prompt_zh when it finishes.
    """
    return await _single_asset_ai(resource_id, auth.user_id, "caption")


@router.post("/{resource_id}/classify")
async def classify_resource(
    resource_id: str,
    auth: AuthDep,
):
    """Auto-tag an image resource along 12 dimensions (bilingual tags).

    Same dispatch contract as generate_gen_prompt (``_dispatch_asset_ai``:
    pre-created task_tracking row with ``dbos_workflow_id=wf_id`` matching
    the dispatched ``workflow_id``).
    """
    return await _single_asset_ai(resource_id, auth.user_id, "classify")


@router.post("/ai/batch")
async def batch_asset_ai(
    data: BatchAssetAiRequest,
    auth: AuthDep,
):
    """Dispatch caption/classify for up to 50 resources in one call.

    Each image gets its OWN workflow + Task Center row (the IC weakness
    this replaces was a single synchronous in-request loop). Non-image /
    inaccessible / missing entries are reported in ``skipped`` with a
    reason rather than failing the whole batch.
    """
    from app.api.media_permissions import check_media_access

    repo = ResourcesRepository()
    dispatched: list[dict] = []
    skipped: list[dict] = []

    for resource_id in dict.fromkeys(data.resource_ids):  # dedupe, keep order
        resource = await repo.get_resource_by_id(resource_id)
        gate = _image_gate_reason(resource)
        if gate:
            skipped.append({"resource_id": resource_id, "reason": gate})
            continue
        if not await check_media_access(resource_id, auth.user_id, None):
            skipped.append({"resource_id": resource_id, "reason": "Access denied"})
            continue
        try:
            task_id = await _dispatch_asset_ai(resource, auth.user_id, data.operation)
            dispatched.append({"resource_id": resource_id, "task_id": task_id})
        except Exception as e:
            logger.error(
                f"[BatchAssetAi] {data.operation} dispatch failed for "
                f"{resource_id}: {e}"
            )
            skipped.append({"resource_id": resource_id, "reason": "Dispatch failed"})

    return {"success": True, "dispatched": dispatched, "skipped": skipped}


@router.post("/export/training-set")
async def export_training_set(
    data: TrainingSetExportRequest,
    auth: AuthDep,
):
    """Stream a LoRA-training zip: each image + a same-stem ``.txt``
    caption holding the asset's generation prompt.

    ``lang`` picks the prompt side for the captions (falling back to the
    other side when the requested one is empty; no .txt when neither
    exists — trainers treat caption-less images as uncaptioned). Mirrors
    the gallery-zip endpoint's in-memory ZIP_STORED streaming.
    """
    import io
    import zipfile
    from pathlib import Path

    from fastapi.responses import StreamingResponse

    from app.api.media_permissions import check_media_access
    from app.core.config import settings

    repo = ResourcesRepository()
    buffer = io.BytesIO()
    added = 0
    used_names: set[str] = set()

    def _unique_arcname(filename: str, resource_id: str) -> str:
        stem, dot, ext = filename.rpartition(".")
        if not dot:
            stem, ext = filename, ""
        candidate = filename
        if candidate.lower() in used_names:
            candidate = f"{stem}_{resource_id}{dot}{ext}"
        used_names.add(candidate.lower())
        return candidate

    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_STORED) as zf:
        for resource_id in dict.fromkeys(data.resource_ids):
            resource = await repo.get_resource_by_id(resource_id)
            if _image_gate_reason(resource):
                continue
            if not await check_media_access(resource_id, auth.user_id, None):
                continue
            abs_path = Path(settings.DOWNLOAD_PATH) / resource["file_path"]
            if not abs_path.is_file():
                logger.warning(
                    f"[TrainingExport] file missing on disk for {resource_id}"
                )
                continue

            filename = resource.get("filename") or abs_path.name
            if "." not in filename and abs_path.suffix:
                filename = f"{filename}{abs_path.suffix}"
            arcname = _unique_arcname(filename, str(resource_id))
            zf.write(abs_path, arcname=arcname)
            added += 1

            primary = "gen_prompt_zh" if data.lang == "zh" else "gen_prompt"
            fallback = "gen_prompt" if data.lang == "zh" else "gen_prompt_zh"
            caption = (resource.get(primary) or "").strip() or (
                resource.get(fallback) or ""
            ).strip()
            if caption:
                stem = arcname.rsplit(".", 1)[0]
                zf.writestr(f"{stem}.txt", caption)

    if added == 0:
        raise HTTPException(
            status_code=404, detail="No exportable images in the selection"
        )

    buffer.seek(0)
    return StreamingResponse(
        buffer,
        media_type="application/zip",
        headers={
            "Content-Disposition": 'attachment; filename="training-set.zip"',
        },
    )
