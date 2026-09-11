"""462 的形状由这里钉住：五个血缘列、两个产出索引、收件箱 dedupe 索引、
幂等写法、不 SET ROLE，以及同 PR 的 ORM 镜像。

照 tests/db/test_migration_461_orchestration.py 的读文件断言风格——不连库，
纯读 SQL 文本，CI 的 schema-drift 才是真执行方。断言只读**可执行正文**
（剥掉整行注释）：本迁移的注释里原样复述了它所钉住的语句，对原始文本做子串
匹配会在语句被删、解释还留着时照样转绿。
"""

from __future__ import annotations

import pathlib
import re

import pytest

from app.models.agents import AgentRunInbox, RunDeliverables

pytestmark = pytest.mark.unit

MIG = pathlib.Path(__file__).resolve().parents[3] / "supabase/migrations"
_RAW = (MIG / "462_harness_p4_phase3a_deliverables.sql").read_text(encoding="utf-8")
BODY = "\n".join(
    line for line in _RAW.splitlines() if not line.strip().startswith("--")
)
_SQUEEZED = re.sub(r"[ \t]+", " ", BODY)

LINEAGE_COLUMNS = ("title", "model", "cost_cents", "turn", "step")
VERSION_KEY = "run_deliverables_kind_ref_version_key"
LATEST_INDEX = "idx_run_deliverables_ref_latest"
DEDUPE_KEY = "agent_run_inbox_dedupe_live_key"


def test_adds_the_five_lineage_columns():
    for col in LINEAGE_COLUMNS:
        assert f"ADD COLUMN IF NOT EXISTS {col}" in _SQUEEZED, col


def test_version_uniqueness_is_scoped_to_the_object():
    """(kind, ref_id) alone would make v2 of an object a conflict rather than
    a new version — the chain 3a exists to record."""
    assert VERSION_KEY in BODY
    assert "(kind, ref_id, version)" in _SQUEEZED


def test_latest_version_index_orders_newest_first():
    assert LATEST_INDEX in BODY
    assert "(kind, ref_id, version DESC)" in _SQUEEZED


def test_inbox_dedupe_index_only_covers_live_rows():
    """一个不带 WHERE 的唯一索引会把「过期后重新排队」这条合法路径变成冲突。"""
    assert DEDUPE_KEY in BODY
    assert "WHERE content ? 'dedupe_key' AND expired_at IS NULL" in _SQUEEZED


def test_every_statement_is_idempotent():
    """重跑一次迁移不许炸：CI 与生产各跑一遍是常态。"""
    creates = re.findall(r"CREATE (?:UNIQUE )?INDEX(?! IF NOT EXISTS)", BODY)
    assert not creates, creates
    assert "ADD COLUMN " not in _SQUEEZED.replace("ADD COLUMN IF NOT EXISTS ", "")


def test_no_set_role_and_single_transaction():
    assert not any(
        re.match(r"(?i)^SET\s+ROLE\b", line.strip()) for line in BODY.splitlines()
    ), "migrations must not SET ROLE (see CLAUDE.md)"
    assert BODY.count("BEGIN;") == 1 and BODY.count("COMMIT;") == 1


# ── ORM 镜像（同 PR，schema-drift 门禁两向零容忍）────────────────────────


def test_the_orm_mirrors_the_five_lineage_columns():
    cols = RunDeliverables.__table__.columns
    for name in LINEAGE_COLUMNS:
        assert name in cols, name
        assert cols[name].nullable, f"{name} must stay nullable (存量零行也按可空写)"
    assert str(cols["cost_cents"].type) == "NUMERIC(12, 4)"


def test_the_orm_mirrors_the_two_deliverable_indexes():
    by_name = {ix.name: ix for ix in RunDeliverables.__table__.indexes}
    assert VERSION_KEY in by_name and LATEST_INDEX in by_name, sorted(by_name)
    assert by_name[VERSION_KEY].unique, "并发闸门只有 UNIQUE 才成立"
    assert not by_name[LATEST_INDEX].unique
    assert [e.name for e in by_name[VERSION_KEY].expressions] == [
        "kind",
        "ref_id",
        "version",
    ]


def test_the_orm_mirrors_the_inbox_dedupe_index_including_its_predicate():
    """partial 谓词漏在 ORM 侧，两边就是两个不同的索引——门禁比的是名字。"""
    by_name = {ix.name: ix for ix in AgentRunInbox.__table__.indexes}
    assert DEDUPE_KEY in by_name, sorted(by_name)
    index = by_name[DEDUPE_KEY]
    assert index.unique
    where = str(index.dialect_kwargs["postgresql_where"])
    assert "content ? 'dedupe_key'" in where and "expired_at IS NULL" in where
