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
    write-through runs under ``system_request_scope`` (via the shared
    ``write_ai_tags`` helper — same sequence ``caption_asset`` now
    reuses for its own semantic tags) — the same fix #608 applied to
    keyword auto-tagging (tags/tag_groups are shared vocabulary tables,
    not tenant rows; a USER scope would reject or mis-filter the
    find-or-create paths).
  - Failure paths RAISE (路线 C rule 4). The tail-catch also records an
    ``error_catalog`` code into ``metadata.error_code`` (added
    2026-08-12-batch-fallback-rollout §1-F2 — caption_asset already had
    this, classify_asset did not) — ``error_msg`` is trigger-owned, so
    that is the only place a user-actionable classification can live (see
    ``app/services/ai/error_catalog.py``).
"""

from __future__ import annotations

from typing import Any, Optional

from dbos import DBOS
from loguru import logger

from app.db.scope import Scope, request_scope
from app.services.library.media_storage import materialize
from app.workflows._ai_tags import AI_TAG_CONFIDENCE, write_ai_tags

__all__ = [
    "AI_TAG_CONFIDENCE",
    "resolve_classify_provider",
    "call_classify",
    "classify_asset_workflow",
]


@DBOS.step()
async def resolve_classify_provider(user_id: Optional[str]) -> dict[str, Any]:
    """Resolve provider key + config + model + agent slug + fallback models
    for classify.

    Calls ``resolve_task_ai_config`` directly instead of the tuple-shim
    ``resolve_classify_provider_config`` (which discards ``fallback_models``
    down to 4 positional fields) so the typed ``ResolvedAIConfig.fallback_models``
    rides along into the step's return dict, threaded to ``call_classify`` ->
    ``ClassifyService`` (spec 2026-08-12-batch-fallback-rollout §1-F2,
    mirrors ``caption_asset.resolve_caption_provider``). ⚠️ the agent slug
    is ``classify`` but the module gate / task_assignment key is
    ``classification``."""
    from app.services.ai.providers.ai_provider_helpers import (
        DEFAULT_CLASSIFY_AGENT_SLUG,
        resolve_task_ai_config,
    )

    cfg = await resolve_task_ai_config(
        user_id, "classification", DEFAULT_CLASSIFY_AGENT_SLUG
    )
    return {
        "provider_key": cfg.provider_key,
        "provider_config": cfg.provider_config or {},
        "agent_model": cfg.model,
        "agent_slug": cfg.agent_slug,
        "fallback_models": list(cfg.fallback_models),
    }


@DBOS.step(retries_allowed=True, max_attempts=1)
async def call_classify(
    abs_path: str,
    user_id: str,
    resource_id: str,
    provider_key: str,
    provider_config: dict[str, Any],
    agent_slug: str,
    wf_id: Optional[str] = None,
    fallback_models: Optional[list[str]] = None,
) -> list[dict[str, str]]:
    """Run the multimodal classification. Returns normalized tag dicts
    (``{dimension, group, en, zh}``). Raises on no-result (rule 4).

    ``max_attempts=1`` (was 2): retry + fallback now live entirely in
    ``LLMFallbackChain`` (via ``ClassifyService.classify()``'s
    ``build_fallback_llm`` wiring) — a step-level retry on top would
    multiply attempts (spec 2026-08-12-batch-fallback-rollout §1-F2).
    """
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
        fallback_models=fallback_models,
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
    from app.services.ai.error_catalog import record_ai_error_code
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

        cfg = await resolve_classify_provider(user_id)
        await manager.update_progress(wf_id, 20, subtitle="Provider resolved")

        async with materialize(file_path) as local_path:
            entries = await call_classify(
                abs_path=str(local_path),
                user_id=user_id,
                resource_id=str(resource_id),
                provider_key=cfg["provider_key"],
                provider_config=cfg["provider_config"],
                agent_slug=cfg.get("agent_slug") or "classify",
                wf_id=wf_id,
                fallback_models=cfg.get("fallback_models") or [],
            )

        await manager.update_progress(
            wf_id, 80, subtitle=f"Writing {len(entries)} tags..."
        )

        # Tags/tag_groups are shared vocabulary tables — write them under
        # the system scope (#608 precedent), not the user scope.
        attached = await write_ai_tags(
            resource_id=resource_id,
            user_id=user_id,
            entries=entries,
            scope_name="ai-classify-tags",
            log_prefix="[ClassifyAsset]",
        )

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
        # Translate the raw failure into a stable error code the frontend
        # can turn into actionable copy (a provider 401/429 otherwise
        # reaches the user as "Step … exceeded its maximum of N retries").
        # Writes metadata only — error_msg/phase stay trigger-owned — and
        # never raises, so the failure path below is unchanged (mirrors
        # caption_asset.py / ai_summary.py).
        await record_ai_error_code(wf_id, e)
        # Route-C rule 4: record for task_tracking/UI, then RE-RAISE so
        # DBOS records ERROR — returning the dict made DBOS mark this
        # workflow SUCCESS while task_tracking said failed (same violation
        # fixed for ai_summary/ai_transcription/analyze_l1, observed live
        # 2026-08-08, wf 5a872175/1e63f80b).
        await record_workflow_failure(
            workflow_id=wf_id,
            error=e,
            context={
                "workflow": "classify_asset",
                "resource_id": resource_id,
                "user_id": user_id,
            },
        )
        raise
