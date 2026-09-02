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

from app.repositories.assets_repository import AssetsRepository, _like_escape

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


# ── M2: the q filter matches the user's text LITERALLY ─────────────────────


def _compiled(**kw):
    stmt = AssetsRepository()._list_stmt(SCOPE, **kw)
    return stmt.compile(dialect=postgresql.dialect())


def test_no_q_emits_no_ilike():
    """Negative control — otherwise "ILIKE is present" could just be it always
    being there."""
    assert "ILIKE" not in _sql()


def test_q_emits_escape_on_both_columns():
    """``escape=`` on only one side of the ``or_`` would make the SAME query
    behave differently depending on which column matched: the name compared
    literally, the description still reading ``_`` as a wildcard."""
    sql = _sql(q="a_b")
    assert sql.count("ILIKE") == 2
    assert sql.count("ESCAPE '\\\\'") == 2


@pytest.mark.parametrize(
    "raw,pattern",
    [
        # `_` matches any single char in LIKE — unescaped, "a_b" also finds "axb".
        ("a_b", "%a\\_b%"),
        # `%` matches everything — unescaped, this q is not a filter at all.
        ("100%", "%100\\%%"),
        # The escape char itself, escaped FIRST so it does not double-escape the
        # backslashes the other two rules add.
        ("back\\slash", "%back\\\\slash%"),
        # Nothing to escape: the plain path must stay byte-for-byte unchanged.
        ("plain", "%plain%"),
        # Surrounding whitespace is still stripped before the wildcards go on.
        ("  spaced  ", "%spaced%"),
    ],
)
def test_the_bound_pattern_escapes_only_the_users_text(raw, pattern):
    """The ``%`` wildcards are OURS and must stay live; everything between them
    is the user's and must be inert."""
    params = _compiled(q=raw).params
    assert params["name_1"] == pattern
    assert params["description_1"] == pattern


def test_the_users_text_never_reaches_the_sql_string():
    """Escaping is about matching semantics, not injection — the value has
    always been bound. Pinned so a future "just interpolate it" cannot land
    quietly alongside the escaping."""
    assert "DROP TABLE" not in _sql(q="x'; DROP TABLE assets; --")


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("a_b", "a\\_b"),
        ("100%", "100\\%"),
        ("a\\b", "a\\\\b"),
        ("_%\\", "\\_\\%\\\\"),
        ("", ""),
    ],
)
def test_like_escape_unit(raw, expected):
    """Ordering matters: escaping ``\\`` after ``%``/``_`` would re-escape the
    backslashes just added, turning ``a_b`` into ``a\\\\_b`` — which matches a
    LITERAL backslash followed by anything, i.e. nothing the user typed."""
    assert _like_escape(raw) == expected


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


def test_count_by_type_counts_only_library_members():
    """mig 449: the badges sit above a shelf that defaults to
    ``library='in'``. Counting project-originated rows here would put a number
    on the sidebar the grid below it cannot show — the same "badge and grid
    silently disagree" failure the preset exclusion above exists to prevent."""
    assert "public.assets.in_library IS true" in _counts_sql()


# ── M3 (mig 449): explicit library membership ──────────────────────────────


def test_the_default_library_filter_is_in_not_all():
    """The DEFAULT is the load-bearing half of this change. A repo default of
    "all" would mean any caller that forgot the argument silently widened the
    shelf back to including every name a script mentioned."""
    assert _sql() == _sql(library="in")
    assert "public.assets.in_library IS true" in _sql()


def test_library_out_selects_the_other_side():
    sql = _sql(library="out")
    assert "public.assets.in_library IS false" in sql
    assert "public.assets.in_library IS true" not in sql


def test_library_all_emits_no_membership_predicate_at_all():
    """The negative control, and the reason ``_library_predicate`` returns None
    for "all" instead of a ``true`` tautology: with a tautology this assertion
    could not tell "unfiltered" from "filtered to everything"."""
    sql = _sql(library="all")
    # ``select(Assets)`` names the column in the SELECT list either way, so the
    # assertion has to be about the PREDICATE form specifically.
    assert "public.assets.in_library" in sql, "sanity: the column is still selected"
    assert "public.assets.in_library IS" not in sql
    assert "in_library IS" not in sql.split("WHERE", 1)[1]


def test_unknown_library_value_raises_instead_of_falling_back():
    """Same rule as ``sort``: a silent fallback to "in" would answer a
    different question than the caller asked and look exactly like a working
    filter. The router pins the vocabulary, so this is a programming error."""
    with pytest.raises(ValueError):
        _sql(library="yes")


def test_the_membership_predicate_composes_with_the_other_filters():
    """The four narrowing clauses are independent — a shelf filtered to one
    project AND to library members must emit both, not the last one written."""
    sql = _sql(library="out", asset_type="character", tag="hero", project_id=55)
    assert "public.assets.in_library IS false" in sql
    assert "public.assets.asset_type = " in sql
    assert "jsonb_path_exists" in sql
    assert "asset_project_refs" in sql


# ── resolve_legacy (the pre-P3 canvas card → asset map) ────────────────────


def _legacy_stmt(table: str = "project_characters", legacy_id: int = 12):
    return AssetsRepository()._resolve_legacy_stmt(SCOPE, table, legacy_id)


def _legacy_sql(**kw) -> str:
    return str(_legacy_stmt(**kw).compile(dialect=postgresql.dialect()))


def test_resolve_legacy_uses_jsonb_containment():
    """``@>``, not an unnest or a text LIKE. ``legacy_ids`` is a LIST of
    ``[table, id]`` pairs (a merged asset carries several), and containment is
    the operator that answers "is this pair among them" without knowing how
    many there are."""
    assert "public.assets.attrs @> " in _legacy_sql()


def test_the_pair_rides_as_one_bound_jsonb_value():
    """Both halves in ONE parameter — an id compared on its own would let
    ``project_characters`` 7 answer for ``project_lib_entities`` 7, which is a
    different entity in a different table."""
    params = _legacy_stmt().compile(dialect=postgresql.dialect()).params
    assert list(params.values())[1] == {"legacy_ids": [["project_characters", 12]]}


def test_the_legacy_id_is_a_json_number_not_a_string():
    """The migration wrote ``int(entity_id)``. ``"12"`` and ``12`` are
    different JSONB scalars, so a stringified id matches NOTHING — and an empty
    result here reads exactly like "that entity was never migrated"."""
    pair = list(_legacy_stmt().compile(dialect=postgresql.dialect()).params.values())[1]
    assert pair["legacy_ids"][0][1] == 12
    assert not isinstance(pair["legacy_ids"][0][1], str)


def test_the_table_label_is_bound_never_interpolated():
    sql = _legacy_sql(table="x'; DROP TABLE assets; --")
    assert "DROP TABLE" not in sql


def test_resolve_legacy_is_scoped_and_hides_deleted_rows():
    sql = _legacy_sql()
    assert "public.assets.scope_id = " in sql
    assert str(SCOPE) not in sql
    assert "public.assets.deleted_at IS NULL" in sql


def test_resolve_legacy_does_not_union_system_presets():
    """``get`` unions ``is_system_preset`` into every scope; this must not. A
    preset has no legacy row behind it, so widening the read could only add
    rows that can never match."""
    assert "is_system_preset" not in _legacy_sql()


def test_resolve_legacy_is_deterministic():
    """``LIMIT 1`` without an ORDER BY is a coin flip, and nothing in the
    schema forbids two assets carrying the same legacy pair."""
    sql = _legacy_sql()
    assert "ORDER BY public.assets.id ASC" in sql
    assert "LIMIT" in sql
