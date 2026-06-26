"""Tests for the resource_fetch channel team-scope param (CHAT-SEC-AGENT-03).

Verifies that:
  (a) When team_id is set, a resource whose scope_id != team_id is rejected
      (PermissionError) even if the summoner's membership would ordinarily
      allow it — the DB query includes the extra team filter.
  (b) When team_id=None, the SQL / params are unchanged — no :tid bind param
      is added — preserving the existing ai_library chat path exactly.
"""

from __future__ import annotations

import pytest

# ---------------------------------------------------------------------------
# (a) With team_id set — resource in wrong scope raises PermissionError
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_fetch_dispatch_team_scope_rejects_wrong_scope(monkeypatch):
    """fetch_all returns [] when scope_id != team_id (as the DB would);
    _fetch_dispatch must raise PermissionError.
    """
    from unittest.mock import AsyncMock

    from app.db import engine as db_engine
    from app.services.ai.tools.resource_fetch_tool import _fetch_dispatch

    monkeypatch.setattr(db_engine, "fetch_all", AsyncMock(return_value=[]))

    with pytest.raises(PermissionError):
        await _fetch_dispatch(
            resource_id="111",
            mode=None,
            args=None,
            user_id="user-abc",
            team_id=999,
        )


# ---------------------------------------------------------------------------
# (b) With team_id set — SQL and params include the :tid team filter
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_fetch_dispatch_team_scope_sql_includes_team_filter(monkeypatch):
    """When team_id is provided, fetch_all must receive SQL containing :tid
    and params must include tid=<team_id>.
    """
    from app.db import engine as db_engine
    from app.services.ai.tools.resource_fetch_tool import _fetch_dispatch

    captured_sql: list[str] = []
    captured_params: list[dict] = []

    async def _spy(sql: str, params: dict | None = None) -> list[dict]:
        captured_sql.append(sql)
        captured_params.append(params or {})
        return []  # empty → PermissionError, which we expect

    monkeypatch.setattr(db_engine, "fetch_all", _spy)

    with pytest.raises(PermissionError):
        await _fetch_dispatch(
            resource_id="222",
            mode=None,
            args=None,
            user_id="user-abc",
            team_id=42,
        )

    assert captured_sql, "fetch_all was not called"
    assert (
        ":tid" in captured_sql[0]
    ), "SQL must contain :tid bind param when team_id is set"
    assert (
        captured_params[0].get("tid") == 42
    ), "params must contain tid=42 when team_id=42"
    # Verify the membership subquery is still present (not replaced by team filter)
    assert (
        "user_id=:uid" in captured_sql[0]
    ), "SQL must contain user_id=:uid membership check"
    assert (
        "team_members" in captured_sql[0]
    ), "SQL must contain team_members subquery for membership validation"


# ---------------------------------------------------------------------------
# (c) With team_id=None — SQL/params unchanged (no :tid added)
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_fetch_dispatch_no_team_filter_when_team_id_none(monkeypatch):
    """When team_id=None, fetch_all must NOT receive :tid — preserving exact
    current behaviour for the existing ai_library chat path.
    """
    from app.db import engine as db_engine
    from app.services.ai.tools.resource_fetch_tool import _fetch_dispatch

    captured_sql: list[str] = []
    captured_params: list[dict] = []

    async def _spy(sql: str, params: dict | None = None) -> list[dict]:
        captured_sql.append(sql)
        captured_params.append(params or {})
        return []  # empty → PermissionError, which we expect

    monkeypatch.setattr(db_engine, "fetch_all", _spy)

    with pytest.raises(PermissionError):
        await _fetch_dispatch(
            resource_id="333",
            mode=None,
            args=None,
            user_id="user-abc",
            team_id=None,
        )

    assert captured_sql, "fetch_all was not called"
    assert ":tid" not in captured_sql[0], "SQL must NOT contain :tid when team_id=None"
    assert (
        "tid" not in captured_params[0]
    ), "params must NOT contain 'tid' key when team_id=None"


# ---------------------------------------------------------------------------
# (d) resource_fetch public API threads team_id down to _fetch_dispatch
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_resource_fetch_threads_team_id_to_dispatch(monkeypatch):
    """resource_fetch must accept team_id and forward it to _fetch_dispatch.
    When the dispatch raises PermissionError the public wrapper converts it
    to {"error": "resource not accessible"} (existing catch block).
    """
    from app.services.ai.tools import resource_fetch_tool as m

    captured_team_ids: list[int | None] = []

    async def _fake_dispatch(*, resource_id, mode, args, user_id, team_id=None):
        captured_team_ids.append(team_id)
        raise PermissionError("no access")

    monkeypatch.setattr(m, "_fetch_dispatch", _fake_dispatch)

    result = await m.resource_fetch(
        resource_id="444",
        mode=None,
        args=None,
        user_id="user-abc",
        available_refs={"444"},
        request_cache={},
        team_id=77,
    )

    assert result == {"error": "resource not accessible"}
    assert captured_team_ids == [
        77
    ], "resource_fetch must pass team_id=77 through to _fetch_dispatch"


# ---------------------------------------------------------------------------
# (e) team_id=None via resource_fetch — back-compat for ai_library callers
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_resource_fetch_team_id_defaults_to_none(monkeypatch):
    """Existing callers that don't pass team_id must still work — the arg is
    optional and defaults to None, and _fetch_dispatch receives None.
    """
    from app.services.ai.tools import resource_fetch_tool as m

    captured_team_ids: list[int | None] = []

    async def _fake_dispatch(*, resource_id, mode, args, user_id, team_id=None):
        captured_team_ids.append(team_id)
        raise PermissionError("no access")

    monkeypatch.setattr(m, "_fetch_dispatch", _fake_dispatch)

    # Deliberately omit team_id to test back-compat
    result = await m.resource_fetch(
        resource_id="555",
        mode=None,
        args=None,
        user_id="user-abc",
        available_refs={"555"},
        request_cache={},
    )

    assert result == {"error": "resource not accessible"}
    assert captured_team_ids == [None], "team_id must default to None when not supplied"
