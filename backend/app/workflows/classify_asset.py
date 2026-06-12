"""classify_asset (asset_classify) DBOS workflow — IC-port P1-3.

Classifies an image resource along 12 dimensions via the user's assigned
``classify`` vision agent and writes the results through to the tags
system: one ``tag_groups`` row per dimension (find-or-create), bilingual
tags (``name`` EN + ``name_zh``), and ``resource_tags`` rows with
``source='ai'`` + confidence.

Dispatched by ``POST /resources/{id}/classify`` with a pre-created
task_tracking row whose ``dbos_workflow_id`` matches this workflow's id
(reference_dbos_dispatch_endpoint_wf_id). On-demand only — uploads do
NOT auto-classify (用户拍板: 自动分类默认关).

Conventions (mirrors caption_asset / analyze_l1):
  - Provider resolution + the multimodal LLM call are ``@DBOS.step``s
    returning plain dicts (memoized on replay; LLM call retries once).
  - The resource read runs under the user's ambient scope; the tags
    write-through runs under ``system_request_scope`` — the same fix
    #608 applied to keyword auto-tagging (tags/tag_groups are shared
    vocabulary tables, not tenant rows; a USER scope would reject or
    mis-filter the find-or-create paths).
  - Failure paths RAISE (路线 C rule 4).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from dbos import DBOS
from loguru import logger

from app.core.config import settings
from app.db.scope import Scope, request_scope, system_request_scope

AI_TAG_CONFIDENCE = 0.8


@DBOS.step()
async def resolve_classify_provider(user_id: Optional[str]) -> dict[str, Any]:
    """Resolve provider key + config + model + agent slug for classify."""
    from app.services.ai.providers.ai_provider_helpers import (
        resolve_classify_provider_config,
    )

    (
        provider_key,
        provider_config,
        agent_model,
        agent_slug,
    ) = await resolve_classify_provider_config(user_id)
    return {
        "provider_key": provider_key,
        "provider_config": provider_config or {},
        "agent_model": agent_model,
        "agent_slug": agent_slug,
    }


@DBOS.step(retries_allowed=True, max_attempts=2)
async def call_classify(
    abs_path: str,
    user_id: str,
    resource_id: str,
    provider_key: str,
    provider_config: dict[str, Any],
    agent_slug: str,
    wf_id: Optional[str] = None,
) -> list[dict[str, str]]:
    """Run the multimodal classification. Returns normalized tag dicts
    (``{dimension, group, en, zh}``). Raises on no-result (rule 4)."""
    from app.services.ai.classify import ClassifyService

    service = ClassifyService(
        provider_key=provider_key,
        provider_config=provider_config,
        agent_slug=agent_slug or "classify",
    )
    tags = await service.classify(
        file_path=abs_path,
        user_id=user_id,
        resource_id=resource_id,
        task_id=wf_id,
    )
    if not tags:
        raise RuntimeError(
            "classification produced no result — the provider call failed or "
            "returned nothing usable (check that the model assigned to "
            "Classification supports vision/images and is reachable; see "
            "application_logs for the provider error)"
        )
    return [
        {"dimension": t.dimension, "group": t.group, "en": t.en, "zh": t.zh}
        for t in tags
    ]


@DBOS.workflow()
async def classify_asset_workflow(
    resource_id: str,
    user_id: str,
) -> dict[str, Any]:
    """Classify an image resource and write bilingual AI tags.

    workflow_id idempotency: re-running with the same id replays the
    cached LLM result; the tag write-through is upsert-based
    (find-or-create + ON CONFLICT junction upsert) so replays are safe.
    """
    from app.repositories.resources_repository import ResourcesRepository
    from app.repositories.tags_repository import get_tags_repository
    from app.services.infra.unified_task_manager import get_task_manager
    from app.workflows._failure_handler import record_workflow_failure

    manager = get_task_manager()
    wf_id = DBOS.workflow_id

    try:
        async with request_scope(Scope(user_id=user_id)):
            resource = await ResourcesRepository().get_resource_by_id(resource_id)
        if not resource:
            raise RuntimeError(f"resource {resource_id} not found")
        file_path = resource.get("file_path")
        if not file_path:
            raise RuntimeError("resource has no stored file to classify")
        abs_path = str(Path(settings.DOWNLOAD_PATH) / file_path)

        cfg = await resolve_classify_provider(user_id)
        await manager.update_progress(wf_id, 20, subtitle="Provider resolved")

        entries = await call_classify(
            abs_path=abs_path,
            user_id=user_id,
            resource_id=str(resource_id),
            provider_key=cfg["provider_key"],
            provider_config=cfg["provider_config"],
            agent_slug=cfg.get("agent_slug") or "classify",
            wf_id=wf_id,
        )

        await manager.update_progress(
            wf_id, 80, subtitle=f"Writing {len(entries)} tags..."
        )

        # Tags/tag_groups are shared vocabulary tables — write them under
        # the system scope (#608 precedent), not the user scope.
        tags_repo = get_tags_repository()
        attached = 0
        async with system_request_scope("ai-classify-tags"):
            group_cache: dict[str, Optional[str]] = {}
            for entry in entries:
                group_name = entry["group"]
                if group_name not in group_cache:
                    group = await tags_repo.get_or_create_group(group_name)
                    group_cache[group_name] = (
                        str(group["id"]) if group and group.get("id") else None
                    )

                tag = await tags_repo.get_tag_by_name(entry["en"], user_id)
                if not tag and entry.get("zh"):
                    tag = await tags_repo.get_tag_by_name(entry["zh"], user_id)
                if not tag:
                    tag = await tags_repo.create_tag(
                        name=entry["en"],
                        user_id=user_id,
                        name_zh=entry.get("zh") or None,
                        group_id=group_cache[group_name],
                    )
                if not tag or not tag.get("id"):
                    logger.warning(
                        f"[ClassifyAsset] could not resolve tag "
                        f"'{entry['en']}' — skipped"
                    )
                    continue
                await tags_repo.add_tag_to_resource(
                    resource_id=str(resource_id),
                    tag_id=str(tag["id"]),
                    confidence=AI_TAG_CONFIDENCE,
                    source="ai",
                )
                attached += 1

        if attached == 0:
            raise RuntimeError(
                "classification returned tags but none could be written — "
                "see application_logs for the tag write errors"
            )

        await manager.update_progress(
            wf_id, 100, subtitle=f"Tagged with {attached} labels"
        )
        logger.info(
            f"[ClassifyAsset] resource {resource_id}: {attached} AI tags attached"
        )
        return {"status": "ok", "resource_id": resource_id, "tags_added": attached}
    except Exception as e:  # noqa: BLE001
        return await record_workflow_failure(
            workflow_id=wf_id,
            error=e,
            context={
                "workflow": "classify_asset",
                "resource_id": resource_id,
                "user_id": user_id,
            },
        )
