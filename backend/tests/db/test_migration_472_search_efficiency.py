"""472 的形状 + 同 PR 的 ORM 镜像。读法照 test_migration_466_revert_cost.py：
只读可执行正文（剥掉整行注释）—— 注释里复述一句 SQL 不算数。"""

from __future__ import annotations

import pathlib
import re

import pytest
from sqlalchemy import CheckConstraint

from app.models import AgentRuns, AiUsageHourly, InboxNotifications
from app.models.search import OutputCitations, SearchDocs

pytestmark = pytest.mark.unit

MIG = pathlib.Path(__file__).resolve().parents[3] / "supabase/migrations"
_RAW = (MIG / "472_harness_3c_search_efficiency.sql").read_text(encoding="utf-8")
BODY = "\n".join(
    line for line in _RAW.splitlines() if not line.strip().startswith("--")
)
_SQ = re.sub(r"[ \t]+", " ", BODY)

RUN_COLS = ("steps", "tool_calls", "tool_errors", "deliverables", "turn_end_reason")
HOURLY_COLS = ("run_count", "failed_runs", "tool_calls", "tool_errors", "deliverables")


def _add_column_stmt(table: str) -> str:
    """那一条 `ALTER TABLE public.<table> ADD COLUMN …;`，单独取出来。

    两族计数器共用 tool_calls / tool_errors / deliverables 三个名字，而它们的
    可空性正好相反 —— 整份文件级的子串断言分不清谁是谁（小时表那条
    `... tool_calls INTEGER NOT NULL DEFAULT 0` 会让 agent_runs 的否定断言也
    命中），所以必须按语句切。
    """
    m = re.search(rf"ALTER TABLE public\.{table}\s+ADD COLUMN", _SQ)
    assert m is not None, table
    return _SQ[m.start() : _SQ.index(";", m.start())]


def test_both_tables_and_every_index_are_idempotent():
    assert "CREATE TABLE IF NOT EXISTS public.search_docs" in _SQ
    assert "CREATE TABLE IF NOT EXISTS public.output_citations" in _SQ
    assert "CREATE TABLE " not in _SQ.replace("CREATE TABLE IF NOT EXISTS ", "")
    assert set(re.findall(r"CREATE INDEX IF NOT EXISTS (\w+)", _SQ)) == {
        "idx_search_docs_title_trgm",
        "idx_search_docs_body_trgm",
        "idx_search_docs_team_updated",
        "idx_search_docs_issue",
        "idx_search_docs_project",
        "idx_output_citations_ref",
        "idx_output_citations_issue",
        "idx_agent_runs_user_created",
    }
    assert "CREATE INDEX " not in _SQ.replace("CREATE INDEX IF NOT EXISTS ", "")
    for col in RUN_COLS + HOURLY_COLS:
        assert f"ADD COLUMN IF NOT EXISTS {col}" in _SQ, col
    assert "ADD COLUMN " not in _SQ.replace("ADD COLUMN IF NOT EXISTS ", "")


def test_the_two_counter_families_have_opposite_nullability():
    """小时表五列是 upsert 的加法累加器（`col + EXCLUDED.col`），可空会把整行算
    成 NULL、一整个小时桶静默变未知；agent_runs 五列必须可空 —— 存量行的
    「那时还没在数」不能被 DEFAULT 0 伪装成「一次都没调」。"""
    hourly = _add_column_stmt("ai_usage_hourly")
    for col in HOURLY_COLS:
        assert (
            f"ADD COLUMN IF NOT EXISTS {col} INTEGER NOT NULL DEFAULT 0" in hourly
        ), col
    runs = _add_column_stmt("agent_runs")
    for col in RUN_COLS:
        assert f"ADD COLUMN IF NOT EXISTS {col} INTEGER NOT NULL" not in runs, col
        assert f"ADD COLUMN IF NOT EXISTS {col} TEXT NOT NULL" not in runs, col


def test_both_mirrors_are_wired_to_the_truth_they_mirror():
    """投影与引用都是镜像，不是独立事实 —— 被投影的 run / 被引用的 message 删掉
    之后它们必须一起消失，否则搜索结果里长出指向不存在行的幽灵，而没有任何探针
    会说出来。议题走 SET NULL：议题删了不代表那次运行没发生过。"""
    for conname, on_delete in (
        ("search_docs_run_id_fkey", "CASCADE"),
        ("search_docs_issue_id_fkey", "SET NULL"),
        ("output_citations_message_id_fkey", "CASCADE"),
        ("output_citations_issue_id_fkey", "SET NULL"),
    ):
        assert f"ADD CONSTRAINT {conname}" in _SQ, conname
        after = _SQ[_SQ.index(f"ADD CONSTRAINT {conname}") :]
        assert f"ON DELETE {on_delete}" in after[: after.index(";")], conname
        # 幂等：建表那句的 IF NOT EXISTS 只看表在不在，看不见表里少了什么约束。
        assert f"WHERE conname = '{conname}'" in _SQ, conname


def test_the_team_backfill_runs_first_and_only_on_finished_runs():
    assert _SQ.index("UPDATE public.agent_runs a") < _SQ.index(
        "INSERT INTO public.search_docs"
    ), "team_id 必须先补：search_docs 的 DO NOTHING 让 run 行只有一次机会"
    assert "a.ended_at IS NOT NULL" in _SQ and "a.team_id IS NULL" in _SQ
    assert _SQ.count("ON CONFLICT (entity_kind, entity_id) DO NOTHING") == 2


def test_new_tables_are_service_role_only_and_the_file_never_sets_roles():
    """anon key 是烤进浏览器包的公开值；多一条给 anon/authenticated 的策略就是
    一个公开读口（CLAUDE.md 2026-09-11）。"""
    for tbl in ("search_docs", "output_citations"):
        assert f"ALTER TABLE public.{tbl} ENABLE ROW LEVEL SECURITY" in _SQ
        assert f"{tbl}_service_role_all ON public.{tbl}" in _SQ
    assert "TO anon" not in _SQ and "TO authenticated" not in _SQ
    assert not any(
        re.match(r"(?i)^SET\s+ROLE\b", line.strip()) for line in BODY.splitlines()
    )
    assert BODY.count("BEGIN;") == 1 and BODY.count("COMMIT;") == 1


def test_the_orm_mirrors_both_new_tables_and_every_index():
    """C1 门禁按 (名字, 有序列清单) 对账 —— 名字对、列序错照样是两个索引。"""
    assert (SearchDocs.__tablename__, OutputCitations.__tablename__) == (
        "search_docs",
        "output_citations",
    )
    d = SearchDocs.__table__.columns
    assert not d["entity_kind"].nullable and not d["entity_id"].nullable
    assert not d["title"].nullable and d["body"].nullable
    c = OutputCitations.__table__.columns
    for name in ("kind", "ref_id", "version", "message_id", "cited_by_user_id"):
        assert not c[name].nullable, name
    got = {
        ix.name: [col.name for col in ix.columns]
        for model in (SearchDocs, OutputCitations)
        for ix in model.__table__.indexes
    }
    assert got == {
        "idx_search_docs_title_trgm": ["title"],
        "idx_search_docs_body_trgm": ["body"],
        "idx_search_docs_team_updated": ["team_id", "updated_at"],
        "idx_search_docs_issue": ["issue_id"],
        "idx_search_docs_project": ["project_id"],
        "idx_output_citations_ref": ["kind", "ref_id", "version"],
        "idx_output_citations_issue": ["issue_id"],
    }
    opts = {
        ix.name: ix.dialect_options["postgresql"] for ix in SearchDocs.__table__.indexes
    }
    assert opts["idx_search_docs_body_trgm"]["where"] is not None
    assert opts["idx_search_docs_project"]["where"] is not None
    assert opts["idx_search_docs_issue"].get("where") is None
    # trgm 索引的**访问方法与 opclass** 也在 C1 的对账面上：名字与列都对而
    # using/ops 丢了，声明的就是一棵 btree —— 它对 `%词%` 完全无用，而按名字
    # 看一切正常。
    for ix_name, col in (
        ("idx_search_docs_title_trgm", "title"),
        ("idx_search_docs_body_trgm", "body"),
    ):
        assert opts[ix_name]["using"] == "gin", ix_name
        assert opts[ix_name]["ops"] == {col: "gin_trgm_ops"}, ix_name
    for ix_name in ("idx_search_docs_team_updated", "idx_search_docs_issue"):
        assert not opts[ix_name].get("using"), ix_name


def test_the_orm_mirrors_the_new_agent_run_and_hourly_columns():
    runs = AgentRuns.__table__.columns
    for col in RUN_COLS:
        assert col in runs and runs[col].nullable, col
    assert "idx_agent_runs_user_created" in {
        ix.name for ix in AgentRuns.__table__.indexes
    }
    hourly = AiUsageHourly.__table__.columns
    for col in HOURLY_COLS:
        assert col in hourly and not hourly[col].nullable, col


def test_the_inbox_check_and_partial_index_stop_drifting():
    """A4 的一半：ORM kind CHECK 缺 agent_question（第三份口径）；
    idx_inbox_notifications_user_unread 库里是 WHERE read_at IS NULL 的部分索引，
    ORM 声明成全表 —— 名字里的 `unread` 正是那个丢掉的谓词。"""
    check = next(
        c
        for c in InboxNotifications.__table__.constraints
        if isinstance(c, CheckConstraint) and c.name == "inbox_notifications_kind_check"
    )
    assert "agent_question" in str(check.sqltext)
    ix = next(
        i
        for i in InboxNotifications.__table__.indexes
        if i.name == "idx_inbox_notifications_user_unread"
    )
    assert ix.dialect_options["postgresql"]["where"] is not None
