# backend/app/api/resources_ai_router.py

"""Resources AI Router

Provider-routed AI operations on a single resource. Currently:
- POST /resources/{id}/gen-prompt/translate — translate the asset's
  generation prompt between EN and ZH via the user's assigned
  `translation` agent (task_assignment.translation, default `translate`).
- POST /resources/{id}/gen-prompt/generate — reverse-engineer a bilingual
  prompt from the image via the assigned `caption` vision agent
  (dispatched as the caption_asset DBOS workflow).

Kept separate from resources_crud_router (already >1300 lines) per the
many-small-files rule.
"""

import uuid as _uuid

from fastapi import APIRouter, Depends, HTTPException
from loguru import logger

from app.core.deps import AuthDep
from app.core.scope_dep import scoped_request
from app.repositories.resources_repository import ResourcesRepository
from app.schemas.resources import GenPromptTranslateRequest

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


@router.post("/{resource_id}/gen-prompt/generate")
async def generate_gen_prompt(
    resource_id: str,
    auth: AuthDep,
):
    """Reverse-engineer a bilingual generation prompt from the image.

    Dispatches the ``caption_asset`` DBOS workflow with a pre-created
    task_tracking row whose ``dbos_workflow_id`` MATCHES the dispatched
    ``workflow_id`` (reference_dbos_dispatch_endpoint_wf_id) — the
    lifecycle trigger can't associate them otherwise and the task would
    never complete. Returns the task id; the workflow writes
    gen_prompt / gen_prompt_zh when it finishes.
    """
    from app.api.media_permissions import check_media_access
    from app.services.infra.dbos_orchestrator import start_workflow_routed
    from app.services.infra.unified_task_manager import get_task_manager
    from app.workflows.caption_asset import caption_asset_workflow

    repo = ResourcesRepository()
    resource = await repo.get_resource_by_id(resource_id)
    if not resource:
        raise HTTPException(status_code=404, detail="Resource not found")
    if not await check_media_access(resource_id, auth.user_id, None):
        raise HTTPException(status_code=403, detail="Access denied")
    if (resource.get("file_type") or "") != "image":
        raise HTTPException(
            status_code=400,
            detail="Prompt generation is only supported for image resources",
        )
    if not resource.get("file_path"):
        raise HTTPException(status_code=400, detail="Resource has no stored file")

    filename = (resource.get("filename") or str(resource_id))[:40]
    wf_id = str(_uuid.uuid4())
    try:
        await get_task_manager().create(
            user_id=auth.user_id,
            task_type="prompt_caption",
            title=f"Prompt {filename}",
            resource_id=str(resource_id),
            dbos_workflow_id=wf_id,
        )
    except Exception as e:
        logger.warning(f"[CaptionAsset] pre-create task_tracking row: {e}")

    try:
        await start_workflow_routed(
            "prompt_caption",
            dbos_workflow_callable=caption_asset_workflow,
            dbos_workflow_kwargs={
                "resource_id": str(resource_id),
                "user_id": auth.user_id,
            },
            workflow_id=wf_id,
        )
    except Exception as e:
        logger.error(f"[CaptionAsset] dispatch failed for {resource_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to start prompt generation")

    return {"success": True, "task_id": wf_id}
