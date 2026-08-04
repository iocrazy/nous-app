"""Unit tests for the migration-period raw-SQL guardrail (``app/db/scoped_sql.py``).

NO DB — ``db_engine.fetch_all`` / ``fetch_one`` / ``execute`` are monkeypatched,
so these exercise only the declare-before-you-query contract:

  * neither ``scope=`` nor ``system=True`` → ``UnscopedRawSQLError``;
  * both ``scope=`` and ``system=True`` → ``UnscopedRawSQLError`` (exactly one);
  * ``system=True`` with an empty/missing ``reason`` → raise;
  * ``system=True, reason="..."`` → forwards to the matching db_engine helper,
    untouched params;
  * ``scope=Scope(...)`` with no matching ``SCOPE_USER_PARAM`` bind in params →
    raise (declaring a scope you don't actually filter by is exactly the gap
    this guardrail exists to close);
  * ``scope=Scope(...)`` whose bound value disagrees with ``scope.user_id`` →
    raise;
  * ``scope=Scope(...)`` with a matching bind but the SQL text never
    references the bind token → raise (an unused bind is not a filter —
    catches "declared a scope, built the params, forgot to reference it in
    the query string");
  * ``scope=Scope(...)`` with a matching bind AND the SQL text referencing
    it → forwards, params unchanged;
  * mode dispatch (fetch_all / fetch_one / execute) calls the right db_engine
    helper;
  * the three mode-specific wrappers (scoped_fetch_all/one/execute) thread
    through to the same validation + dispatch.

This module is distinct from ``app/db/scope.py::scoped_sql`` (the sync
predicate BUILDER covered by ``tests/db/test_scoped_sql.py``) — see the
module docstring cross-reference. Do not confuse the two in test names.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app.db import scoped_sql as guardrail
from app.db.scope import SCOPE_USER_PARAM, Scope
from app.db.scoped_sql import (
    UnscopedRawSQLError,
    scoped_execute,
    scoped_fetch_all,
    scoped_fetch_one,
    scoped_sql,
)

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]

_SQL = "SELECT 1"
# A SQL string that actually references the bind token — required for the
# scope= "success" tests now that _assert_scope_bound also checks the SQL
# text mentions SCOPE_USER_PARAM (see app/db/scoped_sql.py::_assert_scope_bound).
_SQL_SCOPED = f"SELECT * FROM t WHERE owner = :{SCOPE_USER_PARAM}"


# ── Declaration contract ─────────────────────────────────────────────────


async def test_neither_scope_nor_system_raises():
    with pytest.raises(UnscopedRawSQLError):
        await scoped_sql(_SQL, {})


async def test_both_scope_and_system_raises(monkeypatch):
    monkeypatch.setattr(guardrail.db_engine, "fetch_all", AsyncMock(return_value=[]))
    with pytest.raises(UnscopedRawSQLError):
        await scoped_sql(
            _SQL,
            {SCOPE_USER_PARAM: "u1"},
            scope=Scope(user_id="u1"),
            system=True,
            reason="oops",
        )


async def test_system_true_without_reason_raises(monkeypatch):
    monkeypatch.setattr(guardrail.db_engine, "fetch_all", AsyncMock(return_value=[]))
    with pytest.raises(UnscopedRawSQLError):
        await scoped_sql(_SQL, {}, system=True, reason="")


async def test_system_true_whitespace_reason_raises(monkeypatch):
    monkeypatch.setattr(guardrail.db_engine, "fetch_all", AsyncMock(return_value=[]))
    with pytest.raises(UnscopedRawSQLError):
        await scoped_sql(_SQL, {}, system=True, reason="   ")


# ── system=True path ─────────────────────────────────────────────────────


async def test_system_true_with_reason_forwards_to_fetch_all(monkeypatch):
    fake = AsyncMock(return_value=[{"a": 1}])
    monkeypatch.setattr(guardrail.db_engine, "fetch_all", fake)
    result = await scoped_sql(_SQL, {"x": 1}, system=True, reason="gc sweep")
    assert result == [{"a": 1}]
    fake.assert_awaited_once_with(_SQL, {"x": 1})


async def test_system_true_does_not_mutate_input_params(monkeypatch):
    monkeypatch.setattr(guardrail.db_engine, "fetch_all", AsyncMock(return_value=[]))
    original = {"x": 1}
    await scoped_sql(_SQL, original, system=True, reason="probe")
    assert original == {"x": 1}


async def test_system_true_none_params_forwards_empty_dict(monkeypatch):
    fake = AsyncMock(return_value=None)
    monkeypatch.setattr(guardrail.db_engine, "fetch_one", fake)
    await scoped_sql(_SQL, None, system=True, reason="probe", mode="fetch_one")
    fake.assert_awaited_once_with(_SQL, {})


# ── scope= path ───────────────────────────────────────────────────────────


async def test_scope_without_bound_param_raises(monkeypatch):
    monkeypatch.setattr(guardrail.db_engine, "fetch_all", AsyncMock(return_value=[]))
    with pytest.raises(UnscopedRawSQLError):
        await scoped_sql(_SQL, {"other": 1}, scope=Scope(user_id="u1"))


async def test_scope_with_mismatched_bound_param_raises(monkeypatch):
    monkeypatch.setattr(guardrail.db_engine, "fetch_all", AsyncMock(return_value=[]))
    with pytest.raises(UnscopedRawSQLError):
        await scoped_sql(
            _SQL, {SCOPE_USER_PARAM: "someone-else"}, scope=Scope(user_id="u1")
        )


async def test_scope_with_bound_param_but_sql_never_references_token_raises(
    monkeypatch,
):
    """params carries the bind but the SQL text never mentions it — an
    unused bind is not a filter; this is exactly the gap the SQL-text
    substring check exists to catch (M1 review fix)."""
    monkeypatch.setattr(guardrail.db_engine, "fetch_all", AsyncMock(return_value=[]))
    with pytest.raises(UnscopedRawSQLError):
        await scoped_sql(
            _SQL,  # "SELECT 1" — no SCOPE_USER_PARAM substring anywhere
            {SCOPE_USER_PARAM: "u1"},
            scope=Scope(user_id="u1"),
        )


async def test_scope_with_matching_bound_param_forwards(monkeypatch):
    fake = AsyncMock(return_value=[{"id": 1}])
    monkeypatch.setattr(guardrail.db_engine, "fetch_all", fake)
    params = {SCOPE_USER_PARAM: "u1", "q": "x"}
    result = await scoped_sql(_SQL_SCOPED, params, scope=Scope(user_id="u1"))
    assert result == [{"id": 1}]
    fake.assert_awaited_once_with(_SQL_SCOPED, {SCOPE_USER_PARAM: "u1", "q": "x"})


async def test_scope_zero_user_id_matches_bound_zero(monkeypatch):
    """A legitimate bigint 0 owner key is not mistaken for 'no bind'."""
    fake = AsyncMock(return_value=[])
    monkeypatch.setattr(guardrail.db_engine, "fetch_all", fake)
    await scoped_sql(_SQL_SCOPED, {SCOPE_USER_PARAM: 0}, scope=Scope(user_id=0))
    fake.assert_awaited_once()


# ── mode dispatch ─────────────────────────────────────────────────────────


async def test_mode_fetch_one_calls_fetch_one(monkeypatch):
    fake = AsyncMock(return_value={"id": 1})
    monkeypatch.setattr(guardrail.db_engine, "fetch_one", fake)
    result = await scoped_sql(_SQL, {}, system=True, reason="x", mode="fetch_one")
    assert result == {"id": 1}
    fake.assert_awaited_once()


async def test_mode_execute_calls_execute(monkeypatch):
    fake = AsyncMock(return_value=3)
    monkeypatch.setattr(guardrail.db_engine, "execute", fake)
    result = await scoped_sql(_SQL, {}, system=True, reason="x", mode="execute")
    assert result == 3
    fake.assert_awaited_once()


async def test_unknown_mode_raises_value_error(monkeypatch):
    monkeypatch.setattr(guardrail.db_engine, "fetch_all", AsyncMock(return_value=[]))
    with pytest.raises(ValueError):
        await scoped_sql(_SQL, {}, system=True, reason="x", mode="bogus")  # type: ignore[arg-type]


# ── mode-specific wrappers ─────────────────────────────────────────────────


async def test_scoped_fetch_all_wrapper(monkeypatch):
    fake = AsyncMock(return_value=[{"a": 1}])
    monkeypatch.setattr(guardrail.db_engine, "fetch_all", fake)
    result = await scoped_fetch_all(_SQL, {}, system=True, reason="x")
    assert result == [{"a": 1}]


async def test_scoped_fetch_one_wrapper(monkeypatch):
    fake = AsyncMock(return_value=None)
    monkeypatch.setattr(guardrail.db_engine, "fetch_one", fake)
    result = await scoped_fetch_one(_SQL, {}, system=True, reason="x")
    assert result is None


async def test_scoped_execute_wrapper(monkeypatch):
    fake = AsyncMock(return_value=0)
    monkeypatch.setattr(guardrail.db_engine, "execute", fake)
    result = await scoped_execute(_SQL, {}, system=True, reason="x")
    assert result == 0


async def test_scoped_fetch_all_wrapper_requires_declaration():
    with pytest.raises(UnscopedRawSQLError):
        await scoped_fetch_all(_SQL, {})
