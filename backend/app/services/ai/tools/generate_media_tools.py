"""Agent media-generation tool handlers (sub-plan 5, Plan 2).

GenerateImage: call ImageGenerationService for the produced media, register it
into the Tier-1 generated_media store with agent_run provenance, and return a
structured reference. GenerateVideo: submit-only — file a task and start
``workflows/agent_video.py``, whose outcome comes back later as an inbox
message (see ``generate_video``). Never raises into the agent loop.

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

import hashlib
import os
import uuid
from dataclasses import dataclass
from typing import Any, Optional

from loguru import logger

from app.services.ai.media.gen_attribution import resolved_attribution
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
            # 归因写 adapter 解析出的真值：请求侧的 provider 是目录行名、
            # model 常为空，按 (model, provider) 查价一定落空。
            gen_provider, gen_model = resolved_attribution(
                raw or {}, requested_provider=provider, requested_model=model
            )
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
                        model=gen_model,
                        provider=gen_provider,
                        params={"aspect_ratio": args.get("aspect_ratio") or "16:9"},
                        derivation_kind="image_gen",
                        # adapter 解析出来的层标记（image_generation_service 在
                        # 返回 dict 上并列注入）。用户自己的 key 出的图不收积分。
                        byok=bool((raw or {}).get("byok")),
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
        """Submit-only: validate, pick the row, file a task, start
        ``agent_video_workflow``, return at once.

        The generation never runs in the turn. A local-daemon job may take
        ~27 min (``local_dispatch.DREAMINA_DISPATCH_TIMEOUT_S``), far past any
        tool wall clock (this one's is 60s, ``runner/tool_timeouts.py``), so
        waiting here abandoned jobs the user's machine was still running. The outcome reaches the model later as an
        ``agent_run_inbox`` item of kind ``media_result`` (delivery per target:
        ``workflows/agent_video.py``).

        Only model-safe fields come back: no local path, no provider row
        internals, no ticket.
        """
        prompt = (args.get("prompt") or "").strip()
        src = (args.get("source_image_url") or "").strip()
        if not prompt or not src:
            return _refuse(
                "prompt_required", "prompt and source_image_url are required"
            )
        provider, model = _resolve_provider_model(args, "video")
        if not provider:
            return _refuse("no_provider", "no video provider configured")
        user_id = str(run_context.get("user_id") or "")
        if not user_id:
            return _refuse("no_user", "this run has no user to generate for")
        target = _reply_target(run_context)
        if target is None:
            # Nowhere to deliver the result: refuse rather than run a long job
            # nobody will ever read (same rule as ``Task(await=false)``).
            return _refuse(
                "no_reply_target",
                "this run has no issue or conversation to deliver the video to",
            )

        from app.services.media.parsers.video_providers import db_registry

        try:
            route = await db_registry.resolve_video_route(provider, user_id=user_id)
        except Exception as exc:  # noqa: BLE001 - typed refusal to the model
            logger.warning("[genmedia] GenerateVideo route failed: {}", exc)
            return _refuse("no_video_model", f"no usable video model: {exc}")
        local = isinstance(route, db_registry.LocalVideoRoute)
        if local and not await _daemon_online(user_id):
            # Fail fast: the workflow would learn the same thing seconds
            # later, but the model can tell the user NOW. A daemon that drops
            # after this check is still handled — and delivered — by the
            # workflow.
            return _refuse(
                "daemon_offline",
                "the user's local nous-codex daemon is not connected; ask them "
                "to start it (nous-codex run) and try again",
            )

        from app.services.infra.unified_task_manager import get_task_manager

        dedup_key = _video_dedup_key(target, prompt, src)
        try:
            existing = await get_task_manager().find_active_by_dedup_key(
                user_id, VIDEO_TASK_TYPE, dedup_key
            )
        except Exception as exc:  # noqa: BLE001 - an unreadable guard is said
            logger.opt(exception=True).error(
                "[genmedia] GenerateVideo duplicate guard failed: {}", exc
            )
            return _refuse("dispatch_failed", "could not check for a running job")
        if existing:
            return _refuse(
                "already_generating",
                f"this exact video is already rendering (task {existing}); its "
                "result will arrive as an inbox message - do not re-submit",
            )
        return await _submit_video(
            run_context,
            _VideoJob(
                prompt=prompt,
                src=src,
                provider=provider,
                model=model,
                user_id=user_id,
                target=target,
                local=local,
                dedup_key=dedup_key,
            ),
        )


#: task_tracking.task_type is VARCHAR(20).
VIDEO_TASK_TYPE = "agent_video"

_VIDEO_NOTE_TAIL = (
    "The result will arrive later as an inbox message; do not poll or re-submit."
)


@dataclass(frozen=True)
class _VideoJob:
    prompt: str
    src: str
    provider: str
    model: str
    user_id: str
    target: tuple[str, int]
    local: bool
    dedup_key: str


async def _submit_video(run_context: dict, job: _VideoJob) -> dict:
    """File the task row and start the workflow. Never raises."""
    from app.services.infra.dbos_orchestrator import start_workflow_routed
    from app.services.infra.unified_task_manager import get_task_manager
    from app.workflows.agent_video import agent_video_workflow

    run_id = run_context.get("run_id")
    kind, target_id = job.target
    wf_id = str(uuid.uuid4())
    try:
        task_id = await get_task_manager().create(
            user_id=job.user_id,
            task_type=VIDEO_TASK_TYPE,
            title="Generate video (agent)",
            dbos_workflow_id=wf_id,
            metadata={
                "trigger": "agent_tool",
                "run_id": run_id,
                "reply_to": {"target_kind": kind, "target_id": target_id},
                "route": "local" if job.local else "server",
            },
            dedup_key=job.dedup_key,
        )
        dispatch = await start_workflow_routed(
            VIDEO_TASK_TYPE,
            dbos_workflow_callable=agent_video_workflow,
            dbos_workflow_kwargs={
                "prompt": job.prompt,
                "source_image_url": job.src,
                "provider": job.provider,
                "model": job.model,
                "user_id": job.user_id,
                "team_id": run_context.get("team_id"),
                "agent_id": run_context.get("agent_id"),
                "run_id": run_id,
                "turn": run_context.get("turn"),
                "step": run_context.get("step"),
                "conversation_id": run_context.get("conversation_id"),
                "reply_to_kind": kind,
                "reply_to_id": target_id,
            },
            workflow_id=wf_id,
            # Recorded only on the deferred path (issue turns run inside a
            # DBOS step): lets the draining body FAIL this row if it cannot
            # start the workflow. Never reaches the workflow.
            task_id=task_id,
        )
    except Exception as exc:  # noqa: BLE001 - never raise into the loop
        logger.opt(exception=True).error(
            "[genmedia] GenerateVideo dispatch failed: {}", exc
        )
        return _refuse(
            "dispatch_failed",
            f"could not submit the video job: {exc.__class__.__name__}",
        )
    logger.info(
        "[genmedia] GenerateVideo submitted run={} task={} target={}:{} local={}",
        run_id,
        task_id,
        kind,
        target_id,
        job.local,
    )
    where = (
        "Rendering on the user's machine (can take up to ~27 min)."
        if job.local
        else "Rendering on the server (can take several minutes)."
    )
    result: dict[str, Any] = {
        "ok": True,
        "status": "submitted",
        "task_id": task_id,
        "note": f"{where} {_VIDEO_NOTE_TAIL}",
    }
    if (dispatch or {}).get("deferred"):
        # Issue turns: the workflow starts when this step ends. Reported, not
        # smoothed over — "queued" is not "running".
        result["deferred"] = True
    return result


def _refuse(code: str, message: str) -> dict:
    return {"ok": False, "error": message, "error_code": code}


def _reply_target(run_context: dict) -> Optional[tuple[str, int]]:
    """Where the finished video goes back to: the run's issue first, then its
    conversation — the order ``SubAgentTaskService._reply_target`` uses. An
    unaddressable id is the same as none (never a ValueError into the loop).
    """
    for kind in ("issue", "conversation"):
        raw = run_context.get(f"{kind}_id")
        if not raw:
            continue
        try:
            return (kind, int(raw))
        except (TypeError, ValueError):
            logger.warning("[genmedia] unusable {} reply target {!r}", kind, raw)
    return None


def _video_dedup_key(target: tuple[str, int], prompt: str, src: str) -> str:
    """Same target + same prompt + same source = the same job. Hashed: the
    prompt is unbounded and the task row keeps it in metadata anyway."""
    digest = hashlib.sha256(f"{prompt}\x00{src}".encode("utf-8")).hexdigest()[:24]
    return f"agent_video:{target[0]}:{target[1]}:{digest}"


async def _daemon_online(user_id: str) -> bool:
    """Best-effort presence probe. A probe that cannot answer has not proved
    the daemon is offline, so a failure reads as online and the workflow
    reports the truth."""
    try:
        from app.services.codex.daemon_dispatch import RedisDaemonTransport

        return bool(await RedisDaemonTransport().is_online(str(user_id)))
    except Exception as exc:  # noqa: BLE001 - see docstring
        logger.warning("[genmedia] daemon presence probe failed: {}", exc)
        return True


__all__ = ["GenerateMediaTools", "VIDEO_TASK_TYPE"]
