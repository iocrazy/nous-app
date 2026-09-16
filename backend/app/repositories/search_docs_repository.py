"""``search_docs`` 的读写口（3c §2.1）。

**授权在两个地方各做一半，两半都不能省。**

- SQL 侧（这里）：``team_id ∈ 我的 team`` OR ``owner_user_id = 我``。这条谓词
  恒在，没有它这张表就是一张对所有人开放的全量索引——``search_docs`` 把别人的
  run 标题、别人的剧本行都摊平在一列 text 里，一次 ILIKE 就全读走了。
- Python 侧（``search/service.py``）：议题维度的可见性（``visible_issue_ids``）。
  那一半不在这里做，因为议题可见性有它自己的口径（创建人 / 指派人 / team），
  在 SQL 里复制一份等于两套口径，迟早分叉。

``team_ids`` 是**过滤不是授权**。同 ``IssueRepository.list_for_user`` 的
``team_id``（``issue_repository.py:316-323`` 的注释）：客户端传别人的 team id
只会让结果变少，永远不会变多——因为授权谓词是**另一个** ``.where()``，不是
被它替换掉的那个。合成一个谓词正是 ``rpc_user_media_text_search`` 那个洞的形状。
"""

from __future__ import annotations

import uuid as _uuid
from typing import Any, Iterable, Optional

from sqlalchemy import func, literal, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.db.session import get_sessionmaker, in_unit_of_work, read_scope, write_scope
from app.models.search import SearchDocs
from app.models.teams import TeamMembers
from app.services.library.like_escape import LIKE_ESCAPE_CHAR, escape_like
from app.services.search.types import BODY_MAX_BYTES, SearchDoc

#: Snowflake / uuid 列一律以字符串出口（``bigIntSafeFetch`` 纪律：BIGINT 过
#: 2^53 在 JS 里会掉精度）。``id`` 也在内，它同样是 BIGINT。
_TEXT_OUT = (
    "id",
    "team_id",
    "project_id",
    "issue_id",
    "run_id",
    "owner_user_id",
    "agent_id",
)


def _clip(body: Optional[str]) -> Optional[str]:
    """按**字节**截，切在码点边界上。

    ``encode()[:n].decode()`` 会在多字节字符中间切断并抛 UnicodeDecodeError；
    ``errors="ignore"`` 丢的是尾巴上的一个字，不是这条投影。
    """
    if body is None:
        return None
    raw = body.encode("utf-8")
    if len(raw) <= BODY_MAX_BYTES:
        return body
    return raw[:BODY_MAX_BYTES].decode("utf-8", errors="ignore")


class SearchDocsRepository:
    """投影表的唯一访问口。``upsert`` 写、``search`` 读，别的都不该有。"""

    def _values(self, doc: SearchDoc) -> dict[str, Any]:
        out = {f: getattr(doc, f) for f in SearchDoc.__dataclass_fields__}
        out["entity_id"], out["body"] = str(doc.entity_id), _clip(doc.body)
        return out

    def _upsert_stmt(self, doc: SearchDoc):
        values = self._values(doc)
        updatable = {
            k: v for k, v in values.items() if k not in ("entity_kind", "entity_id")
        }
        return (
            pg_insert(SearchDocs)
            .values(**values)
            .on_conflict_do_update(
                index_elements=[SearchDocs.entity_kind, SearchDocs.entity_id],
                # 投影可重建：同一个对象再投一次就是覆盖，绝不是第二行。
                set_={**updatable, "updated_at": func.now()},
            )
        )

    def _search_stmt(
        self,
        *,
        q: str,
        kinds: Iterable[str],
        team_ids: list[int],
        user_id: str,
        project_id: Optional[int],
        issue_id: Optional[int],
        limit: int,
    ):
        # 拼模式者负责转义（CLAUDE.md）。SQL 侧再声明 ESCAPE——只做一侧等于没做。
        pattern = f"%{escape_like(q)}%"
        body = func.coalesce(SearchDocs.body, literal(""))
        score = func.greatest(
            func.similarity(SearchDocs.title, q), func.similarity(body, q)
        ).label("score")
        # 边界校验：这个值进 WHERE 之前必须是一个真 uuid（同
        # ``IssueRepository.list_for_user`` 开头那次 ``_uuid.UUID(user_id)``）。
        # 原生 UUID 进 bind 也让 asyncpg 不必猜类型。
        me = _uuid.UUID(str(user_id))
        mine = select(TeamMembers.team_id).where(TeamMembers.user_id == me)
        stmt = (
            select(SearchDocs, score)
            .where(SearchDocs.entity_kind.in_(sorted(kinds)))
            .where(
                or_(
                    SearchDocs.title.ilike(pattern, escape=LIKE_ESCAPE_CHAR),
                    body.ilike(pattern, escape=LIKE_ESCAPE_CHAR),
                )
            )
            # 授权谓词：恒在。没有它这张表就是一张对所有人开放的全量索引。
            .where(or_(SearchDocs.team_id.in_(mine), SearchDocs.owner_user_id == me))
        )
        if team_ids:
            stmt = stmt.where(SearchDocs.team_id.in_([int(t) for t in team_ids]))
        if project_id is not None:
            stmt = stmt.where(SearchDocs.project_id == int(project_id))
        if issue_id is not None:
            stmt = stmt.where(SearchDocs.issue_id == int(issue_id))
        return stmt.order_by(score.desc(), SearchDocs.updated_at.desc()).limit(
            int(limit)
        )

    async def upsert(self, doc: SearchDoc) -> None:
        """写一条投影行（按 (entity_kind, entity_id) 覆盖）。

        ⚠️ **在别人的事务里时走独立 session。** 回退（``deliverables/revert.py``）
        在 ``unit_of_work()`` 里登记产出，而一次失败的 INSERT 会把那个 session
        当场弄废——调用方的 best-effort 吞得掉异常，吞不掉一个已经 abort 的事务，
        随后每条语句都变成 ``PendingRollbackError``（同
        ``registry._stamp_seq`` 的那段注释）。于是「投影写失败」会把一次内容已经
        改好的回退判成失败并回滚它，正好是投影的写方不该做的事。

        代价是：调用方的事务若在此之后回滚，这条投影会变成一个指向不存在版本的
        幽灵行。可接受——它是投影，同一坐标下一次真写入就覆盖掉了；而反过来
        （让投影去否决一次成功的内容改动）不可接受。
        """
        stmt = self._upsert_stmt(doc)
        if in_unit_of_work():
            async with get_sessionmaker()() as session:
                async with session.begin():
                    await session.execute(stmt)
            return
        async with write_scope() as session:
            await session.execute(stmt)

    async def search(
        self,
        *,
        q: str,
        kinds: Iterable[str],
        team_ids: list[int],
        user_id: str,
        project_id: Optional[int],
        issue_id: Optional[int],
        limit: int,
    ) -> list[dict[str, Any]]:
        """命中行 + ``score``。行原样出口——读侧不许切 ``entity_id``（见下）。"""
        stmt = self._search_stmt(
            q=q,
            kinds=kinds,
            team_ids=team_ids,
            user_id=user_id,
            project_id=project_id,
            issue_id=issue_id,
            limit=limit,
        )
        async with read_scope() as session:
            rows = (await session.execute(stmt)).all()
        return [self._as_dict(row[0], row[1]) for row in rows]

    @staticmethod
    def _as_dict(row: SearchDocs, score: Any) -> dict[str, Any]:
        """一行投影摊成 dict。

        ⚠️ **``entity_id`` 原样出口，谁都不许在读侧切它。** 产出行的拼法是
        ``kind:ref_id:version``（``projection.output_entity_id`` 是唯一拼它的
        地方），但三段坐标在表上**各有一列**——读者要 kind / ref_id / version
        就读列，切字符串等于把拼法复制成第二份，而第二份不会跟着
        ``output_entity_id`` 一起改。``entity_id`` 在读侧只有一个用途：
        ``SearchHit.id``，也就是「这条命中是哪一版」的不透明身份键。
        """
        out: dict[str, Any] = {
            attr.key: getattr(row, attr.key)
            for attr in SearchDocs.__mapper__.column_attrs
        }
        for column in _TEXT_OUT:
            if out.get(column) is not None:
                out[column] = str(out[column])
        out["score"] = float(score or 0.0)
        return out


_repo = SearchDocsRepository()


def get_search_docs_repository() -> SearchDocsRepository:
    return _repo


__all__ = ["SearchDocsRepository", "get_search_docs_repository"]
