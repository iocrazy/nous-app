"""Agent media-generation tool handlers (sub-plan 5, Plan 2).

GenerateImage / GenerateVideo: call ImageGenerationService for the produced
media, register it into the Tier-1 generated_media store with agent_run
provenance, and return a structured reference. Never raises into the agent loop.

The provider hands back EITHER a url (Ark 等 URL 类 provider) OR a local file
path (CLI 类 provider——codex / jimeng-cli 把文件写进自己的临时目录再交回路径，
形状见 ``ImageGenResult`` / ``VideoGenResult``）。两种都要接：3a 补验时服务层
日志明明写着生成成功，工具却回 "provider returned no image url"，就是因为这里
只读 url（run 349426769401737）。``workflows/script_shot_generate.py`` 早就两种
都接，这里照它。

回给模型的 ``url`` 永远是登记行的服务 URL（image → ``/cover``、video →
``/stream``，与 router 的 ``_IMPORT_KIND_ENDPOINT`` 一致），不是本地路径（模型
拿到路径既无法使用也是信息泄露），也不是会过期的 provider CDN 地址。
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
from app.services.library.scratch_reaper import reap_scratch_dir


def _is_url(produced: str) -> bool:
    return produced.startswith(("http://", "https://"))


def _reap_if_local(produced: str) -> None:
    """登记完就收掉 CLI 的临时目录；它失败绝不该把生成判失败。"""
    if _is_url(produced):
        return
    try:
        reap_scratch_dir(produced)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[genmedia] scratch reap failed for {}: {}", produced, exc)


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
            return {
                "ok": False,
                "error": "prompt is required",
                "error_code": "prompt_required",
            }
        provider, model = _resolve_provider_model(args, "image")
        if not provider:
            return {
                "ok": False,
                "error": "no image provider configured",
                "error_code": "no_provider",
            }
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
            # URL provider 给 image_url；CLI provider 给 image_path（本地文件）。
            produced = str(
                (raw or {}).get("image_url") or (raw or {}).get("image_path") or ""
            )
            if not produced:
                return {
                    "ok": False,
                    "error": "provider returned no image url",
                    "error_code": "no_image",
                }
            is_url = _is_url(produced)
            try:
                row = await register_generated_media(
                    user_id=str(run_context.get("user_id")),
                    scope_id=await _scope_for(run_context),
                    source_url=produced if is_url else None,
                    source_path=None if is_url else produced,
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
                    # 3a T8c: the live run's own recorder, so the deliverable
                    # event folds into this run's views instead of a second
                    # writer's — see ``register_generated_media``.
                    recorder=run_context.get("recorder"),
                )
                gen_id = (row or {}).get("id")
                if gen_id is None:
                    raise RuntimeError("register_generated_media returned no id")
                return {
                    "ok": True,
                    "generated_media_id": gen_id,
                    "url": f"/api/v1/generated-media/{gen_id}/cover",
                    "kind": "image",
                }
            finally:
                # 入库成败都收——失败时那份文件同样没人再引用了（四个
                # workflow 调用点也都是 finally）。
                _reap_if_local(produced)
        except Exception as exc:  # noqa: BLE001
            logger.opt(exception=True).warning(
                "[genmedia] GenerateImage failed: {}", exc
            )
            return {
                "ok": False,
                "error": f"image generation failed: {exc}",
                "error_code": "generation_failed",
            }

    async def generate_video(self, args: dict, run_context: dict) -> dict:
        prompt = (args.get("prompt") or "").strip()
        src = (args.get("source_image_url") or "").strip()
        if not prompt or not src:
            return {
                "ok": False,
                "error": "prompt and source_image_url are required",
                "error_code": "prompt_required",
            }
        provider, model = _resolve_provider_model(args, "video")
        if not provider:
            return {
                "ok": False,
                "error": "no video provider configured",
                "error_code": "no_provider",
            }
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
            # URL provider 给 video_url；CLI provider 给 video_path（本地文件）。
            produced = str(
                (raw or {}).get("video_url") or (raw or {}).get("video_path") or ""
            )
            if not produced:
                return {
                    "ok": False,
                    "error": "provider returned no video url",
                    "error_code": "no_video",
                }
            is_url = _is_url(produced)
            try:
                row = await register_generated_media(
                    user_id=str(run_context.get("user_id")),
                    scope_id=await _scope_for(run_context),
                    source_url=produced if is_url else None,
                    source_path=None if is_url else produced,
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
                    # 3a T8c: the live run's own recorder, so the deliverable
                    # event folds into this run's views instead of a second
                    # writer's — see ``register_generated_media``.
                    recorder=run_context.get("recorder"),
                )
                gen_id = (row or {}).get("id")
                if gen_id is None:
                    raise RuntimeError("register_generated_media returned no id")
                # video 行在 /cover 上是 404（router 只给 media_kind == "image"
                # 发 cover），耐久 URL 走 /stream —— 与 script_shot_video.py 一致。
                return {
                    "ok": True,
                    "generated_media_id": gen_id,
                    "url": f"/api/v1/generated-media/{gen_id}/stream",
                    "kind": "video",
                }
            finally:
                _reap_if_local(produced)
        except Exception as exc:  # noqa: BLE001
            logger.opt(exception=True).warning(
                "[genmedia] GenerateVideo failed: {}", exc
            )
            return {
                "ok": False,
                "error": f"video generation failed: {exc}",
                "error_code": "generation_failed",
            }


__all__ = ["GenerateMediaTools"]
