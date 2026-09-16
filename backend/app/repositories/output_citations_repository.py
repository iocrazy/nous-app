"""``output_citations`` 的读写口（3c §2.2）。

「这一版产出被谁引用过」在这张表出现之前答不出来：引用只活在
``messages.body`` 的 jsonb 里，那一列零索引，反查等于全表扫 jsonb。对照组是
画布侧的 ``canvas_asset_refs`` —— 同一个问题，同一个解法：物化一张镜像表。

**删消息不级联到「引用发生过」这个事实上。** 表上的外键是
``message_id → messages(id) ON DELETE CASCADE``：消息没了，指着它的那条反查
记录就不该还在结果里当幽灵（它的 ``message_id`` 会指向一条读不出来的消息）。
``issue_id`` 则是 ``ON DELETE SET NULL`` —— 议题删了只说明这次引用不再挂在任何
议题上，引用本身发生过，计数照算。两条不是同一条规则，别合并读。

**授权不在这里。** 两个读法都按 ``(kind, ref_id)`` 取，而调用方（血缘端点）
进门已经过了 ``assert_chain_visible``；引用行自己携带的议题可见性由调用方用
``visible_issue_ids`` 批量裁剪。在这里再写一份等于两套口径，迟早分叉
（同 ``search_docs_repository`` 里那段「两半各做一半」的注释）。
"""

from __future__ import annotations

from typing import Any, Dict, List

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.db.session import read_scope, write_scope
from app.models.search import OutputCitations


class OutputCitationsRepository:
    """镜像表的唯一访问口：写一批、按三坐标列一版、按两坐标数整条链。"""

    # -- writes -------------------------------------------------------

    def _insert_stmt(self, rows: List[Dict[str, Any]]):
        """``ON CONFLICT DO NOTHING``：UNIQUE (message_id, kind, ref_id, version)。

        一条消息重投（编辑、重发、重放）不该让计数翻倍，而重投**不是**错误，
        所以它不该抛。去重在 ``citations.py`` 里也做了一次——那一次挡的是同一
        批里的重复，这一次挡的是跨批的重投，两者封的不是同一件事。
        """
        return pg_insert(OutputCitations).values(rows).on_conflict_do_nothing()

    async def insert_many(
        self, rows: List[Dict[str, Any]], *, session: Any = None
    ) -> None:
        """一批引用行。``session`` 非空时借调用方的事务，否则自开一个。

        借得到就借：镜像与消息同生共死才不会留下指向不存在消息的行。借不到也
        照写 —— 唯一的写点 ``ConversationRepository.send_message`` 自开自提交，
        拿不到 handle（见 ``citations.py`` 的偏离说明）。
        """
        if not rows:
            return
        stmt = self._insert_stmt(rows)
        if session is not None:
            await session.execute(stmt)
            return
        async with write_scope() as own:
            await own.execute(stmt)

    # -- reads --------------------------------------------------------

    def _list_stmt(self, kind: str, ref_id: str, version: int):
        """一**版**的引用，新的在前。三个坐标都钉住——少 ``version`` 一个，
        血缘面板上每一版都会显示整条链的引用。

        ``id DESC`` 是**决胜键不是装饰**：``created_at`` 默认 ``now()``，也就是
        事务开始时刻，所以同一个事务里落的两条引用时间戳**完全相等**，只按时间
        排的话它们的先后由服务器随便定，两次请求可以给出两种顺序。
        """
        return (
            select(
                OutputCitations.issue_id,
                OutputCitations.message_id,
                OutputCitations.cited_by_user_id,
                OutputCitations.created_at,
            )
            .where(OutputCitations.kind == str(kind))
            .where(OutputCitations.ref_id == str(ref_id))
            .where(OutputCitations.version == int(version))
            .order_by(OutputCitations.created_at.desc(), OutputCitations.id.desc())
        )

    async def list_for_ref(
        self, kind: str, ref_id: str, version: int
    ) -> List[Dict[str, Any]]:
        """``[{issue_id, message_id, user_id, at}]``，id 一律字符串出口。

        ``issue_id`` 可以是 ``None``（议题已删，或这条引用压根不在议题上）——
        ``ON DELETE SET NULL`` 的那一半，不是缺陷。
        """
        async with read_scope() as session:
            rows = (await session.execute(self._list_stmt(kind, ref_id, version))).all()
        return [
            {
                "issue_id": str(issue_id) if issue_id is not None else None,
                "message_id": str(message_id),
                "user_id": str(user_id),
                "at": at,
            }
            for issue_id, message_id, user_id, at in rows
        ]

    def _counts_stmt(self, kind: str, ref_id: str):
        """整条链一次 GROUP BY。**刻意不筛版本**：每版一次查询会让二十版的链
        发二十次往返，而这一个问题一次就答得完。"""
        return (
            select(OutputCitations.version, func.count().label("n"))
            .where(OutputCitations.kind == str(kind))
            .where(OutputCitations.ref_id == str(ref_id))
            .group_by(OutputCitations.version)
        )

    async def counts_for_chain(self, kind: str, ref_id: str) -> Dict[int, int]:
        """``{version: 被引次数}``，**全量**（不按可见性裁）。没被引过的版本
        压根不在 dict 里 —— 调用方用 ``.get(v, 0)`` 读，缺席就是零。"""
        async with read_scope() as session:
            rows = (await session.execute(self._counts_stmt(kind, ref_id))).all()
        return {int(version): int(n) for version, n in rows}


_repo = OutputCitationsRepository()


def get_output_citations_repository() -> OutputCitationsRepository:
    return _repo


__all__ = ["OutputCitationsRepository", "get_output_citations_repository"]
