"""存量 ``search_docs`` 行的正文回填（3c 补充票）。

**这份代码在补的缺口。** mig 472 的两段回填段只投了坐标。run 那一段带上了
``r.output_summary``，产出那一段的列清单里**根本没有 ``body``** —— 于是 3c 上线
之前登记的每一件产出，在检索里只有标题搜得到，正文索引
``idx_search_docs_body_trgm``（partial，``WHERE body IS NOT NULL``）对它们完全是
空的。而缺席是静默的：搜一件产出搜不到，和那件产出不存在，在结果页上长得一模
一样。实时写方（``projection.py``）从上线那天起就是对的，所以这是一次性的存量
账，不是一条持续漏水的缝。

生产实测（2026-09-16，36 条产出行 / 280 条 run 行）：

=================  ====  =========
entity             空的  总数
=================  ====  =========
output script_shot   29         30
output script_scene   3          3
output generated_m…   3          3
run                  87        280
=================  ====  =========

**为什么这不是一份 SQL 迁移。** 32/35 条空正文的产出行是 ``script_shot`` 与
``script_scene``：前者要按 ``script_shot_ops`` 做三档字段解析（水位前的 after、
水位后第一条 op 的 before、最后才是当前行），后者要把 ``script_ops`` 的 JSON 逐
条重放。这两样在一句 UPDATE 里表达不出来。能表达的只有 3 条媒体 —— 为那 3 条写
一份与 Python 各算一遍的 SQL，买到的是一条**没有任何东西会说出来的**漂移缝：
两侧写进的是同一列，谁也不会发现它们哪天开始不一致了。

**正文从哪来，必须和实时写方走同一条路。** 每一类的取法都指着登记时传给
``register_deliverable(search_text=...)`` 的那个同一个表达式：

``script_shot`` / ``script_scene``
    ``diff.rebuild_content()`` 折叠账本，再过 ``render_shot`` / ``render_elements``
    —— 与 ``screenwriting_tools`` 和 ``revert`` 在登记口传的是**同一对函数**，不是
    长得像的另一对。
``generated_media``
    ``generated_media.prompt`` 这一列本身，也就是登记口
    （``generated_media_service.py`` 的 ``search_text=origin.prompt``）写进去的那个
    值。标题取首行、正文取全文，两者刻意不同 —— 这里只补正文。
``script_chapter``
    四类里唯一至今没有生产者、也没有账本的一类（``diff`` 模块 docstring）。重建
    不出来记进 ``unavailable``，**不是失败**。
run 行
    ``agent_runs.output_summary``，同 ``project_run_best_effort``。

**只补空的，永不覆盖。** 那条 ``body IS NULL OR body = ''`` 谓词在 **SQL 侧**
（``SearchDocsRepository.fill_empty_body``），不在这里的 Python 判断里：从读回一
批行到写回去之间，实时写方可能刚好投过同一行，而它写的是**现在**的内容，比回填
按账本重建出来的那一版更该留下。Python 侧再判一次只能缩小窗口，关不掉它。

**重建不出来不是错误，但必须数出来。** 一次回填写了 0 条，可能是「没有空行」也
可能是「每一行都重建失败」—— 那是两件完全不同的事。所以 ``BackfillStats`` 把
``filled`` / ``unavailable`` / ``failed`` 分开计，和
``backfill_canvas_asset_refs.py`` 分开报 malformed 计数是同一条纪律。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Optional

from loguru import logger
from sqlalchemy import and_, or_, select

from app.db.scope import system_request_scope
from app.db.session import read_scope
from app.models.agents import AgentRuns, RunDeliverables
from app.models.generated_media import GeneratedMedia
from app.models.search import SearchDocs
from app.repositories.search_docs_repository import get_search_docs_repository
from app.services.deliverables.diff import (
    NO_LEDGER,
    NO_SNAPSHOT,
    NOT_FOUND,
    rebuild_content,
    render_elements,
    render_shot,
)

#: 重建出来是一串空白时用的原因码。写一个空 body 与不写没有区别（``fill_empty_body``
#: 的谓词和 partial 索引都按「空」处理），但把它记成 ``filled`` 会让统计说谎。
EMPTY = NO_SNAPSHOT


@dataclass(frozen=True)
class BackfillStats:
    """一次回填的账。四个数各自独立 —— 不许把一个塞进另一个的分支里。"""

    #: 读回来的空正文行数（产出 + run）。
    scanned: int = 0
    #: 真的写进去了的行数（``fill_empty_body`` 回报 rowcount 为 1）。
    filled: int = 0
    #: 重建不出正文的行数。**不是失败**：章节没有账本、被删掉的分镜没有内容。
    unavailable: int = 0
    #: 重建出了正文、但写回去时那一行已经不空了（实时写方抢先）。
    raced: int = 0
    #: 这一行在处理过程中抛了异常。真缺陷，逐条记 ERROR。
    failed: int = 0

    def plus(self, **delta: int) -> "BackfillStats":
        """加法返回新对象（不可变纪律）。"""
        return BackfillStats(
            scanned=self.scanned + delta.get("scanned", 0),
            filled=self.filled + delta.get("filled", 0),
            unavailable=self.unavailable + delta.get("unavailable", 0),
            raced=self.raced + delta.get("raced", 0),
            failed=self.failed + delta.get("failed", 0),
        )


async def _media_prompt(ref_id: str) -> tuple[Optional[str], Optional[str]]:
    """``generated_media.prompt`` —— 登记口传给 ``search_text=`` 的那一列本身。

    读不回来（行删了 / ref_id 不是 bigint）返回 ``NOT_FOUND``，与
    ``rebuild_content`` 的失败口径一致：说不出来就说「说不出来」，不要退化成空串。
    """
    try:
        async with read_scope() as session:
            row = (
                await session.execute(
                    select(GeneratedMedia.prompt).where(
                        GeneratedMedia.id == int(ref_id)
                    )
                )
            ).first()
    except Exception as exc:  # noqa: BLE001 — 同 rebuild_content：说不出来也要说出来
        logger.opt(exception=True).error(
            f"[search] backfill could not read generated_media/{ref_id}: {exc!r}"
        )
        return None, NOT_FOUND
    if row is None:
        return None, NOT_FOUND
    return (row[0] or None), (None if row[0] else EMPTY)


async def output_body(row: Mapping[str, Any]) -> tuple[Optional[str], Optional[str]]:
    """一版产出的正文，**逐类对齐登记口传的 ``search_text``**（见模块 docstring）。

    ``row`` 要带 ``kind`` / ``ref_id`` / ``version`` / ``ledger_ref`` / ``created_at``
    —— 后两个是 ``rebuild_content`` 切账本水位用的（``ledger_ref`` 优先，存量行
    NULL 时退回时间戳）。返回 ``(正文, None)`` 或 ``(None, 原因码)``。
    """
    kind = str(row.get("kind") or "")
    ref_id = str(row.get("ref_id") or "")
    if kind == "generated_media":
        return await _media_prompt(ref_id)
    if kind in ("script_shot", "script_scene"):
        content, reason = await rebuild_content(kind, ref_id, dict(row))
        if content is None:
            return None, reason
        # ⚠️ 这两个函数就是 screenwriting_tools / revert 在登记时调的那两个。
        # 在这里重写一份「差不多的」渲染，等于让同一版产出的正文取决于它是被
        # 回填写进去的还是新写进去的。
        text = (
            render_shot(content) if kind == "script_shot" else render_elements(content)
        )
        return (text, None) if text.strip() else (None, EMPTY)
    # script_chapter 落在这里：没有账本，谁都重建不出来。
    return None, NO_LEDGER


async def _empty_output_rows() -> list[dict[str, Any]]:
    """正文为空的产出行 + 它们重建所需的账本坐标。

    按 ``kind`` / ``ref_id`` / ``version`` **三列**连回 ``run_deliverables``，不是
    切 ``entity_id`` 那个串 —— ``SearchDocsRepository._as_dict`` 的注释把这条写死了：
    拼法只有 ``projection.output_entity_id`` 一个地方说了算，读侧切它就是把拼法复制
    成第二份，而第二份不会跟着改。
    """
    async with read_scope() as session:
        rows = (
            (
                await session.execute(
                    select(
                        SearchDocs.entity_id,
                        RunDeliverables.kind,
                        RunDeliverables.ref_id,
                        RunDeliverables.version,
                        RunDeliverables.ledger_ref,
                        RunDeliverables.created_at,
                    )
                    .join(
                        RunDeliverables,
                        and_(
                            SearchDocs.kind == RunDeliverables.kind,
                            SearchDocs.ref_id == RunDeliverables.ref_id,
                            SearchDocs.version == RunDeliverables.version,
                        ),
                    )
                    .where(SearchDocs.entity_kind == "output")
                    .where(or_(SearchDocs.body.is_(None), SearchDocs.body == ""))
                    .order_by(SearchDocs.id)
                )
            )
            .mappings()
            .all()
        )
    return [dict(row) for row in rows]


async def _empty_run_rows() -> list[dict[str, Any]]:
    """正文为空、而 ``output_summary`` 有值的 run 行。

    mig 472 的 run 段**已经**写过 ``r.output_summary``，所以今天这个查询在生产上
    命中 0 行（87 条空正文的 run 行，它们的 ``output_summary`` 全是 NULL —— 那是
    「这次运行没有摘要」，不是「回填漏了」）。这一段留着不是补历史，是补**顺序**：
    一条 run 的摘要如果在 472 跑完之后、投影写方之外被补上（回溯修数据、以后新加
    的摘要生成），这一条就是它进检索的路。
    """
    async with read_scope() as session:
        rows = (
            (
                await session.execute(
                    select(SearchDocs.entity_id, AgentRuns.output_summary)
                    .join(AgentRuns, AgentRuns.id == SearchDocs.run_id)
                    .where(SearchDocs.entity_kind == "run")
                    .where(or_(SearchDocs.body.is_(None), SearchDocs.body == ""))
                    .where(AgentRuns.output_summary.isnot(None))
                    .where(AgentRuns.output_summary != "")
                    .order_by(SearchDocs.id)
                )
            )
            .mappings()
            .all()
        )
    return [dict(row) for row in rows]


async def _write(
    stats: BackfillStats, *, entity_kind: str, entity_id: str, body: str, dry_run: bool
) -> BackfillStats:
    if dry_run:
        return stats.plus(filled=1)
    wrote = await get_search_docs_repository().fill_empty_body(
        entity_kind=entity_kind, entity_id=entity_id, body=body
    )
    if wrote:
        return stats.plus(filled=1)
    # 谓词拦下来了：读回来到写回去之间实时写方投过这一行。它写的是现在的内容，
    # 该留下的就是它 —— 这是正常结局，不是失败。
    logger.info(
        f"[search] backfill skipped {entity_kind}/{entity_id}: "
        "the live writer filled it first"
    )
    return stats.plus(raced=1)


async def backfill_search_docs_bodies(*, dry_run: bool = False) -> BackfillStats:
    """把每一条正文为空的投影行补上正文。幂等：补过的行第二次不会再进候选集。

    ``dry_run`` 只跳过写，读与重建照跑 —— 预演要跑的是真路径，否则它预演的是
    另一个程序。
    """
    async with system_request_scope("backfill search_docs bodies (3c)"):
        stats = BackfillStats()
        for row in await _empty_output_rows():
            stats = stats.plus(scanned=1)
            entity_id = str(row["entity_id"])
            try:
                body, reason = await output_body(row)
            except Exception as exc:  # noqa: BLE001 — 一行炸不该带走整批
                logger.opt(exception=True).error(
                    f"[search] backfill failed on output {entity_id}: {exc!r}"
                )
                stats = stats.plus(failed=1)
                continue
            if body is None:
                logger.info(
                    f"[search] backfill has no body for output {entity_id}: {reason}"
                )
                stats = stats.plus(unavailable=1)
                continue
            stats = await _write(
                stats,
                entity_kind="output",
                entity_id=entity_id,
                body=body,
                dry_run=dry_run,
            )

        for row in await _empty_run_rows():
            stats = stats.plus(scanned=1)
            stats = await _write(
                stats,
                entity_kind="run",
                entity_id=str(row["entity_id"]),
                body=str(row["output_summary"]),
                dry_run=dry_run,
            )

    logger.info(
        "[search] backfill {}: scanned={} filled={} unavailable={} raced={} failed={}",
        "dry run" if dry_run else "done",
        stats.scanned,
        stats.filled,
        stats.unavailable,
        stats.raced,
        stats.failed,
    )
    return stats


__all__ = [
    "EMPTY",
    "NOT_FOUND",
    "NO_LEDGER",
    "BackfillStats",
    "backfill_search_docs_bodies",
    "output_body",
]
