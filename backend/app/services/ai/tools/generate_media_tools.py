"""Agent media-generation tool handlers (sub-plan 5, Plan 2).

GenerateImage / GenerateVideo: call ImageGenerationService for the provider URL,
register the result into the Tier-1 generated_media store with agent_run
provenance, and return a structured reference. Never raises into the agent loop.
"""

from __future__ import annotations

import os
from typing import Any, Optional

from loguru import logger

from app.services.library.generated_media_service import (
    GenerationOrigin,
    register_generated_media,
)
from app.services.library.resources_service import _resolve_personal_team_id


def _resolve_provider_model(args: dict, kind: str) -> tuple[Optional[str], str]:
    """args override → env default. kind = 'image' | 'video'."""
    prov = (args.get("provider") or "").strip() or os.environ.get(
        f"GENMEDIA_DEFAULT_{kind.upper()}_PROVIDER", ""
    ).strip()
    model = (args.get("model") or "").strip() or os.environ.get(
        f"GENMEDIA_DEFAULT_{kind.upper()}_MODEL", ""
    ).strip()
    return (prov or None, model)


async def _scope_for(run_context: dict) -> int:
    team_id = run_context.get("team_id")
    if team_id is not None:
        return int(team_id)
    return int(await _resolve_personal_team_id(str(run_context.get("user_id"))))


class GenerateMediaTools:
    def _svc(self) -> Any:
        from app.services.ai.media.image_generation_service import (
            ImageGenerationService,
        )

        return ImageGenerationService()

    async def generate_image(self, args: dict, run_context: dict) -> dict:
        prompt = (args.get("prompt") or "").strip()
        if not prompt:
            return {"ok": False, "error": "prompt is required"}
        provider, model = _resolve_provider_model(args, "image")
        if not provider:
            return {"ok": False, "error": "no image provider configured"}
        try:
            raw = await self._svc().generate_image(
                project_id="",
                node_id="",
                prompt=prompt,
                model=model,
                provider_name=provider,
                aspect_ratio=(args.get("aspect_ratio") or "16:9"),
                user_id=str(run_context.get("user_id") or "") or None,
            )
            url = (raw or {}).get("url") or (raw or {}).get("image_url")
            if not url:
                return {"ok": False, "error": "provider returned no image url"}
            row = await register_generated_media(
                user_id=str(run_context.get("user_id")),
                scope_id=await _scope_for(run_context),
                source_url=str(url),
                mime="image/png",
                origin=GenerationOrigin(
                    kind="agent_run",
                    run_id=run_context.get("run_id"),
                    agent_id=run_context.get("agent_id"),
                    prompt=prompt,
                    model=model,
                    provider=provider,
                    params={"aspect_ratio": args.get("aspect_ratio") or "16:9"},
                    derivation_kind="image_gen",
                    # 3a: the step this came out of, so the deliverable card
                    # hangs off the right one instead of the whole run.
                    turn=run_context.get("turn"),
                    step=run_context.get("step"),
                ),
            )
            return {
                "ok": True,
                "generated_media_id": (row or {}).get("id"),
                "url": url,
                "kind": "image",
            }
        except Exception as exc:  # noqa: BLE001
            logger.opt(exception=True).warning(
                "[genmedia] GenerateImage failed: {}", exc
            )
            return {"ok": False, "error": f"image generation failed: {exc}"}

    async def generate_video(self, args: dict, run_context: dict) -> dict:
        prompt = (args.get("prompt") or "").strip()
        src = (args.get("source_image_url") or "").strip()
        if not prompt or not src:
            return {"ok": False, "error": "prompt and source_image_url are required"}
        provider, model = _resolve_provider_model(args, "video")
        if not provider:
            return {"ok": False, "error": "no video provider configured"}
        try:
            raw = await self._svc().generate_video(
                project_id="",
                node_id="",
                source_image_url=src,
                prompt=prompt,
                provider_name=provider,
                model=model,
                user_id=str(run_context.get("user_id") or "") or None,
            )
            url = (raw or {}).get("url") or (raw or {}).get("video_url")
            if not url:
                return {"ok": False, "error": "provider returned no video url"}
            row = await register_generated_media(
                user_id=str(run_context.get("user_id")),
                scope_id=await _scope_for(run_context),
                source_url=str(url),
                mime="video/mp4",
                origin=GenerationOrigin(
                    kind="agent_run",
                    run_id=run_context.get("run_id"),
                    agent_id=run_context.get("agent_id"),
                    prompt=prompt,
                    model=model,
                    provider=provider,
                    params={"source_image_url": src},
                    derivation_kind="video_gen",
                    # 3a: the step this came out of, so the deliverable card
                    # hangs off the right one instead of the whole run.
                    turn=run_context.get("turn"),
                    step=run_context.get("step"),
                ),
            )
            return {
                "ok": True,
                "generated_media_id": (row or {}).get("id"),
                "url": url,
                "kind": "video",
            }
        except Exception as exc:  # noqa: BLE001
            logger.opt(exception=True).warning(
                "[genmedia] GenerateVideo failed: {}", exc
            )
            return {"ok": False, "error": f"video generation failed: {exc}"}


__all__ = ["GenerateMediaTools"]
