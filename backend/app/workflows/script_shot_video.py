"""script_shot_video DBOS workflow — single-shot video generation (Phase B P3,
PR-J2 Task 5, flag ``FEATURE_SHOT_VIDEO``).

The video sibling of ``script_shot_generate``: resolve the DB-catalog video
provider (jimeng-cli / seedance today), run ``generate_video``, persist the
produced local clip through the Tier-1 ``generated_media`` store, and write a
durable same-origin URL onto the shot's ``video_url`` column.

Two decisions worth reading (both recorded here so a future editor doesn't
"fix" them):

1. **image2video vs text2video.** ``shot.image_url`` is a same-origin
   ``/api/v1/generated-media/{id}/cover`` URL, NOT a local file path — but the
   CLI's ``image2video`` needs a real local file. So ``_resolve_local_image_for_i2v``
   bridges the cover URL back to the referenced ``generated_media`` row and
   hands a readable local path to ``image2video`` — filesystem rows directly,
   object-store rows (``sb://``) via ``materialize()`` to a temp file held for
   the duration of the generation. A raw provider/ephemeral image url or any
   miss → ``None`` → the step falls back to ``text2video``.

2. **The shot's ``status`` column is the IMAGE lane's state machine
   (empty→generating→done/failed) and this workflow MUST NOT clobber it.** A
   shot can carry a done image AND generate a video; a video failure must never
   make the image look failed. So the video lifecycle (queued/in_progress/
   failed) lives entirely in ``task_tracking`` (task_type='shot_video', which the
   Task Center already surfaces — route C), and the only durable shot-row write
   is ``video_url`` on success, written via ``update_status`` with the shot's
   CURRENT status passed through unchanged (``update_status`` requires a status
   arg; re-writing the existing value is a no-op for that column). No new column,
   no migration. On failure the workflow simply ``raise``s — DBOS records FAILED,
   task_tracking mirrors it; the shot row is left untouched (no phantom 'failed'
   image state).

Persistence (route C): unlike the image path, a jimeng video product is ALWAYS
a local file (no ephemeral CDN url to fall back to), so a persist failure has no
durable artifact to keep — ``persist_video_generation`` raises rather than
degrading. The jimeng ``jimeng_`` scratch dir is reaped after ingest either way
(H1 discipline, shared with ``script_shot_generate``).
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Optional

from dbos import DBOS
from loguru import logger

from app.services.ai.media.gen_attribution import resolved_attribution
from app.services.library.scratch_reaper import reap_scratch_dir
from app.workflows.script_shot_generate import (
    _compose_prompt,
    _resolve_scope_id,
    _step_output,
)

# Default video aspect — shots carry no aspect field; 16:9 is the cinematic
# default (the provider maps it to the CLI --ratio).
_DEFAULT_ASPECT = "16:9"
_VIDEO_MIME = "video/mp4"


@asynccontextmanager
async def _resolve_local_image_for_i2v(
    shot: dict[str, Any],
) -> AsyncIterator[Optional[str]]:
    """Resolve the shot's image to a local file path for image2video, or None.

    ``shot.image_url`` is a same-origin ``/cover`` URL (see decision 1).
    Delegates to the shared generated-media bridge (G4-B0 extracted it so the
    canvas video fallback shares one implementation, containment guard
    included). Both filesystem AND object-store images resolve now (Task 2:
    ``generated_media_local_path`` materializes sb:// rows to a temp file,
    deleted when this block exits); any other miss → None (the caller falls
    back to text2video)."""
    from app.services.library.generated_media_service import (
        generated_media_local_path,
    )

    image_url = str((shot or {}).get("image_url") or "")
    async with generated_media_local_path(image_url, media_kind="image") as path:
        yield path


def _canonical_provider(provider_key: Optional[str]) -> Optional[str]:
    """目录行的 ``actual_provider`` → 协议的**规范 key**（别名归一）。

    出图链的三个 adapter 写进 ``ImageGenResult.provider`` 的都是协议自己的
    ``key``（``ark_image.py`` 写死 ``"ark"``、``jimeng.py`` 写死
    ``"jimeng-cli"``、``codex.py`` 用构造时传进来的 ``self.key``——
    ``codex`` 或 ``openai-images``），从来不是别名。而出视频链手上的
    ``provider_key`` 是目录行的 ``actual_provider`` 原文，可能是别名
    （``ark.py:16`` ``doubao``、``jimeng.py:65`` ``jimeng``）。同一个 provider
    在两条链上拼法不同，按 ``(model, provider)`` 查价就会有一半落空。

    认不出来的值原样留着——瞎猜一个规范名比诚实地记下原文更糟。
    """
    from app.services.ai.provider_protocols import resolve_generation_protocol

    if not provider_key:
        return None
    protocol = resolve_generation_protocol(provider_key)
    return protocol.key if protocol is not None else provider_key


@DBOS.step(retries_allowed=True, max_attempts=3)
async def generate_shot_video_step(
    shot_id: str,
    model: Optional[str],
    provider: Optional[str],
    user_id: Optional[str] = None,
) -> dict[str, str]:
    """Read the shot + scene, compose the prompt, and run the video provider.

    Returns ``{"path", "provider", "model"}`` — the produced clip's LOCAL file
    path (jimeng writes to disk, no URL) plus the attribution the catalog
    actually resolved. The request's ``model`` / ``provider`` are only the
    catalog LOOKUP NAME (they are what gets handed to ``resolve_video_provider``
    below), so they are not an answer to "which model ran" — the resolved row's
    ``actual_model`` and the stamped ``provider_key`` are. Uses image2video when
    the shot already has a filesystem-backed image, else text2video. Raises if
    the shot is missing or the provider yields no file."""
    from app.repositories.script_scene_repository import get_script_scene_repository
    from app.repositories.script_shot_repository import get_script_shot_repository
    from app.services.media.parsers.video_providers.db_registry import (
        resolve_video_provider,
    )

    shot = await get_script_shot_repository().get_by_id(shot_id)
    if not shot:
        raise ValueError(f"Shot not found: {shot_id}")
    scene = await get_script_scene_repository().get_by_id(str(shot.get("scene_id")))
    prompt = _compose_prompt(shot, scene)

    provider_obj, actual_model = await resolve_video_provider(
        provider or model or None, user_id=user_id
    )

    async with _resolve_local_image_for_i2v(shot) as image_path:
        result = await provider_obj.generate_video(
            prompt=prompt,
            aspect=_DEFAULT_ASPECT,
            model_version=actual_model or model or None,
            image_path=image_path,
        )
    local_path = getattr(result, "local_path", None)
    if not local_path:
        raise RuntimeError(f"Video provider returned no file for shot {shot_id}")
    # jimeng 的 ``GenResult`` 不带归因（只有 local_path / mime / raw），所以真值
    # 取自解析结果本身：``_stamp_provider_key`` 盖的 provider_key + actual_model。
    gen_provider, gen_model = resolved_attribution(
        {
            "provider": _canonical_provider(
                getattr(provider_obj, "provider_key", None)
            ),
            "model": actual_model,
        },
        requested_provider=provider,
        requested_model=model,
    )
    logger.info(
        "[script_shot_video][step] shot {} → {} ({})",
        shot_id,
        local_path,
        "image2video" if image_path else "text2video",
    )
    return {
        "path": local_path,
        "provider": gen_provider or "",
        "model": gen_model or "",
    }


@DBOS.step()
async def persist_video_generation(
    shot_id: str,
    local_path: str,
    model: Optional[str],
    provider: Optional[str],
    user_id: Optional[str],
    run_id: Optional[int] = None,
    turn: Optional[int] = None,
    step: Optional[int] = None,
    # 归因：目录真正解析出的那一行。keyword-only + 默认 None = DBOS 冻结输入
    # 兼容（部署前排队的 workflow 恢复时没有这两个）。
    *,
    resolved_provider: Optional[str] = None,
    resolved_model: Optional[str] = None,
) -> str:
    """Persist the local clip through the generated-media store → durable URL.

    Returns the same-origin ``/api/v1/generated-media/{id}/stream`` URL (a
    token-free serving endpoint, so a bare ``<video src>`` can load it). Unlike
    the image path there is NO ephemeral-url fallback (a jimeng video is always a
    local file), so any failure — missing user_id, unresolvable scope, ingest
    error — ``raise``s (route C: the workflow fails, task_tracking mirrors it).
    The ``jimeng_`` scratch dir is reaped after ingest regardless (H1)."""
    from app.repositories.script_scene_repository import get_script_scene_repository
    from app.repositories.script_shot_repository import get_script_shot_repository
    from app.services.library.generated_media_service import (
        GenerationOrigin,
        register_generated_media,
    )

    try:
        if not user_id:
            raise ValueError(f"shot {shot_id} video persist has no user_id")

        shot = await get_script_shot_repository().get_by_id(shot_id)
        if not shot:
            raise ValueError(f"Shot not found: {shot_id}")
        scene = await get_script_scene_repository().get_by_id(str(shot.get("scene_id")))
        prompt = _compose_prompt(shot, scene)
        scope_id = await _resolve_scope_id(scene, str(user_id))

        row = await register_generated_media(
            user_id=str(user_id),
            scope_id=scope_id,
            source_path=local_path,
            mime=_VIDEO_MIME,
            origin=GenerationOrigin(
                kind="shot_video",
                node_id=str(shot_id),
                prompt=prompt,
                # 请求侧的 model/provider 是目录**行名**（step 拿它查表），
                # 按 (model, provider) 查价要的是真正跑了的那一行。
                # 回退到 ``model`` 不是将就：``actual_model`` 为空时 step 给 CLI
                # 的就是它（``generate_video(model_version=actual_model or model
                # or None)``），所以这个值确实是跑了的那个。出图链那边回退到
                # None，是因为它的 ``model`` 默认是 ``dall-e-3`` 哨兵——一个
                # 假模型名，两边的规则都是「只写真跑过的东西」。
                model=resolved_model or model,
                provider=resolved_provider or provider,
                derivation_kind="shot_video",
                # BYOK 标记刻意不接线：视频目录今天没有 BYOK 层，接一个恒
                # False 的参数只是让人以为这里有判断。真出现 BYOK 视频行时，
                # 照出图链那条做（``script_shot_generate.persist_generation``
                # 的 ``byok`` 参数 → ``GenerationOrigin.byok``）。
                # 3a：run 上下文在派发时就丢了，这里回填，否则这条路
                # 产出的视频永远没有 run 可挂（真栈缺口，spec §1.3）。
                run_id=run_id,
                turn=turn,
                step=step,
            ),
        )
        gen_id = row.get("id")
        if gen_id is None:
            raise RuntimeError("register_generated_media returned no id")
        durable = f"/api/v1/generated-media/{gen_id}/stream"
        logger.info(
            "[script_shot_video][persist] shot {} → generated_media {} ({})",
            shot_id,
            gen_id,
            durable,
        )
        return durable
    finally:
        reap_scratch_dir(local_path)


@DBOS.step()
async def mark_shot_video_done(shot_id: str, video_url: str) -> None:
    """Write ``video_url`` onto the shot WITHOUT touching the image-lane status.

    Uses the repo's single-column ``update_video_url`` (a bare UPDATE of
    ``video_url``) rather than a read-modify-write of ``status`` — the latter
    would race a concurrent image generation flipping ``status`` to 'done' and
    clobber it (see decision 2)."""
    from app.repositories.script_shot_repository import get_script_shot_repository

    await get_script_shot_repository().update_video_url(shot_id, video_url)


@DBOS.workflow()
async def script_shot_video_workflow(
    shot_id: str,
    *,
    model: Optional[str] = None,
    provider: Optional[str] = None,
    user_id: Optional[str] = None,
    # 3a: the dispatching run, threaded through so the produced clip can be
    # registered against it. Keyword-defaulted for frozen DBOS input compat.
    run_id: Optional[int] = None,
    turn: Optional[int] = None,
    step: Optional[int] = None,
) -> dict[str, Any]:
    """DBOS orchestrator: shot → generated video url on the shot row.

    - input: shot_id (bigint str) + optional model/provider catalog hints + user_id
    - output: {status, shot_id, video_url}
    - side-effects: sets shot.video_url (NOT shot.status — see module docstring)

    On any failure the error propagates (route C: DBOS records FAILED and
    task_tracking mirrors it); the shot row is left untouched so a video failure
    never corrupts the image lane. ``user_id`` is optional (frozen DBOS input
    compat) but a real value is required to persist."""
    step_out = await generate_shot_video_step(shot_id, model, provider, user_id)
    # 第四格是层标记，出视频这条路上恒 False —— ``resolve_video_provider``
    # 只读平台目录（``_enabled_rows("video")``），没有 BYOK 层可读。共用
    # ``_step_output`` 是为了只有一处知道 step 返回值的形状。
    local_path, gen_provider, gen_model, _byok = _step_output(step_out, key="path")
    video_url = await persist_video_generation(
        shot_id,
        local_path,
        model,
        provider,
        user_id,
        run_id,
        turn,
        step,
        resolved_provider=gen_provider,
        resolved_model=gen_model,
    )
    await mark_shot_video_done(shot_id, video_url)
    return {"status": "success", "shot_id": shot_id, "video_url": video_url}
