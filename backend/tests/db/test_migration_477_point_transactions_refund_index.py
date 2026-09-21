"""477 的形状由这里钉住：consume 腿的姊妹索引，给 refund 腿用（终审 I-2）。

与 ``test_migration_474_point_transactions_index.py`` 同一个范式，同一个理由 ——
``charged_points_for_references`` 报的是**净扣**（扣 − 退），所以它按 ``type`` 拆成
两条查询各发一次；474 只覆盖了第一条，第二条此前没有任何索引可用（mig 123 那个
refund 唯一索引首列是 ``team_id``，而这条查询不带 ``team_id``），于是在同一个**被
前端轮询**的端点上又开了一条全扫。

照 474 的读文件断言风格——不连库，纯读 SQL 文本，CI 的 schema-drift 才是真执行方。
断言只读**可执行正文**（剥掉整行注释）：本迁移的注释里原样复述了它所钉住的语句，
对原始文本做子串匹配会在语句被删、解释还留着时照样转绿。
"""

from __future__ import annotations

import contextlib
import pathlib
import re
from unittest.mock import patch

import pytest
from sqlalchemy import Index
from sqlalchemy.dialects import postgresql

from app.models import PointTransactions
from app.repositories.points_repository import PointsRepository

pytestmark = pytest.mark.unit

MIG = pathlib.Path(__file__).resolve().parents[3] / "supabase/migrations"
_RAW = (MIG / "477_point_transactions_agent_run_refund_index.sql").read_text(
    encoding="utf-8"
)
BODY = "\n".join(
    line for line in _RAW.splitlines() if not line.strip().startswith("--")
)
_SQUEEZED = re.sub(r"\s+", " ", BODY)

INDEX = "idx_point_transactions_agent_run_refund"


def test_creates_the_index_idempotently():
    """整份迁移可重跑——run-migration 把一批文件喂进同一个 session，一次失败后的
    重跑不该在这一句上炸。"""
    assert f"CREATE INDEX IF NOT EXISTS {INDEX}" in _SQUEEZED


class _Session:
    def __init__(self) -> None:
        self.stmts: list = []

    async def execute(self, stmt):
        self.stmts.append(stmt)

        class _R:
            def all(self):
                return []

        return _R()


def _compiled_leg_wheres() -> list[str]:
    """``charged_points_for_references`` **真发出去**的两条语句的 WHERE 子句。

    重新拼一条一样的 select 是没有意义的——那只会证明这个测试自己会写 SQL。
    走真实方法 + 桩 session，改了实现这里才会跟着动。"""
    sess = _Session()

    @contextlib.asynccontextmanager
    async def _scope():
        yield sess

    async def _run():
        with patch("app.repositories.points_repository.read_scope", _scope):
            await PointsRepository().charged_points_for_references(
                reference_type="agent_run", reference_ids=["1"]
            )

    import asyncio

    asyncio.run(_run())
    out = []
    for stmt in sess.stmts:
        sql = str(stmt.compile(dialect=postgresql.dialect()))
        out.append(sql[sql.index("WHERE") :])
    return out


def test_the_index_predicate_and_the_refund_leg_agree_on_both_equalities():
    """planner 只在索引谓词能蕴含查询条件时才用它，所以这条测试钉的是**两侧**：
    迁移里写的 partial 谓词，和仓库真发出去的第二条 WHERE。

    单钉迁移一侧是不够的——查询那边把两条腿合成 ``type IN (…)``，迁移文本一字不变，
    这条索引（以及 474 那条）就双双静默失效：结果仍然正确，只是又变回全表扫描。"""
    # 迁移侧：字面谓词
    assert "ON public.point_transactions (reference_id)" in _SQUEEZED
    assert "WHERE type = 'refund' AND reference_type = 'agent_run'" in _SQUEEZED

    # 查询侧：两条腿，各自是等值（而不是 IN / LIKE / 函数包裹）
    wheres = _compiled_leg_wheres()
    assert len(wheres) == 2, "净扣要两条腿：consume 一条、refund 一条"
    for where in wheres:
        assert re.search(r"point_transactions\.type = %\(\w+\)s", where), where
        assert re.search(r"point_transactions\.reference_type = %\(\w+\)s", where), (
            where
        )


def test_it_is_not_unique():
    """⚠️ 与 mig 123 的 ``idx_point_transactions_unique_refund`` 并存是刻意的：那条
    首列是 ``team_id`` 且负责「同一引用至多一笔退款」的约束。把本索引也建成 UNIQUE
    等于用一个**少了 team_id 的更弱的键**把那条约束重述一遍 —— 跨团队的同名引用会
    直接写不进去。"""
    assert "CREATE UNIQUE INDEX" not in _SQUEEZED.upper()


def test_it_does_not_set_role():
    """迁移里 SET ROLE service_role 是主动降权，在 schema-drift 的裸库上必然
    permission denied（CLAUDE.md「迁移里不要写 SET ROLE」）。"""
    assert "SET ROLE" not in _SQUEEZED.upper()


def test_it_does_not_use_concurrently():
    """run-migration 把整批迁移喂进同一个 psql 事务，``CONCURRENTLY`` 在事务里不
    合法 —— 写了它整批都跑不起来。"""
    assert "CONCURRENTLY" not in _SQUEEZED.upper()


def _declared(name: str) -> Index:
    found = [ix for ix in PointTransactions.__table__.indexes if ix.name == name]
    assert found, f"{name} 没有 ORM 镜像；C1 门禁只管 ORM→库，反向不会替你发现"
    return found[0]


def test_the_orm_mirrors_the_index_as_partial():
    """C1 门禁（tests/db/test_orm_indexes_integration.py）按 (名字, 轴) 比对，
    「部分索引」是其中一条轴。声明成全表索引会转红——而它的作用正是不让读模型的人
    对「这条查询走不走索引」产生一个错误的、无法证伪的印象。"""
    ix = _declared(INDEX)
    assert [c.name for c in ix.columns] == ["reference_id"]
    assert ix.unique is not True
    where = ix.dialect_options["postgresql"].get("where")
    assert where is not None, "少了 postgresql_where，这条声明会在部分索引轴上漂移"
    assert "refund" in str(where) and "agent_run" in str(where)


def test_the_two_legs_have_two_distinct_indexes():
    """两条腿各一个索引，名字不同、谓词不同、列相同。合成一个（比如去掉 ``type``
    那个条件）就等于把 474 当初避免的那次全扫又放回来。"""
    consume = _declared("idx_point_transactions_agent_run_consume")
    refund = _declared(INDEX)
    assert consume.name != refund.name
    assert [c.name for c in consume.columns] == [c.name for c in refund.columns]
    w_consume = str(consume.dialect_options["postgresql"]["where"])
    w_refund = str(refund.dialect_options["postgresql"]["where"])
    assert "consume" in w_consume and "refund" not in w_consume
    assert "refund" in w_refund and "consume" not in w_refund
