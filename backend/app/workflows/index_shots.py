"""index_shots DBOS workflow — cut one video into shots and embed one frame
per shot (the Visual retrieval layer, spec §5).

One run = one video. Two triggers share it: the Shots tab's Index This
Video (``POST /ai/analyze/index-shots/{resource_id}``) and the Settings
backfill (``POST /ai/analyze/backfill-shots``), which dispatches one run per
video under a parent task.

路线 C（CLAUDE.md「任务系统架构纪律」）
=====================================
1. ``task_tracking`` 行由端点建（``manager.create(dbos_workflow_id=wf_id)``
   与 ``start_workflow_routed(workflow_id=wf_id)`` 同一个 id）；这里只调
   ``start`` / ``update_progress`` / ``complete`` / ``fail``，绝不 PATCH
   ``phase`` / ``status`` / ``progress``。
2. 失败一律 raise（``manager.fail`` 之后），subtitle 是类型化原因
   （``shot indexing failed: provider_error``），UI 与回填父任务据此分流。
3. 结果进 ``metadata.shots``（``ShotIndexResult.as_metadata()``），DBOS 的
   input/output 不放业务字段。

Scope：这一步要读 ``resources``（源视频），而 workflow 跑在 DBOS 执行任务里，
HTTP 的 scope 不会跨过来 —— 与 ``cover_frames`` 同款，用 USER scope 自建
（只读发起人自己看得到的素材）。
"""

from __future__ import annotations

from typing import Any, Optional

from dbos import DBOS
from loguru import logger

from app.services.library.shot_index import ShotIndexError

TASK_TYPE = "index_shots"  # ≤20 chars — task_tracking.task_type 是 VARCHAR(20)


@DBOS.step()
async def mark_index_shots_processing_step(
    workflow_id: str, user_id: Optional[str] = None
) -> None:
    """queued → processing（``user_id`` 必须透传，同 cover_frames）。"""
    from app.services.infra.unified_task_manager import get_task_manager

    try:
        await get_task_manager().start(
            workflow_id, user_id=user_id, task_type=TASK_TYPE
        )
    except Exception as e:  # noqa: BLE001 — best-effort，同 cover_frames 链
        logger.warning(f"[index_shots.mark_processing] {workflow_id}: {e}")


@DBOS.step(retries_allowed=False)
async def index_shots_step(
    workflow_id: str, resource_id: str, user_id: str
) -> dict[str, Any]:
    """Load the source video (USER scope), resolve the active space + its
    embedder, then cut / embed / write. Raises :class:`ShotIndexError`.

    Not retried by DBOS: a second attempt would pay the provider again for
    the same frames; the user re-runs from the Shots tab if they want to."""
    from app.db.scope import Scope, request_scope
    from app.repositories.resources_repository import ResourcesRepository
    from app.services.distribution.cover_frames import (
        CoverFrameError,
        load_source_video,
    )
    from app.services.infra.unified_task_manager import get_task_manager
    from app.services.library.shot_index import (
        index_resource_shots,
        resolve_space_and_embedder,
    )
    from app.services.workflow_heartbeat import async_heartbeat_loop

    manager = get_task_manager()

    async def progress(pct: int, subtitle: str) -> None:
        await manager.update_progress(workflow_id, pct, subtitle=subtitle)

    async with request_scope(Scope(user_id=user_id)):
        try:
            source = await load_source_video(ResourcesRepository(), resource_id)
        except CoverFrameError as e:
            reason = "resource_not_found" if e.status_code == 404 else "not_a_video"
            if "no file" in (e.detail or ""):
                reason = "no_video_file"
            raise ShotIndexError(reason, e.detail) from e
        space, embedder = await resolve_space_and_embedder()
        async with async_heartbeat_loop(workflow_id=workflow_id):
            result = await index_resource_shots(
                resource_id=int(resource_id),
                file_path=source.file_path,
                space=space,
                embedder=embedder,
                progress=progress,
            )
    return result.as_metadata()


@DBOS.workflow()
async def index_shots_workflow(resource_id: str, user_id: str) -> dict[str, Any]:
    """Index the shots of ``resource_id`` for ``user_id``.

    - **成功** → ``manager.complete()``，``metadata.shots`` 是
      :class:`ShotIndexResult`；subtitle ``Indexed · N shots``。
    - **失败** → ``manager.fail()`` + raise；subtitle 与 ``error_code`` 都是
      :class:`ShotIndexError.reason`。
    """
    from app.services.infra.unified_task_manager import get_task_manager

    manager = get_task_manager()
    workflow_id = DBOS.workflow_id
    await mark_index_shots_processing_step(workflow_id, user_id)
    try:
        meta = await index_shots_step(workflow_id, str(resource_id), user_id)
    except ShotIndexError as e:
        await manager.fail(
            workflow_id,
            f"shot indexing failed: {e.reason}",
            error_code=e.reason,
            metadata_patch={
                "shots": {
                    "resource_id": str(resource_id),
                    "error": e.reason,
                    "detail": e.detail[:200],
                }
            },
        )
        raise
    n = int(meta.get("shots") or 0)
    skipped = int(meta.get("skipped") or 0)
    subtitle = f"Indexed · {n} shot{'s' if n != 1 else ''}"
    if skipped:
        subtitle += f" · {skipped} not embedded"
    await manager.complete(
        workflow_id, subtitle=subtitle, metadata_patch={"shots": meta}
    )
    return {"status": "completed", "resource_id": str(resource_id), "shots": n}


__all__ = [
    "TASK_TYPE",
    "index_shots_step",
    "index_shots_workflow",
    "mark_index_shots_processing_step",
]
