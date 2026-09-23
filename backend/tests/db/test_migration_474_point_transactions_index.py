"""474 的形状由这里钉住：一个 partial 索引，谓词与查询里那两个等值条件逐字一致，
幂等写法，不 SET ROLE，以及同 PR 的 ORM 镜像。

照 tests/db/test_migration_462_deliverables.py 的读文件断言风格——不连库，纯读
SQL 文本，CI 的 schema-drift 才是真执行方。断言只读**可执行正文**（剥掉整行
注释）：本迁移的注释里原样复述了它所钉住的语句，对原始文本做子串匹配会在语句被
删、解释还留着时照样转绿。
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
_RAW = (MIG / "474_point_transactions_agent_run_index.sql").read_text(encoding="utf-8")
BODY = "\n".join(
    line for line in _RAW.splitlines() if not line.strip().startswith("--")
)
_SQUEEZED = re.sub(r"\s+", " ", BODY)

INDEX = "idx_point_transactions_agent_run_consume"


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


def _compiled_query_where() -> str:
    """``charged_points_for_references`` **真发出去**的那条语句的 WHERE 子句。

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
    sql = str(sess.stmts[0].compile(dialect=postgresql.dialect()))
    return sql[sql.index("WHERE") :]


def test_the_index_predicate_and_the_query_agree_on_both_equalities():
    """planner 只在索引谓词能蕴含查询条件时才用它，所以这条测试钉的是**两侧**：
    迁移里写的 partial 谓词，和仓库真发出去的 WHERE。

    单钉迁移一侧是不够的——查询那边把 ``reference_type`` 的等值改成 IN、或者
    干脆去掉，迁移文本一字不变，测试照样绿，而索引已经静默失效：结果仍然正确，
    只是又变回全表扫描，没有任何探针会说出来。

    查询侧只比**形状**不比取值：两个等值条件编译成 bind 参数（``%(type_1)s``），
    取值由调用方传入，已经在 ``tests/repositories/test_points_charged_references.py``
    里按绑定值断言过了。"""
    # 迁移侧：字面谓词
    assert "ON public.point_transactions (reference_id)" in _SQUEEZED
    assert "WHERE type = 'consume' AND reference_type = 'agent_run'" in _SQUEEZED

    # 查询侧：同样两列，同样是等值（而不是 IN / LIKE / 函数包裹）
    where = _compiled_query_where()
    assert re.search(r"point_transactions\.type = %\(\w+\)s", where), where
    assert re.search(r"point_transactions\.reference_type = %\(\w+\)s", where), where


def test_it_is_not_unique():
    """474 这个文件本身建的是普通索引（当时的考虑是同一次 run 可能有多行流水）。
    2026-09-22 用户裁定「一树一扣」之后，mig 486 把它升成了 UNIQUE ——
    那一步由 ``test_migration_486_consume_unique.py`` 钉住；这里只证 474 的
    文件没被改写（已发布的迁移不回头改）。"""
    assert "CREATE UNIQUE INDEX" not in _SQUEEZED.upper()


def test_it_does_not_set_role():
    """迁移里 SET ROLE service_role 是主动降权，在 schema-drift 的裸库上必然
    permission denied（CLAUDE.md「迁移里不要写 SET ROLE」）。"""
    assert "SET ROLE" not in _SQUEEZED.upper()


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
    # mig 486 升 UNIQUE（同一 run 只许一条 consume）；ORM 与库同批。
    assert ix.unique is True
    where = ix.dialect_options["postgresql"].get("where")
    assert where is not None, "少了 postgresql_where，这条声明会在部分索引轴上漂移"
    assert "consume" in str(where) and "agent_run" in str(where)
