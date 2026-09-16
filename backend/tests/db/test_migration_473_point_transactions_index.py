"""473 的形状由这里钉住：一个 partial 索引，谓词与查询里那两个等值条件逐字一致，
幂等写法，不 SET ROLE，以及同 PR 的 ORM 镜像。

照 tests/db/test_migration_462_deliverables.py 的读文件断言风格——不连库，纯读
SQL 文本，CI 的 schema-drift 才是真执行方。断言只读**可执行正文**（剥掉整行
注释）：本迁移的注释里原样复述了它所钉住的语句，对原始文本做子串匹配会在语句被
删、解释还留着时照样转绿。
"""

from __future__ import annotations

import pathlib
import re

import pytest
from sqlalchemy import Index

from app.models import PointTransactions

pytestmark = pytest.mark.unit

MIG = pathlib.Path(__file__).resolve().parents[3] / "supabase/migrations"
_RAW = (MIG / "473_point_transactions_agent_run_index.sql").read_text(encoding="utf-8")
BODY = "\n".join(
    line for line in _RAW.splitlines() if not line.strip().startswith("--")
)
_SQUEEZED = re.sub(r"\s+", " ", BODY)

INDEX = "idx_point_transactions_agent_run_consume"


def test_creates_the_index_idempotently():
    """整份迁移可重跑——run-migration 把一批文件喂进同一个 session，一次失败后的
    重跑不该在这一句上炸。"""
    assert f"CREATE INDEX IF NOT EXISTS {INDEX}" in _SQUEEZED


def test_the_predicate_matches_the_query_word_for_word():
    """planner 只在索引谓词能蕴含查询条件时才用它。这两个等值条件就是
    ``PointsRepository.charged_points_for_references`` 发出去的那两个；
    改了其中任何一边而不改另一边，索引会静默失效——查询照常返回正确结果，
    只是又变回全表扫描，没有任何探针会说出来。"""
    assert "ON public.point_transactions (reference_id)" in _SQUEEZED
    assert "WHERE type = 'consume' AND reference_type = 'agent_run'" in _SQUEEZED


def test_it_is_not_unique():
    """同一次 run 允许有多行流水（重试、补扣）——``charged_points_for_references``
    求和正是为此。建成 UNIQUE 会让第二次扣分直接写不进去。"""
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
    assert ix.unique is not True
    where = ix.dialect_options["postgresql"].get("where")
    assert where is not None, "少了 postgresql_where，这条声明会在部分索引轴上漂移"
    assert "consume" in str(where) and "agent_run" in str(where)
