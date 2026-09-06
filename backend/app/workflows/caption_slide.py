"""caption_slide (prompt_caption_slide) DBOS workflow.

The per-slide variant of ``caption_asset``: reverse-engineers a prompt for
ONE slide of a downloaded album and merges it into the parent resource's
``resources.slide_prompts`` JSONB map.

Reuses ``caption_asset``'s two ``@DBOS.step``s verbatim
(``resolve_caption_provider`` / ``call_caption``) — the provider
resolution and the vision call are identical; only the input file and the
destination column differ.

WHAT THIS DELIBERATELY DOES NOT DO
==================================
No AI tags. ``caption_asset`` writes 3-6 semantic tags per image, which is
right for one asset — but an album runs this once PER SLIDE, so a 26-slide
carousel would attach up to ~150 tag rows to a single resource and the
shared ``tags`` vocabulary would grow by the same order on every album.
Slide captions therefore write prompt text only; the album's resource-level
tags still come from the normal Tags picker.

MERGE DISCIPLINE
================
The write goes through ``ResourcesRepository.merge_slide_prompt``, which
read-modify-writes the WHOLE map — never a bare ``{slide_name: entry}``
that would clobber every other slide. Same rule the frontend's
``SlidePromptStrip`` follows on its own PATCH path (see that file's header);
both ends of the same column have to honour it or one of them erases the
other's work.

Route C discipline (CLAUDE.md): ``manager.create()`` happens at dispatch,
progress goes through the manager API, failures RAISE, and the tail-catch
records an ``error_catalog`` code into ``metadata.error_code``.
"""

from __future__ import annotations

from contextlib import AsyncExitStack
from pathlib import Path
from typing import Any

from dbos import DBOS
from loguru import logger

from app.db.scope import Scope, request_scope

# Module-level like caption_asset's own import of materialize — the two
# workflows share the adapter, so they should also share how they reach it.
from app.services.library.media_storage import materialize, resolve_media_source
from app.services.prompts.origin import stamp_origin
from app.workflows.caption_asset import call_caption, resolve_caption_provider


@DBOS.workflow()
async def caption_slide_workflow(
    resource_id: str,
    user_id: str,
    media_id: str,
    slide_name: str,
) -> dict[str, Any]:
    """Caption one album slide into ``slide_prompts[slide_name]``.

    workflow_id idempotency: re-running with the same id replays the
    cached vision result instead of paying for a second call.
    """
    from app.repositories.media_repository import MediaRepository
    from app.repositories.resources_repository import ResourcesRepository
    from app.services.ai.error_catalog import record_ai_error_code
    from app.services.infra.unified_task_manager import get_task_manager
    from app.services.media.slide_paths import resolve_slide_source
    from app.workflows._failure_handler import record_workflow_failure

    manager = get_task_manager()
    wf_id = DBOS.workflow_id

    try:
        async with request_scope(Scope(user_id=user_id)):
            repo = ResourcesRepository()
            resource = await repo.get_resource_by_id(resource_id)
            if not resource:
                raise RuntimeError(f"resource {resource_id} not found")

            media = await MediaRepository().get_by_id(str(media_id))
            download_path = (media or {}).get("download_path")
            if not download_path:
                raise RuntimeError(
                    f"album {media_id} has no download_path — its slides are "
                    "not on disk"
                )
            # Re-resolves (and re-guards) rather than trusting a path passed
            # through the workflow input: DBOS freezes inputs, so a stale
            # absolute path from an earlier deploy would outlive the file.
            slide_source = await resolve_slide_source(download_path, slide_name)

            await manager.update_progress(wf_id, 10, subtitle="Resolving provider")
            cfg = await resolve_caption_provider(user_id)

            await manager.update_progress(
                wf_id, 30, subtitle=f"Analyzing {slide_name}..."
            )
            # A migrated album's slide is an sb:// object; the vision call
            # takes a real path, so stream it to a temp file first (same
            # adapter caption_asset uses). Still on the filesystem →
            # resolve_slide_source already returned an absolute path, and
            # materialize() must NOT be used on it: it re-joins against
            # settings.DOWNLOAD_PATH, which can differ from the base
            # resolve_slide_file resolved under (frontend_config.yml
            # override / the /app/downloads writability fallback).
            async with AsyncExitStack() as stack:
                if resolve_media_source(slide_source).is_object_store:
                    slide_path = await stack.enter_async_context(
                        materialize(slide_source)
                    )
                else:
                    slide_path = Path(slide_source)
                result = await call_caption(
                    abs_path=str(slide_path),
                    user_id=user_id,
                    resource_id=str(resource_id),
                    provider_key=cfg["provider_key"],
                    provider_config=cfg["provider_config"],
                    agent_slug=cfg.get("agent_slug") or "caption",
                    wf_id=wf_id,
                    fallback_models=cfg.get("fallback_models") or [],
                )

            await manager.update_progress(wf_id, 70, subtitle="Parsing result")
            entry: dict[str, str] = {}
            if result.get("en"):
                entry["en"] = result["en"]
            if result.get("zh"):
                entry["zh"] = result["zh"]
            if not entry:
                raise RuntimeError(
                    "caption agent returned neither an EN nor a ZH prompt"
                )

            await manager.update_progress(wf_id, 85, subtitle="Saving slide prompt")
            # Only the positive sides are written. The caption contract has no
            # negative-prompt field, so any neg_en/neg_zh the user typed by
            # hand survives (merge_slide_prompt merges INTO the existing entry).
            # mig 455: the row-level origin follows the last writer of any
            # slide's text (spec §8 — per-slide origin is deferred). It rides
            # in the SAME flush as the text: as a second PATCH, a failure
            # between the two left the new text labelled with the previous
            # writer's origin and nothing said so.
            #
            # stamp_origin keys off the text column present in the patch, and
            # the slide text here is merged by the repository — so ask the
            # helper what a slide_prompts write stamps, and hand the
            # repository that stamp alone.
            stamp = stamp_origin({"slide_prompts": entry}, "captioned")
            await repo.merge_slide_prompt(
                resource_id,
                slide_name,
                entry,
                extra={"prompt_origin": stamp["prompt_origin"]},
            )

        await manager.update_progress(wf_id, 100, subtitle="Slide prompt generated")
        logger.info(
            f"[CaptionSlide] generated prompt for resource {resource_id} "
            f"slide {slide_name!r} ({sorted(entry.keys())})"
        )
        return {
            "status": "ok",
            "resource_id": resource_id,
            "slide_name": slide_name,
            "sides": sorted(entry.keys()),
        }
    except Exception as e:  # noqa: BLE001
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
                "workflow": "caption_slide",
                "resource_id": resource_id,
                "slide_name": slide_name,
                "user_id": user_id,
            },
        )
        raise
