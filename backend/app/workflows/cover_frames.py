"""cover_frames DBOS workflow —— 从视频抽候选封面帧。

一次 workflow run = 一个视频的一次抽帧。编排只有一步实活：

    materialize 源视频 → ffmpeg 均匀取 N 帧 → 候选清单（小预览图 + 时间点）
    写进 task_tracking.metadata，前端订阅 Realtime 取

为什么是 workflow 而不是同步端点
==============================
抽帧要下载整个源视频（对象存储，可能几个 GB）再跑 N 次 ffmpeg seek，秒级到
分钟级都可能。挂在 HTTP 请求上等于把网关线程占住、把超时交给反向代理去决定。
与之相对，**选帧**是同步端点（见 ``cover_frames.derive_cover_pair``）——
那一步只是裁一张已经存在的图片。

路线 C 纪律（CLAUDE.md「任务系统架构纪律」）
==========================================
1. ``task_tracking`` 行由**端点**建（``manager.create(dbos_workflow_id=wf_id)``
   与 ``start_workflow_routed(workflow_id=wf_id)`` 同一个 id），workflow 里只
   调 ``start`` / ``complete`` / ``fail``，绝不 PATCH ``phase`` / ``status`` /
   ``progress`` —— 那几列归 ``mirror_dbos_lifecycle_to_tracking`` trigger。
2. **失败一律 raise**，绝不 ``return {"status": "failed"}``（纪律 4）。返回
   dict 会被 DBOS 判成 SUCCESS，trigger 于是把任务标 completed，用户看到的是
   "完成了但一张候选帧都没有"。
3. 候选清单是业务装饰字段，走 ``metadata``（纪律 3 明确允许）。走的是
   ``complete(metadata_patch=...)`` 而不是 ``update_progress`` —— 后者对同一
   task 有 1 write/sec 节流且会**整条丢弃**（含 metadata_patch），而候选清单
   丢一次这次抽帧就白跑了。
   ⚠️ metadata 里带的是 base64 预览，但**只能是小图**：宽 240、每张 ≤14 KB，
   按 12 张上限合起来 ≈224 KB base64，离 Realtime 的行上限有数倍余量 —— 而且
   这个预算由 ``cover_frames._fit_preview`` 按构造保证，不是靠"通常不会很大"。
   原始尺寸（1080 宽，6 张 ≈1.5 MB）才是给实时通道灌洪水，那个从不送。

上界（§7.2）
===========
没有 ``while True``。抽帧的两道上界都在 service 层（``_TOTAL_DEADLINE_SECONDS``
罩住下载+抽帧，``_EXTRACT_TIMEOUT_SECONDS`` 罩住 ffmpeg），超时会变成
``CoverFrameError(504)``。这里额外裹 ``async_heartbeat_loop``，让 stall
detector 在长下载期间看得见这个任务还活着。

临时文件
=======
本 workflow 与 service 都不创建临时文件；清理由被复用的三个组件各自的
``finally`` / ``TemporaryDirectory`` 承担，说明见
``app/services/distribution/cover_frames.py`` 的模块 docstring。
"""

from __future__ import annotations

from typing import Any, Optional

from dbos import DBOS
from loguru import logger

from app.services.distribution.cover_frames import (
    DEFAULT_COVER_FRAMES,
    MAX_COVER_FRAMES,
    CoverFrameError,
)

TASK_TYPE = "cover_frames"  # ≤20 chars — task_tracking.task_type 是 VARCHAR(20)


@DBOS.step()
async def mark_cover_frames_processing_step(
    workflow_id: str, user_id: Optional[str] = None
) -> None:
    """queued → processing。与 session_login / publish_distribution 同款：
    trigger 只写 ``status``，``phase`` 得由 manager 推。``user_id`` 必须透传，
    否则 ``start()`` 的自愈建行会撞 ``task_tracking.user_id`` UUID NOT NULL。
    """
    from app.services.infra.unified_task_manager import get_task_manager

    try:
        await get_task_manager().start(
            workflow_id, user_id=user_id, task_type=TASK_TYPE
        )
    except Exception as e:  # noqa: BLE001 — best-effort，同 session_login 链
        logger.warning(f"[cover_frames.mark_processing] {workflow_id}: {e}")


@DBOS.step()
async def extract_cover_frames_step(
    workflow_id: str,
    source_resource_id: str,
    user_id: str,
    num_frames: int,
) -> dict[str, Any]:
    """下载 → 抽帧 → 返回可直接进 metadata 的 dict（预览图 + 时间点）。

    ⚠️ **这一步一行 resources 都不写。** 候选帧曾经每张落一行，继承源视频的
    folder / library / scope，于是混进用户放那个视频的文件夹里；现在它们是纯
    临时物，用户挑中之后由 ``/covers/select`` 按时间点重抽。落库只发生在那一
    步，且只有两张成品封面。

    全程 heartbeat：这一步里最长的是 materialize 的下载，期间没有任何进度
    信号，stall detector 会把安静的任务判成 lost。

    ⚠️ 仍然必须自建 ambient scope —— 这一步要**读** resources（源视频），而
    workflow 跑在 DBOS 的执行任务里，HTTP 请求的 scope 早就随响应结束被 reset
    了，contextvar 不会跨过来。没有它，选择点 fail-closed，抽帧在 workflow 侧
    同样失败。用 USER scope 而非 SYSTEM：只读发起人自己看得到的素材，比 SYSTEM
    更紧。同款写法见 upload_postprocess / caption_asset / soda_download。
    """
    from app.db.scope import Scope, request_scope
    from app.services.distribution.cover_frames import extract_cover_candidates
    from app.services.workflow_heartbeat import async_heartbeat_loop

    async with request_scope(Scope(user_id=user_id)):
        async with async_heartbeat_loop(workflow_id=workflow_id):
            result = await extract_cover_candidates(
                source_resource_id=source_resource_id,
                num_frames=num_frames,
            )
    logger.info(
        f"[cover_frames] resource={source_resource_id} "
        f"extracted={len(result.candidates)} duration={result.duration_seconds}"
    )
    return result.as_dict()


@DBOS.workflow()
async def cover_frames_workflow(
    source_resource_id: str,
    user_id: str,
    num_frames: int = DEFAULT_COVER_FRAMES,
) -> dict[str, Any]:
    """抽出一组候选封面帧。

    终态两条：

    - **成功** → ``manager.complete()``，候选清单在 ``metadata.cover_frames``。
    - **失败** → ``manager.fail()`` + **raise**（路线 C 纪律 4）。失败原因带
      ``status_code``，前端可以据此区分"这个视频读不了"（422/404）与"超时了，
      重试可能有用"（504）—— 触发路径必须有类型化回显，silent no-op 不可接受。
    """
    from app.services.infra.unified_task_manager import get_task_manager

    manager = get_task_manager()
    workflow_id = DBOS.workflow_id
    await mark_cover_frames_processing_step(workflow_id, user_id)

    num_frames = max(1, min(MAX_COVER_FRAMES, num_frames))

    try:
        payload = await extract_cover_frames_step(
            workflow_id, source_resource_id, user_id, num_frames
        )
    except CoverFrameError as e:
        await manager.fail(
            workflow_id,
            f"cover extraction failed: {e.detail}",
            metadata_patch={
                "cover_frames": {
                    "source_resource_id": str(source_resource_id),
                    "candidates": [],
                    "error": e.detail,
                    "error_status": e.status_code,
                }
            },
        )
        raise

    count = len(payload.get("candidates") or [])
    await manager.complete(
        workflow_id,
        subtitle=f"{count} cover frame{'s' if count != 1 else ''} ready",
        metadata_patch={"cover_frames": payload},
    )
    return {
        "status": "completed",
        "source_resource_id": str(source_resource_id),
        "frame_count": count,
    }


__all__ = [
    "TASK_TYPE",
    "cover_frames_workflow",
    "extract_cover_frames_step",
    "mark_cover_frames_processing_step",
]
