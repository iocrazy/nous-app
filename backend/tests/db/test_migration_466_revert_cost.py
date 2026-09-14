"""466 的形状：两处放宽 NOT NULL、四个新列、两个具名 CHECK、幂等、不 SET ROLE，
以及同 PR 的 ORM 镜像。照 tests/db/test_migration_462_deliverables.py 的读文件风格
——只读可执行正文（剥掉整行注释），注释里复述语句不算数。"""

from __future__ import annotations

import pathlib
import re

import pytest
from sqlalchemy import CheckConstraint

from app.models.agents import RunDeliverables
from app.models.ai import AiModelPrices
from app.models.scripts import ScriptShotOps

pytestmark = pytest.mark.unit

MIG = pathlib.Path(__file__).resolve().parents[3] / "supabase/migrations"
_RAW = (MIG / "466_harness_3b_revert_cost.sql").read_text(encoding="utf-8")
BODY = "\n".join(
    line for line in _RAW.splitlines() if not line.strip().startswith("--")
)
_SQ = re.sub(r"[ \t]+", " ", BODY)

NEW_COLS = (
    "actor_user_id",
    "reverted_from_version",
    "ledger_ref",
    "actor",
    "per_call_cents",
)


def test_both_run_id_columns_lose_not_null():
    """回退是人手占号：两张表的 run_id 都要允许 NULL，否则登记结构上不可能。"""
    assert _SQ.count("ALTER COLUMN run_id DROP NOT NULL") == 2


def test_adds_every_new_column_idempotently():
    for col in NEW_COLS:
        assert f"ADD COLUMN IF NOT EXISTS {col}" in _SQ, col
    assert "ADD COLUMN " not in _SQ.replace("ADD COLUMN IF NOT EXISTS ", "")


def test_both_checks_are_named_and_say_run_or_actor():
    assert "CONSTRAINT run_deliverables_run_or_actor" in _SQ
    assert "CHECK (run_id IS NOT NULL OR actor_user_id IS NOT NULL)" in _SQ
    assert "CONSTRAINT script_shot_ops_run_or_actor" in _SQ
    assert "CHECK (run_id IS NOT NULL OR actor IS NOT NULL)" in _SQ
    assert _SQ.count("DROP CONSTRAINT IF EXISTS") == 2


def test_no_third_index_no_set_role_single_transaction():
    """307 与 456 已各建一个索引；再建一个只是多一份写放大。"""
    assert "idx_generated_media_promoted_resource" not in BODY
    assert not any(
        re.match(r"(?i)^SET\s+ROLE\b", line.strip()) for line in BODY.splitlines()
    ), "migrations must not SET ROLE"
    assert BODY.count("BEGIN;") == 1 and BODY.count("COMMIT;") == 1


def test_the_orm_mirrors_the_nullable_run_ids_and_new_columns():
    assert RunDeliverables.__table__.columns["run_id"].nullable
    assert ScriptShotOps.__table__.columns["run_id"].nullable
    d = RunDeliverables.__table__.columns
    for name in ("actor_user_id", "reverted_from_version", "ledger_ref"):
        assert name in d and d[name].nullable, name
    assert str(d["actor_user_id"].type) == "UUID"
    assert ScriptShotOps.__table__.columns["actor"].nullable
    price = AiModelPrices.__table__.columns["per_call_cents"]
    assert price.nullable and str(price.type) == "NUMERIC(12, 4)"


def test_the_orm_mirrors_both_check_constraints():
    """CHECK 漏在 ORM 侧，drift 门禁比的是名字——少一个就是两个 schema。"""

    def names(model):
        return {
            c.name
            for c in model.__table__.constraints
            if isinstance(c, CheckConstraint)
        }

    assert "run_deliverables_run_or_actor" in names(RunDeliverables)
    assert "script_shot_ops_run_or_actor" in names(ScriptShotOps)
