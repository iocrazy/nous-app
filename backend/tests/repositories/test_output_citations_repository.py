"""反查的两个读法（3c §2.2）。"""

import pytest

from app.repositories.output_citations_repository import OutputCitationsRepository

pytestmark = pytest.mark.unit


def _sql(stmt) -> str:
    from sqlalchemy.dialects import postgresql

    return str(stmt.compile(dialect=postgresql.dialect()))


def test_list_for_ref_pins_all_three_coordinates():
    sql = _sql(OutputCitationsRepository()._list_stmt("script_shot", "9", 4))
    for col in ("kind", "ref_id", "version"):
        assert col in sql
    assert "ORDER BY" in sql.upper()


def test_counts_for_chain_groups_by_version_and_does_not_filter_on_it():
    sql = _sql(OutputCitationsRepository()._counts_stmt("script_shot", "9"))
    # 整条链一次查完——每版一次查询会让二十版的链发二十次往返。
    assert "GROUP BY" in sql.upper() and "version =" not in sql


def test_a_reposted_message_does_not_double_count():
    """重投（编辑、重发、重放）不是错误，所以 INSERT 不该抛 —— 靠 UNIQUE
    (message_id, kind, ref_id, version) + DO NOTHING 吃掉它。"""
    sql = _sql(
        OutputCitationsRepository()._insert_stmt(
            [
                {
                    "kind": "script_shot",
                    "ref_id": "9",
                    "version": 4,
                    "issue_id": 96,
                    "conversation_id": 700,
                    "message_id": 5001,
                    "cited_by_user_id": "11111111-1111-1111-1111-111111111111",
                }
            ]
        )
    ).upper()
    assert "ON CONFLICT DO NOTHING" in sql
