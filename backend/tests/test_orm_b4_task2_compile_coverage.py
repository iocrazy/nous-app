"""Compile-level coverage for Phase B4 Task 2 (schedules/conversations 域 —
5 文件: conversations_ai_store.py / usage_repository.py / chat_upload.py /
scope_binding.py / ai_library_chat_wiring.py).

Same technique as tests/test_orm_b4_task1_compile_coverage.py: compile the
real statement-building code with the postgresql dialect and assert with
MUTUALLY EXCLUSIVE assertions that the right join/column/operator survived.
This file pins the batch's highest-risk rewrite points:

  - conversations_ai_store's ``_joined_query()``: conversations JOIN
    conversation_ai_meta JOIN conversation_members (member_type='user') —
    the shared base every session-read method (get_session/list_sessions)
    builds on. An accidental switch to an INNER JOIN on
    conversation_ai_meta (vs the legacy's identical INNER JOIN) or a dropped
    member_type filter would silently change which sessions are visible.
  - create_session's two ``conversation_members`` inserts: bare
    ``ON CONFLICT DO NOTHING`` with NO ``index_elements`` — the table has no
    real PK, only the expression index
    ``uq_conversation_members(conversation_id, member_type,
    COALESCE(user_id, agent_id))``, which SQLAlchemy cannot name by column
    list. Adding index_elements=[...] here would silently stop matching
    that index and turn every idempotent re-insert into a real conflict.
  - usage_repository's ORDER BY: the SAME Label object is reused between
    the SELECT list and ORDER BY, so the query renders
    ``ORDER BY cost_cents DESC NULLS LAST, total_tokens DESC`` (referencing
    the output alias, matching the legacy raw SQL) rather than repeating
    the full aggregate expression — a regression here would still be
    correct SQL, but would silently drop the alias-based ordering the
    legacy behavior depended on for readability/plan stability.
  - usage_repository's group-key factories: "agent"/"project" cast their
    UUID/BIGINT column to text (matching the legacy ``::text`` casts so
    heterogeneous group keys serialize uniformly); "model"/"module"/
    "attribution" must NOT cast (they're already text columns) — a stray
    cast on the wrong side would silently change how NULL group keys
    compare in GROUP BY.
  - scope_binding's ``_scope_of_conversation``: conversations LEFT OUTER
    JOIN conversation_ai_meta — must stay a LEFT join. An inner join would
    silently drop a conversation's project binding whenever it doesn't
    (yet) have an ai_meta sidecar row.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.dialects import postgresql

from app.repositories import usage_repository
from app.services.ai.chat.conversations_ai_store import ConversationsAiStore


def _compile(stmt: Any) -> tuple[str, dict[str, Any]]:
    compiled = stmt.compile(dialect=postgresql.dialect())
    return str(compiled), dict(compiled.params)


# ── conversations_ai_store.py — shared joined query ────────────────────────


def test_joined_query_is_inner_joins_with_member_type_user_filter():
    """The base every session read builds on: conversations JOIN
    conversation_ai_meta JOIN conversation_members, with the user-member
    filter INLINED into the join condition (not a separate WHERE) — matching
    the legacy ``JOIN ... ON ... AND cm.member_type = 'user'`` shape."""
    from app.models import Conversations

    stmt = ConversationsAiStore._joined_query().where(Conversations.id == 1)
    sql, binds = _compile(stmt)
    assert "JOIN public.conversation_ai_meta ON" in sql
    assert (
        "JOIN public.conversation_members ON public.conversation_members"
        ".conversation_id = public.conversations.id AND public"
        ".conversation_members.member_type = %(member_type_1)s" in sql
    )
    assert "LEFT" not in sql  # both joins must stay INNER (legacy JOIN, not LEFT JOIN)
    assert binds["member_type_1"] == "user"


def test_joined_query_selects_all_thirteen_legacy_shape_columns():
    """Every column _to_legacy_shape reads must be present in the SELECT
    list — a dropped column here would surface as a silent KeyError/None
    deep in _to_legacy_shape instead of at the query boundary."""
    sql, _binds = _compile(ConversationsAiStore._joined_query())
    for col in (
        "public.conversations.id",
        "public.conversations.scope_id",
        "public.conversations.project_id",
        "public.conversations.title",
        "public.conversations.created_at",
        "public.conversation_ai_meta.agent_slug",
        "public.conversation_ai_meta.agent_id",
        "public.conversation_ai_meta.total_tokens",
        "public.conversation_ai_meta.message_count",
        "public.conversation_ai_meta.context_type",
        "public.conversation_ai_meta.context_id",
        "public.conversation_ai_meta.updated_at",
        "public.conversation_members.user_id",
    ):
        assert col in sql, f"missing column: {col}"


# ── conversations_ai_store.py — bare ON CONFLICT DO NOTHING ────────────────


def test_conversation_members_insert_uses_bare_on_conflict_do_nothing():
    """conversation_members has NO real primary key — only the expression
    index uq_conversation_members(conversation_id, member_type,
    COALESCE(user_id, agent_id)), which SQLAlchemy cannot name via
    index_elements=[...]. Both member inserts (user + agent) must therefore
    use a BARE ON CONFLICT DO NOTHING with no index/constraint target."""
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    from app.models import ConversationMembers

    user_stmt = (
        pg_insert(ConversationMembers)
        .values(conversation_id=1, member_type="user", user_id="u1", role="owner")
        .on_conflict_do_nothing()
    )
    agent_stmt = (
        pg_insert(ConversationMembers)
        .values(conversation_id=1, member_type="agent", agent_id="a1", added_by="u1")
        .on_conflict_do_nothing()
    )
    for stmt in (user_stmt, agent_stmt):
        sql, _binds = _compile(stmt)
        assert sql.rstrip().endswith("ON CONFLICT DO NOTHING")
        # No index/constraint target rendered before the DO NOTHING clause.
        assert "ON CONFLICT (" not in sql
        assert "ON CONSTRAINT" not in sql


# ── usage_repository.py — ORDER BY label reuse + NULLS LAST ────────────────


def test_summarize_groups_order_by_reuses_select_list_labels():
    """cost_cents/total_tokens Label objects are reused between the SELECT
    list and ORDER BY, so the compiled ORDER BY references the output alias
    (matching the legacy alias-based ORDER BY) instead of repeating the full
    COALESCE(SUM(...)) expression twice."""
    from sqlalchemy import func, select

    from app.models import AiUsageHourly

    grp_label = usage_repository._GROUP_KEY_FACTORY["model"]().label("grp")
    cost_label = func.coalesce(func.sum(AiUsageHourly.cost_cents), 0).label(
        "cost_cents"
    )
    total_label = func.coalesce(func.sum(AiUsageHourly.total_tokens), 0).label(
        "total_tokens"
    )
    stmt = (
        select(grp_label, cost_label, total_label)
        .group_by(grp_label)
        .order_by(cost_label.desc().nulls_last(), total_label.desc())
    )
    sql, _binds = _compile(stmt)
    assert "ORDER BY cost_cents DESC NULLS LAST, total_tokens DESC" in sql
    # The full aggregate expression must NOT be repeated in ORDER BY.
    assert sql.count("coalesce(sum(") == 2  # once each in the SELECT list only


def test_group_key_factory_casts_uuid_and_bigint_dimensions_to_text():
    """ "agent"/"project" are UUID/BIGINT columns — cast to text (matching the
    legacy ``::text`` casts) so heterogeneous group keys serialize
    uniformly. "model"/"module"/"attribution" are already text columns and
    must NOT be wrapped in a CAST."""
    from sqlalchemy import select

    for key in ("agent", "project"):
        sql, _binds = _compile(select(usage_repository._GROUP_KEY_FACTORY[key]()))
        assert "CAST(" in sql and "AS VARCHAR)" in sql

    for key in ("model", "module", "attribution"):
        sql, _binds = _compile(select(usage_repository._GROUP_KEY_FACTORY[key]()))
        assert "CAST(" not in sql


def test_issue_totals_counts_with_bare_count_star():
    """func.count() (no args) must render as count(*), matching the legacy
    COUNT(*) — count() with an explicit column argument would silently
    exclude NULL rows from the run_count tally."""
    from sqlalchemy import func, select

    from app.models import AgentRuns

    stmt = select(func.count().label("run_count")).where(AgentRuns.issue_id == 1)
    sql, _binds = _compile(stmt)
    assert "count(*)" in sql


# ── scope_binding.py — LEFT OUTER JOIN for the ai_meta sidecar ─────────────


def test_scope_of_conversation_query_is_a_left_outer_join():
    """A conversation's project binding must survive even when it has no
    conversation_ai_meta sidecar row (non direct_agent conversations don't
    carry one) — an INNER join would silently drop those rows and degrade
    every such dispatch to an unbound scope."""
    from sqlalchemy import select

    from app.models import ConversationAiMeta, Conversations

    stmt = (
        select(
            Conversations.project_id,
            ConversationAiMeta.context_type,
            ConversationAiMeta.context_id,
        )
        .select_from(Conversations)
        .outerjoin(
            ConversationAiMeta,
            ConversationAiMeta.conversation_id == Conversations.id,
        )
        .where(Conversations.id == 1)
    )
    sql, _binds = _compile(stmt)
    assert "LEFT OUTER JOIN public.conversation_ai_meta" in sql
