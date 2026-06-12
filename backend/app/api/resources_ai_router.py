# backend/app/api/resources_ai_router.py

"""Resources AI Router

Provider-routed AI operations on a single resource. Currently:
- POST /resources/{id}/gen-prompt/translate — translate the asset's
  generation prompt between EN and ZH via the user's assigned
  `translation` agent (task_assignment.translation, default `translate`).

Kept separate from resources_crud_router (already >1300 lines) per the
many-small-files rule.
"""

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
