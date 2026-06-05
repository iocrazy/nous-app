"""Unit tests for the ``scoped_sql`` raw-SQL tenant guard (app/db/scope.py, A3).

NO DB — these exercise the helper's contract against the ambient ``_scope``
ContextVar directly. ``scoped_sql`` is a predicate BUILDER: it returns
``(predicate_sql, params)`` so the caller can only AND the helper-owned predicate
into its WHERE — it can NEVER hand-write (and thus misplace) the tenant token.

Coverage:
  * no scope (None ambient) → ``UnscopedQueryError`` (fail-closed: a raw read on
    a scoped table with no identity is forbidden);
  * USER scope with a falsy user_id (None / "") → raise (a user scope with no
    identity would bind NULL → open the IS NULL branch → full-table leak);
  * USER scope → binds ``scope.user_id`` AS-IS under ``SCOPE_USER_PARAM``;
  * a legitimate 0 / bigint user_id is NOT rejected by the falsy check;
  * SYSTEM scope → binds ``None`` (the ``IS NULL`` branch opens the full table);
  * the returned predicate is the FIXED helper-built shape (no tautology);
  * returns a NEW dict — the caller's params is never mutated.

These run with no enforcement flag and no engine: ``scoped_sql`` reads the
ambient scope only, so the flag is irrelevant to it (the flag gates the ORM
choke point, not this helper).
"""

from __future__ import annotations

import pytest

from app.db.scope import (
    SCOPE_USER_PARAM,
    Scope,
    UnscopedQueryError,
    request_scope,
    scoped_sql,
    system_request_scope,
)

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]

# The qualified owner column every caller passes (a hardcoded literal).
_COL = "r.creator_id"
# The exact predicate the builder must emit for that column.
_EXPECTED_PRED = (
    "(CAST(:scope_user_id AS uuid) IS NULL "
    "OR r.creator_id = CAST(:scope_user_id AS uuid))"
)


async def test_no_scope_raises():
    """No ambient scope → fail-closed raise (never a silent full-scan)."""
    with pytest.raises(UnscopedQueryError):
        scoped_sql(_COL, {"x": 1})


async def test_user_scope_falsy_none_raises():
    """USER scope with user_id=None → raise: binding NULL would open the IS NULL
    branch → a full-table cross-tenant read under a user scope (fail #2)."""
    async with request_scope(Scope(user_id=None)):  # type: ignore[arg-type]
        with pytest.raises(UnscopedQueryError):
            scoped_sql(_COL, {"x": 1})


async def test_user_scope_falsy_empty_string_raises():
    """USER scope with user_id="" → same fail-closed raise (fail #2)."""
    async with request_scope(Scope(user_id="")):
        with pytest.raises(UnscopedQueryError):
            scoped_sql(_COL, {"x": 1})


async def test_user_scope_binds_user_id():
    """USER scope → binds ``scope.user_id`` AS-IS under SCOPE_USER_PARAM, and
    returns the fixed predicate shape."""
    async with request_scope(Scope(user_id="user-a")):
        pred, params = scoped_sql(_COL, {"x": 1})
    assert pred == _EXPECTED_PRED
    assert params[SCOPE_USER_PARAM] == "user-a"
    assert params["x"] == 1


async def test_user_scope_zero_user_id_is_allowed():
    """A legitimate ``0`` / bigint owner key is NOT rejected by the falsy guard
    (precise ``is None or == ''`` check, NOT ``not user_id``)."""
    async with request_scope(Scope(user_id=0)):
        pred, params = scoped_sql(_COL)
    assert pred == _EXPECTED_PRED
    assert params[SCOPE_USER_PARAM] == 0


async def test_user_scope_binds_user_id_as_is_for_bigint():
    """The owner key is bound AS-IS — a bigint stays an int (no coercion), the
    uuid-string/bigint dual-key invariant the choke point relies on."""
    async with request_scope(Scope(user_id=123456789)):
        _pred, params = scoped_sql(_COL)
    assert params[SCOPE_USER_PARAM] == 123456789
    assert isinstance(params[SCOPE_USER_PARAM], int)


async def test_system_scope_binds_none():
    """SYSTEM scope → binds None so the IS NULL branch opens the full table
    (deliberate cross-user read). Predicate shape is identical to a USER read."""
    async with system_request_scope(reason="test: scoped_sql system bind"):
        pred, params = scoped_sql(_COL, {"x": 1})
    assert pred == _EXPECTED_PRED
    assert params[SCOPE_USER_PARAM] is None
    assert params["x"] == 1


async def test_predicate_is_fixed_shape_not_a_tautology():
    """The builder OWNS the predicate: the caller cannot produce a tautology
    (``:t = :t`` / ``OR 1=1``). It is always the IS-NULL-OR-equals form against
    the passed column, and it references the column EXACTLY once on each side."""
    async with request_scope(Scope(user_id="user-a")):
        pred, _ = scoped_sql("x.owner")
    assert pred == (
        "(CAST(:scope_user_id AS uuid) IS NULL "
        "OR x.owner = CAST(:scope_user_id AS uuid))"
    )
    assert "1=1" not in pred
    # The bound token appears, and the owner column is on the filtering side.
    assert "x.owner =" in pred


async def test_returns_new_dict_input_not_mutated():
    """Immutable: the returned dict is a fresh copy; the input is untouched."""
    original = {"x": 1}
    async with request_scope(Scope(user_id="user-a")):
        _pred, params = scoped_sql(_COL, original)
    assert params is not original
    assert SCOPE_USER_PARAM not in original, "input params was mutated"
    assert original == {"x": 1}


async def test_none_params_yields_only_tenant_bind():
    """``params=None`` → a new dict carrying just the tenant bind."""
    async with request_scope(Scope(user_id="user-a")):
        _pred, params = scoped_sql(_COL)
    assert params == {SCOPE_USER_PARAM: "user-a"}
