"""把一次运行 / 一版产出投影进 ``search_docs``（3c §2.1）。

**失败永不连坐。** 两个写方都跑在「东西已经存在」之后：run 已经结束并写完了
自己那一行，产出已经生成、已经付过钱、已经登记。在那之后把整次调用判成失败，
调用方会重试，于是同一件东西产两遍——比检索里少一条记录糟得多。所以两个函数
都只 ``logger.error`` 不 raise。⚠️ 不是静默吞错：失败记 ERROR，投影的缺席本身
也是可查的信号（同 ``register_deliverable_best_effort``）。

**这里不做可见性判断。** 谁看得见由读的那一侧决定
（``search_docs_repository`` 的授权谓词 + ``search/service.py`` 的议题裁剪）。
两处都判会出现两套口径，而分叉的那一天没有任何测试会说出来。写侧只负责把
坐标写全——写不出某个坐标就留 NULL，不是整条不写。
"""

from __future__ import annotations

from typing import Any, Optional

from loguru import logger

from app.repositories.search_docs_repository import get_search_docs_repository
from app.services.search.types import SearchDoc

#: 无议题的 run 用输入摘要当标题时取的长度。整段 500 字的 input_summary 会把
#: 命中列表撑成一堵墙。
_TITLE_FALLBACK_CHARS = 80


def _int(value: Any) -> Optional[int]:
    return None if value is None else int(value)


def _str(value: Any) -> Optional[str]:
    return None if value is None else str(value)


async def _issue_identity(issue_id: Any) -> tuple[Optional[str], Optional[str]]:
    from app.repositories.issue_repository import issue_repository

    return await issue_repository.identifier_and_title(int(issue_id))


async def _run_coords(run_id: Any) -> Optional[dict[str, Any]]:
    """一条产出的坐标来自它的 run —— ``run_deliverables`` 没有 issue_id 列，
    ``agent_runs.issue_id`` 是唯一真相（``output_ref_resolver`` docstring 第二条）。
    人手版没有 run，返回 ``None``。一条 SELECT 取 team/project/issue/agent/user/model。
    """
    if run_id is None:
        return None
    from sqlalchemy import select

    from app.db.session import read_scope
    from app.models import AgentRuns

    async with read_scope() as session:
        row = (
            await session.execute(
                select(
                    AgentRuns.team_id,
                    AgentRuns.project_id,
                    AgentRuns.issue_id,
                    AgentRuns.agent_id,
                    AgentRuns.user_id,
                    AgentRuns.model,
                ).where(AgentRuns.id == int(run_id))
            )
        ).first()
    if row is None:
        return None
    return {
        "team_id": row[0],
        "project_id": row[1],
        "issue_id": row[2],
        "agent_id": row[3],
        "user_id": row[4],
        "model": row[5],
    }


def _run_title(run_row: dict, key: Optional[str], title: Optional[str]) -> str:
    if key and title:
        return f"{key} · {title}"
    if key:
        return key
    # title 是 NOT NULL：编不出名字也要写一条，否则这次运行在检索里不存在。
    return (run_row.get("input_summary") or "").strip()[:_TITLE_FALLBACK_CHARS] or "run"


async def project_run_best_effort(run_row: dict) -> None:
    """一次运行的终态投影。只在 run 结束时调用一次。"""
    try:
        issue_id = run_row.get("issue_id")
        key, title = (
            await _issue_identity(issue_id) if issue_id is not None else (None, None)
        )
        await get_search_docs_repository().upsert(
            SearchDoc(
                # run 行的身份键就是 ``str(agent_runs.id)``——**和产出行的拼法
                # 不同**，两者刻意不统一（契约补充）：run 本来就有一个全局唯一
                # id，产出的「一版」没有可用的单列 id，只能由坐标拼。
                entity_kind="run",
                entity_id=str(run_row.get("id")),
                title=_run_title(run_row, key, title),
                body=run_row.get("output_summary"),
                team_id=_int(run_row.get("team_id")),
                project_id=_int(run_row.get("project_id")),
                issue_id=_int(issue_id),
                run_id=_int(run_row.get("id")),
                owner_user_id=_str(run_row.get("user_id")),
                agent_id=_str(run_row.get("agent_id")),
                model=run_row.get("model"),
                status=run_row.get("status"),
                error_code=run_row.get("error_code"),
            )
        )
    except Exception as exc:  # noqa: BLE001 — 见模块 docstring
        logger.opt(exception=True).error(
            f"[search] run {run_row.get('id')} did not reach search_docs: {exc!r}"
        )


def output_entity_id(kind: Any, ref_id: Any, version: Any) -> str:
    """一件产出**某一版**的身份键（契约补充）：``kind:ref_id:version``。

    不是 ``run_deliverables.id``。mig 472 的回填段按同一个拼法写存量行，所以拼法
    必须只有这**一个**函数说了算——两处各拼各的，回填行与新写行就互不覆盖，
    UNIQUE (entity_kind, entity_id) 比的正是这个字符串，拦不住的那一版会在表里
    留两行、在检索里出现两次。

    三段都不含 ``:``：kind 是四值枚举，ref_id 是 Snowflake 十进制，version 是小
    整数——所以这个串可以反向切开，虽然读者不该切（坐标另有三列，见
    ``SearchDocsRepository._as_dict``）。
    """
    return f"{kind}:{ref_id}:{version}"


async def project_output_best_effort(row: Any, *, search_text: Optional[str]) -> None:
    """一版产出的投影。``search_text`` 为 ``None`` 只是搜不到正文，不是错误。"""
    try:
        coords = await _run_coords(row.run_id) or {}
        await get_search_docs_repository().upsert(
            SearchDoc(
                entity_kind="output",
                entity_id=output_entity_id(row.kind, row.ref_id, row.version),
                title=row.title or f"{row.kind} #{row.ref_id}",
                body=search_text,
                kind=row.kind,
                ref_id=str(row.ref_id),
                version=row.version,
                team_id=_int(coords.get("team_id")),
                project_id=_int(coords.get("project_id")),
                issue_id=_int(coords.get("issue_id")),
                run_id=_int(row.run_id),
                # 人手版（回退）没有 run，署名人就是它的 owner。
                owner_user_id=_str(coords.get("user_id") or row.actor_user_id),
                agent_id=_str(coords.get("agent_id")),
                model=coords.get("model"),
            )
        )
    except Exception as exc:  # noqa: BLE001 — 见模块 docstring
        logger.opt(exception=True).error(
            f"[search] {row.kind}/{row.ref_id} v{row.version} did not reach "
            f"search_docs: {exc!r}"
        )


__all__ = [
    "output_entity_id",
    "project_output_best_effort",
    "project_run_best_effort",
]
