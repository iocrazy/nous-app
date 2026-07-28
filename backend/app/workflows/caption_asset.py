"""caption_asset (prompt_caption) DBOS workflow — IC-port P1-2.

Reverse-engineers a generation prompt for an image resource via the
user's assigned ``caption`` vision agent and writes it to
``resources.gen_prompt`` / ``gen_prompt_zh`` / ``gen_prompt_json``
(upgraded to the three-format contract 2026-07-28), plus 3-6 semantic
tags written through to the tags system (``source='ai'`` — same
find-or-create sequence ``classify_asset`` uses, via the shared
``write_ai_tags`` helper).

Dispatched by ``POST /resources/{id}/gen-prompt/generate`` with a
pre-created task_tracking row whose ``dbos_workflow_id`` matches this
workflow's id (reference_dbos_dispatch_endpoint_wf_id).

Design (mirrors analyze_l1 / upload_postprocess conventions):
  - Provider resolution and the multimodal LLM call are ``@DBOS.step``s
    (primitives in/out, memoized on replay; the LLM call retries once).
  - The resource read/write happens in the workflow body inside the
    user's ambient scope (same as upload_postprocess); the tags
    write-through happens AFTER that scope exits, under
    ``system_request_scope`` (mirrors classify_asset's non-nesting
    pattern — tags/tag_groups are shared vocabulary, not tenant rows).
  - Failure paths RAISE (CLAUDE.md 路线 C rule 4) so task_tracking shows
    the real reason instead of a phantom "completed".
"""

from __future__ import annotations

import json
from typing import Any, Optional

from dbos import DBOS
from loguru import logger

from app.db.scope import Scope, request_scope
from app.services.library.media_storage import materialize


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
) -> dict[str, Any]:
    """Run the multimodal caption call.

    Returns ``CaptionService.caption()``'s result dict — always has
    ``en``/``zh`` when usable, and may additionally carry
    ``prompt_json`` / ``tags`` / ``category`` when the model's reply
    matched the strict structured contract (degrades to the two-field
    shape otherwise; see ``parse_caption_result``).

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
    """Generate gen_prompt / gen_prompt_zh / gen_prompt_json + AI tags for
    an image resource.

    workflow_id idempotency: re-running with the same id replays the
    cached LLM result instead of paying for a second vision call.
    """
    from app.repositories.resources_repository import ResourcesRepository
    from app.services.infra.unified_task_manager import get_task_manager
    from app.workflows._ai_tags import write_ai_tags
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

            await manager.update_progress(wf_id, 10, subtitle="Resolving provider")
            cfg = await resolve_caption_provider(user_id)

            await manager.update_progress(wf_id, 30, subtitle="Analyzing image...")
            async with materialize(file_path) as local_path:
                result = await call_caption(
                    abs_path=str(local_path),
                    user_id=user_id,
                    resource_id=str(resource_id),
                    provider_key=cfg["provider_key"],
                    provider_config=cfg["provider_config"],
                    agent_slug=cfg.get("agent_slug") or "caption",
                    wf_id=wf_id,
                )

            await manager.update_progress(wf_id, 70, subtitle="Parsing result")
            update: dict[str, Any] = {}
            if result.get("en"):
                update["gen_prompt"] = result["en"]
            if result.get("zh"):
                update["gen_prompt_zh"] = result["zh"]
            if not update:
                raise RuntimeError(
                    "caption agent returned neither an EN nor a ZH prompt"
                )
            # Always write gen_prompt_json (even as NULL) so a degraded
            # re-run (structured contract missed this time) clears the
            # PREVIOUS run's stale JSON instead of leaving it behind —
            # repo.update_resource is load-then-setattr, so None clears the
            # column correctly.
            prompt_json = result.get("prompt_json")
            update["gen_prompt_json"] = (
                json.dumps(prompt_json, ensure_ascii=False)
                if isinstance(prompt_json, dict) and prompt_json
                else None
            )

            await manager.update_progress(wf_id, 85, subtitle="Saving prompt & tags")
            await repo.update_resource(resource_id, update)

        # Tags/tag_groups are shared vocabulary tables — write them under
        # the system scope (#608 precedent, same as classify_asset), after
        # the user request_scope above has exited (non-nesting pattern).
        #
        # Group name is a FIXED constant, not the LLM's free-form
        # `category` — tag_groups is a shared, unscoped table, so an
        # unbounded, unwhitelisted string from the model would grow it
        # without limit. `category` is still surfaced to the UI via
        # gen_prompt_json (already folded in above).
        attached = 0
        tags = result.get("tags") or []
        if tags:
            entries = [
                {"group": "Prompt", "en": t["en"], "zh": t.get("zh", "")}
                for t in tags
                if isinstance(t, dict) and t.get("en")
            ]
            if entries:
                # A tag-write failure must NOT fail the whole task — the
                # prompt (gen_prompt/gen_prompt_zh/gen_prompt_json) already
                # committed above, and the frontend's onGenerated() callback
                # depends on this workflow completing to refetch it.
                try:
                    attached = await write_ai_tags(
                        resource_id=resource_id,
                        user_id=user_id,
                        entries=entries,
                        scope_name="ai-caption-tags",
                        log_prefix="[CaptionAsset]",
                    )
                except Exception as tag_err:  # noqa: BLE001
                    logger.warning(
                        f"[CaptionAsset] tag write failed for resource "
                        f"{resource_id}: {tag_err}"
                    )
                    attached = 0

        await manager.update_progress(wf_id, 100, subtitle="Prompt & tags generated")
        logger.info(
            f"[CaptionAsset] generated prompt for resource {resource_id} "
            f"({attached} AI tags attached)"
        )
        return {
            "status": "ok",
            "resource_id": resource_id,
            "sides": sorted(update.keys()),
            "tags_added": attached,
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
