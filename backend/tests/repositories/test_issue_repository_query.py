"""``list_for_user(q=)``（3c §2.3）。mig 166 的三个 trgm GIN（identifier /
title / description）从建好那天起没有任何谓词碰过（侦察 A3）。这条参数是它们的
第一个消费方，缺一列等于那个索引继续闲置而用户以为搜过了。"""

import pytest

from app.repositories.issue_repository import issue_repository

pytestmark = pytest.mark.unit


def _sql(stmt) -> str:
    from sqlalchemy.dialects import postgresql

    return str(stmt.compile(dialect=postgresql.dialect()))


def test_a_query_matches_identifier_title_and_description():
    sql = _sql(issue_repository._q_predicate_stmt("rain"))
    assert sql.count("ILIKE") == 3
    for col in ("identifier", "title", "description"):
        assert col in sql


def test_a_query_declares_its_escape_char():
    assert "ESCAPE" in _sql(issue_repository._q_predicate_stmt("100%"))


def test_a_blank_query_adds_no_predicate():
    # 空串与只有空白 = 没搜。加一个 ``%%`` 谓词会让 GIN 失效并全表扫。
    assert issue_repository._q_predicate("  ") is None
    assert issue_repository._q_predicate(None) is None


def test_the_term_is_escaped_before_it_becomes_a_pattern():
    """用户搜一个字面 ``%`` 必须匹配 ``%`` 本身，不是匹配一切。

    ``ESCAPE`` 子句在场只说明「反斜杠是转义符」；真正干活的是
    ``escape_like``。没有它，``%`` 原样进模式 = 一次全表匹配（同
    ``search_docs`` 那侧被突变钉住的那条）。
    """
    stmt = issue_repository._q_predicate_stmt("100%")
    bound = stmt.compile(
        dialect=__import__(
            "sqlalchemy.dialects.postgresql", fromlist=["dialect"]
        ).dialect()
    ).params
    assert set(bound.values()) == {"%100\\%%"}
