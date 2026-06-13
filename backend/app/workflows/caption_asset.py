"""caption_asset (prompt_caption) DBOS workflow — IC-port P1-2.

Reverse-engineers a bilingual generation prompt for an image resource via
the user's assigned ``caption`` vision agent and writes it to
``resources.gen_prompt`` / ``gen_prompt_zh``.

Dispatched by ``POST /resources/{id}/gen-prompt/generate`` with a
pre-created task_tracking row whose ``dbos_workflow_id`` matches this
workflow's id (reference_dbos_dispatch_endpoint_wf_id).

Design (mirrors analyze_l1 / upload_postprocess conventions):
  - Provider resolution and the multimodal LLM call are ``@DBOS.step``s
    (primitives in/out, memoized on replay; the LLM call retries once).
  - The resource read/write happens in the workflow body inside the
    user's ambient scope (same as upload_postprocess).
  - Failure paths RAISE (CLAUDE.md 路线 C rule 4) so task_tracking shows
    the real reason instead of a phantom "completed".
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from dbos import DBOS
from loguru import logger

from app.core.config import settings
from app.db.scope import Scope, request_scope


@DBOS.step()
async def resolve_caption_provider(user_id: Optional[str]) -> dict[str, Any]:
    """Resolve provider key + config + model + agent slug for caption."""
    from app.services.ai.providers.ai_provider_helpers import (
        resolve_caption_provider_config,
    )

    (
        provider_key,
        provider_config,
        agent_model,
        agent_slug,
    ) = await resolve_caption_provider_config(user_id)
    return {
        "provider_key": provider_key,
        "provider_config": provider_config or {},
        "agent_model": agent_model,
        "agent_slug": agent_slug,
    }


@DBOS.step(retries_allowed=True, max_attempts=2)
async def call_caption(
    abs_path: str,
    user_id: str,
    resource_id: str,
    provider_key: str,
    provider_config: dict[str, Any],
    agent_slug: str,
    wf_id: Optional[str] = None,
) -> dict[str, str]:
    """Run the multimodal caption call. Returns ``{"en", "zh"}``.

    Raises on no-result so the workflow is marked FAILED with the actual
    reason (rule 4) instead of completing with nothing written.
    """
    from app.services.ai.caption import CaptionService

    service = CaptionService(
        provider_key=provider_key,
        provider_config=provider_config,
        agent_slug=agent_slug or "caption",
    )
    result = await service.caption(
        file_path=abs_path,
        user_id=user_id,
        resource_id=resource_id,
        task_id=wf_id,
    )
    if not result:
        raise RuntimeError(
            "prompt generation produced no result — the provider call failed "
            "(check that the model assigned to Caption supports vision/images "
            "and is reachable; see application_logs for the provider error)"
        )
    return result


@DBOS.workflow()
async def caption_asset_workflow(
    resource_id: str,
    user_id: str,
) -> dict[str, Any]:
    """Generate gen_prompt / gen_prompt_zh for an image resource.

    workflow_id idempotency: re-running with the same id replays the
    cached LLM result instead of paying for a second vision call.
    """
    from app.repositories.resources_repository import ResourcesRepository
    from app.services.infra.unified_task_manager import get_task_manager
    from app.workflows._failure_handler import record_workflow_failure

    manager = get_task_manager()
    wf_id = DBOS.workflow_id

    try:
        async with request_scope(Scope(user_id=user_id)):
            repo = ResourcesRepository()
            resource = await repo.get_resource_by_id(resource_id)
            if not resource:
                raise RuntimeError(f"resource {resource_id} not found")
            file_path = resource.get("file_path")
            if not file_path:
                raise RuntimeError("resource has no stored file to caption")
            abs_path = str(Path(settings.DOWNLOAD_PATH) / file_path)

            cfg = await resolve_caption_provider(user_id)
            await manager.update_progress(wf_id, 20, subtitle="Provider resolved")

            result = await call_caption(
                abs_path=abs_path,
                user_id=user_id,
                resource_id=str(resource_id),
                provider_key=cfg["provider_key"],
                provider_config=cfg["provider_config"],
                agent_slug=cfg.get("agent_slug") or "caption",
                wf_id=wf_id,
            )

            await manager.update_progress(wf_id, 85, subtitle="Saving prompt...")
            update: dict[str, str] = {}
            if result.get("en"):
                update["gen_prompt"] = result["en"]
            if result.get("zh"):
                update["gen_prompt_zh"] = result["zh"]
            if not update:
                raise RuntimeError(
                    "caption agent returned neither an EN nor a ZH prompt"
                )
            await repo.update_resource(resource_id, update)

        await manager.update_progress(wf_id, 100, subtitle="Prompt generated")
        logger.info(f"[CaptionAsset] generated prompt for resource {resource_id}")
        return {
            "status": "ok",
            "resource_id": resource_id,
            "sides": sorted(update.keys()),
        }
    except Exception as e:  # noqa: BLE001
        return await record_workflow_failure(
            workflow_id=wf_id,
            error=e,
            context={
                "workflow": "caption_asset",
                "resource_id": resource_id,
                "user_id": user_id,
            },
        )
