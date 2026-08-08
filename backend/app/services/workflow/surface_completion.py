"""B4 (spec §5): surface 节点的自动完成 —— 判据满足时在产物写入回流点把
节点推到 done。

完成动作的优先路径是「关 mirror issue」:transition_status(issue, 'done')
会经 issue_repository._fire_stage_node_sync 把节点投影为 done 并入队
autopilot tick(级联推进由既有机器完成,本模块不直接动游标)。只有镜像
尚未创建(节点在未到达的组)时才走 repo 唯一写入口 set_node_status。

产物删除:本模块【刻意】只做正向(未完成→完成)。派生值回退是事实层面的
(progress 端点重算自然为假,spec §5 ①),存储的节点状态/游标/issue 一律
不回退 —— 想真退,走显式 retreat。

所有公开函数永不 raise(范式 A:函数内 import + try/except + warning),
caller 直接调,不需要再包。
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, Optional, Tuple

from loguru import logger
from sqlalchemy import select

_AUTO_SURFACES: Tuple[str, ...] = ("script", "storyboard")  # renders 本期不映射(2026-08-07 拍板)
_AUTO_COMPLETABLE = frozenset({"pending", "in_progress"})
_TERMINAL_ISSUE = frozenset({"done", "cancelled"})


def should_auto_complete(node: Dict[str, Any], criterion_met: bool) -> bool:
    """纯决策:这个节点现在该不该被自动推到 done。

    in_review 不碰(评审车道属于人,autopilot 硬线同族);done/skipped 幂等
    跳过;review_required 的 surface 节点保留人工完成路径。
    """
    return bool(
        criterion_met
        and node.get("surface") in _AUTO_SURFACES
        and not node.get("skipped")
        and not node.get("review_required")
        and node.get("status") in _AUTO_COMPLETABLE
    )


async def sync_surface_completion(
    project_id: str, episode_id: str, surfaces: Iterable[str] = _AUTO_SURFACES
) -> None:
    try:
        from app.repositories.episode_repository import get_episode_repository
        from app.repositories.project_stage_nodes_repository import (
            get_project_stage_nodes_repository,
        )

        wanted = set(surfaces) & set(_AUTO_SURFACES)
        if not wanted:
            return
        criteria = await get_episode_repository().surface_criteria_for_episode(
            episode_id
        )
        nodes = await get_project_stage_nodes_repository().list_nodes_by_episode(
            project_id, episode_id
        )
        for node in nodes:
            surf = node.get("surface")
            if surf not in wanted:
                continue
            if not should_auto_complete(node, bool(criteria.get(surf))):
                continue
            await _complete_node(project_id, node)
    except Exception as exc:  # noqa: BLE001 — 回流 hook 绝不影响主写
        logger.warning(
            f"[surface-completion] sync failed for project {project_id} "
            f"episode {episode_id}: {exc!r}"
        )


async def _complete_node(project_id: str, node: Dict[str, Any]) -> None:
    from app.repositories.issue_repository import get_issue_repository
    from app.services.library.project_stage_issues import (
        ORIGIN_KIND,
        build_stage_origin_id,
    )

    node_id = str(node["id"])
    issues = await get_issue_repository().list_by_origin(
        ORIGIN_KIND, build_stage_origin_id(project_id, node_id)
    )
    open_issues = [i for i in issues if i.get("status") not in _TERMINAL_ISSUE]
    if open_issues:
        for issue in open_issues:
            # 投影钩子会刷节点 status=done 并自行入队 autopilot tick
            await get_issue_repository().transition_status(int(issue["id"]), "done")
        logger.info(
            f"[surface-completion] node {node_id} auto-completed via "
            f"{len(open_issues)} mirror issue(s) (project {project_id})"
        )
        return
    # 镜像未创建(节点在未到达的组):走唯一写入口,tick 自己入队
    from app.repositories.project_stage_nodes_repository import (
        get_project_stage_nodes_repository,
    )

    await get_project_stage_nodes_repository().set_node_status(node_id, "done")
    logger.info(
        f"[surface-completion] node {node_id} auto-completed directly "
        f"(no mirror issue yet, project {project_id})"
    )
    await _enqueue_tick(project_id)


async def _enqueue_tick(project_id: str) -> None:
    try:
        from app.workflows.autopilot import enqueue_autopilot_tick

        await enqueue_autopilot_tick(project_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"[surface-completion] tick enqueue failed: {exc!r}")


async def sync_project_surface_completion(project_id: str) -> Dict[str, int]:
    """全项目一次性重算(部署点火/修复用,Task 7 的端点调它)。"""
    from app.repositories.episode_repository import get_episode_repository

    episodes = await get_episode_repository().list_by_project(project_id)
    for ep in episodes:
        await sync_surface_completion(project_id, str(ep["id"]))
    return {"episodes": len(episodes)}


# ---- 写路径 seams:各回流点只知道自己手里的 id,这里解析归属 ----

async def _scope_for_script(script_id: str) -> Optional[Tuple[str, str]]:
    from app.db.session import read_scope
    from app.models.scripts import Episodes, ScriptProjects

    async with read_scope() as s:
        row = (
            await s.execute(
                select(Episodes.project_id, ScriptProjects.episode_id)
                .join(Episodes, Episodes.id == ScriptProjects.episode_id)
                .where(ScriptProjects.id == int(script_id))
            )
        ).first()
    if not row or row.episode_id is None:
        return None
    return (str(row.project_id), str(row.episode_id))


async def _scope_for_scene(scene_id: str) -> Optional[Tuple[str, str]]:
    from app.db.session import read_scope
    from app.models.scripts import Episodes, ScriptProjects, ScriptScenes

    async with read_scope() as s:
        row = (
            await s.execute(
                select(Episodes.project_id, ScriptProjects.episode_id)
                .select_from(ScriptScenes)
                .join(ScriptProjects, ScriptProjects.id == ScriptScenes.script_id)
                .join(Episodes, Episodes.id == ScriptProjects.episode_id)
                .where(ScriptScenes.id == int(scene_id))
            )
        ).first()
    if not row or row.episode_id is None:
        return None
    return (str(row.project_id), str(row.episode_id))


async def _scope_for_shot(shot_id: str) -> Optional[Tuple[str, str]]:
    from app.db.session import read_scope
    from app.models.scripts import Episodes, ScriptProjects, ScriptScenes, ScriptShots

    async with read_scope() as s:
        row = (
            await s.execute(
                select(Episodes.project_id, ScriptProjects.episode_id)
                .select_from(ScriptShots)
                .join(ScriptScenes, ScriptScenes.id == ScriptShots.scene_id)
                .join(ScriptProjects, ScriptProjects.id == ScriptScenes.script_id)
                .join(Episodes, Episodes.id == ScriptProjects.episode_id)
                .where(ScriptShots.id == int(shot_id))
            )
        ).first()
    if not row or row.episode_id is None:
        return None
    return (str(row.project_id), str(row.episode_id))


async def fire_surface_sync_for_script(
    script_id: str, surfaces: Iterable[str] = _AUTO_SURFACES
) -> None:
    try:
        scope = await _scope_for_script(script_id)
        if scope:
            await sync_surface_completion(scope[0], scope[1], surfaces)
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"[surface-completion] fire(script={script_id}) failed: {exc!r}")


async def fire_surface_sync_for_scene(
    scene_id: str, surfaces: Iterable[str] = _AUTO_SURFACES
) -> None:
    try:
        scope = await _scope_for_scene(scene_id)
        if scope:
            await sync_surface_completion(scope[0], scope[1], surfaces)
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"[surface-completion] fire(scene={scene_id}) failed: {exc!r}")


async def fire_surface_sync_for_shot(
    shot_id: str, surfaces: Iterable[str] = ("storyboard",)
) -> None:
    try:
        scope = await _scope_for_shot(shot_id)
        if scope:
            await sync_surface_completion(scope[0], scope[1], surfaces)
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"[surface-completion] fire(shot={shot_id}) failed: {exc!r}")
