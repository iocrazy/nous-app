"""Compiled-SQL pins for ``AssetsRepository._list_stmt`` (P2 filters + sorts).

The unit-level session is stubbed everywhere else in this suite, so a query
this repo builds is otherwise only ever executed by
``tests/db/test_assets_repository_integration.py`` (which needs a DB and is
skipped without one). Compiling the statement against the real PostgreSQL
dialect is the part that can run anywhere: it proves the ``jsonb_path_exists``
predicate is emitted at all, that the jsonpath is a fixed literal and the
user's value rides as a bound parameter, and that each ``sort`` produces a
different ORDER BY instead of silently collapsing to the default.

It cannot prove PostgreSQL ACCEPTS the jsonpath — case 12 in the integration
file does that.
"""

from __future__ import annotations

import pytest
from sqlalchemy.dialects import postgresql

from app.repositories.assets_repository import AssetsRepository

SCOPE = 727145299382534200


def _sql(**kw) -> str:
    stmt = AssetsRepository()._list_stmt(SCOPE, **kw)
    return str(stmt.compile(dialect=postgresql.dialect()))


def test_no_tag_filter_emits_no_jsonb_path_predicate():
    """The negative control: without it, "the predicate is present" could just
    be it always being there."""
    assert "jsonb_path_exists" not in _sql()


def test_tag_filter_emits_jsonb_path_exists_over_every_group():
    sql = _sql(tag="hero")
    assert "jsonb_path_exists" in sql
    # '$.*' walks the tag groups, '[*]' their members (lax mode also matches a
    # scalar group value) — a plain '@>' cannot reach into nested arrays.
    assert "$.*[*] ? (@ == $v)" in sql
    assert "jsonb_build_object" in sql


def test_the_tag_value_is_bound_not_interpolated():
    """The jsonpath is ours and constant; the value is the caller's and must
    never be spliced into the SQL text."""
    sql = _sql(tag="hero'; DROP TABLE assets; --")
    assert "DROP TABLE" not in sql


@pytest.mark.parametrize(
    "sort,expected",
    [
        ("recent", "ORDER BY public.assets.updated_at DESC, public.assets.id DESC"),
        ("name", "ORDER BY lower(public.assets.name) ASC, public.assets.id ASC"),
    ],
)
def test_each_sort_emits_its_own_order_by(sort, expected):
    assert expected in " ".join(_sql(sort=sort).split())


def test_default_sort_is_recent():
    assert _sql() == _sql(sort="recent")


def test_unknown_sort_raises_instead_of_falling_back():
    """A silent fallback to 'recent' would answer a sort the caller did not ask
    for — and look exactly like a working one."""
    with pytest.raises(ValueError):
        _sql(sort="readiness")


# ── count_by_type (the sidebar badges) ─────────────────────────────────────


def _counts_sql() -> str:
    stmt = AssetsRepository()._count_by_type_stmt(SCOPE)
    return str(stmt.compile(dialect=postgresql.dialect()))


def test_count_by_type_groups_by_type():
    sql = _counts_sql()
    assert "count(*)" in sql
    assert "GROUP BY public.assets.asset_type" in sql


def test_count_by_type_excludes_soft_deleted_rows():
    """A trashed asset must not keep inflating its badge — the badge is what
    tells the user the delete took effect."""
    assert "public.assets.deleted_at IS NULL" in _counts_sql()


def test_count_by_type_excludes_system_presets():
    """``assets_scope_or_preset`` is an OR, so a preset MAY carry a scope_id:
    the scope predicate alone would not keep global rows out of a team's own
    tally. This is the predicate that does."""
    assert "public.assets.is_system_preset IS false" in _counts_sql()


def test_count_by_type_is_scoped_and_binds_the_scope_id():
    sql = _counts_sql()
    assert "public.assets.scope_id = " in sql
    # The scope is a bound parameter, never spliced into the SQL text.
    assert str(SCOPE) not in sql
