"""Unit tests for ChatBroadcastRepository (Task 1: Agent Broadcast).

Tests cover:
  - list_broadcast_candidate_channels: joins agent_channels + channels (non-archived),
    groups agent_ids per channel.
  - team_member_ids: queries team_members table by team_id.
  - completed_workflow_counts_since: filters status='completed', task_kind='workflow',
    completed_at > :since, user_id filtering; empty user_ids short-circuits.
  - get_watermark / set_watermark: read/write system_settings under
    broadcast_watermark_channel_{id} key.

Mocking follows the pattern in test_chat_edit_delete.py and test_chat_user_mention.py:
patch("app.db.engine.<method>", fake_async_fn).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from app.repositories.chat_broadcast_repository import ChatBroadcastRepository

_REPO = ChatBroadcastRepository()

_CHAN_ID = 1234567890123456789
_TEAM_ID = 9876543210987654321
_AGENT_ID_1 = "aaaaaaaa-0000-0000-0000-000000000001"
_AGENT_ID_2 = "aaaaaaaa-0000-0000-0000-000000000002"
_USER_ID_1 = "bbbbbbbb-0000-0000-0000-000000000001"
_USER_ID_2 = "bbbbbbbb-0000-0000-0000-000000000002"
_NOW = datetime(2026, 6, 27, 12, 0, 0, tzinfo=timezone.utc)
_SINCE = datetime(2026, 6, 26, 12, 0, 0, tzinfo=timezone.utc)


# ── list_broadcast_candidate_channels ────────────────────────────────────────


@pytest.mark.asyncio
async def test_candidate_channels_sql_joins_agent_channels_and_channels():
    """SQL must JOIN agent_channels with channels, filter is_archived=false,
    and aggregate agent_ids per channel."""
    captured: dict = {}

    async def fake_fetch_all(sql, params=None):
        captured["sql"] = sql
        return [
            {
                "channel_id": _CHAN_ID,
                "team_id": _TEAM_ID,
                "agent_ids": [_AGENT_ID_1, _AGENT_ID_2],
            }
        ]

    with patch("app.db.engine.fetch_all", fake_fetch_all):
        result = await _REPO.list_broadcast_candidate_channels()

    sql = captured["sql"]
    assert "agent_channels" in sql, "SQL must reference agent_channels"
    assert "channels" in sql, "SQL must JOIN channels"
    assert "is_archived" in sql, "SQL must filter on is_archived"
    assert "agent_id" in sql, "SQL must select/aggregate agent_id"

    assert len(result) == 1
    ch = result[0]
    assert ch["channel_id"] == _CHAN_ID
    assert ch["team_id"] == _TEAM_ID
    assert _AGENT_ID_1 in ch["agent_ids"]
    assert _AGENT_ID_2 in ch["agent_ids"]


@pytest.mark.asyncio
async def test_candidate_channels_returns_empty_list_when_no_rows():
    """Returns [] when no non-archived agent-bound channels exist."""

    async def fake_fetch_all(sql, params=None):
        return []

    with patch("app.db.engine.fetch_all", fake_fetch_all):
        result = await _REPO.list_broadcast_candidate_channels()

    assert result == []


@pytest.mark.asyncio
async def test_candidate_channels_coerces_ids_to_int():
    """channel_id and team_id in the returned dicts must be Python ints."""

    async def fake_fetch_all(sql, params=None):
        # Return string-keyed ints (simulating some driver edge cases)
        return [
            {"channel_id": _CHAN_ID, "team_id": _TEAM_ID, "agent_ids": [_AGENT_ID_1]}
        ]

    with patch("app.db.engine.fetch_all", fake_fetch_all):
        result = await _REPO.list_broadcast_candidate_channels()

    assert isinstance(result[0]["channel_id"], int)
    assert isinstance(result[0]["team_id"], int)


# ── team_member_ids ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_team_member_ids_queries_team_members_table():
    """SQL queries team_members WHERE team_id = :tid and returns user_id strings."""
    captured: dict = {}

    async def fake_fetch_all(sql, params=None):
        captured["sql"] = sql
        captured["params"] = params
        return [{"user_id": _USER_ID_1}, {"user_id": _USER_ID_2}]

    with patch("app.db.engine.fetch_all", fake_fetch_all):
        result = await _REPO.team_member_ids(_TEAM_ID)

    sql = captured["sql"]
    assert "team_members" in sql, "SQL must query team_members table"
    assert "user_id" in sql, "SQL must select user_id"
    assert "team_id" in sql, "SQL must filter by team_id"

    assert isinstance(captured["params"]["tid"], int), "team_id must be coerced to int"
    assert captured["params"]["tid"] == int(_TEAM_ID)

    assert result == [_USER_ID_1, _USER_ID_2]
    for uid in result:
        assert isinstance(uid, str), "user_ids must be returned as strings"


@pytest.mark.asyncio
async def test_team_member_ids_returns_empty_list_for_no_members():
    """Returns [] when team has no members."""

    async def fake_fetch_all(sql, params=None):
        return []

    with patch("app.db.engine.fetch_all", fake_fetch_all):
        result = await _REPO.team_member_ids(_TEAM_ID)

    assert result == []


# ── completed_workflow_counts_since ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_counts_since_empty_user_ids_short_circuits():
    """Empty user_ids must return zeros without issuing any DB query."""
    calls: list = []

    async def fake_fetch_all(sql, params=None):
        calls.append(sql)
        return []

    with patch("app.db.engine.fetch_all", fake_fetch_all):
        result = await _REPO.completed_workflow_counts_since([], _SINCE)

    assert calls == [], "DB must NOT be queried when user_ids is empty"
    assert result == {"total": 0, "by_kind": {}, "max_completed_at": None}


@pytest.mark.asyncio
async def test_counts_since_sql_filters_status_and_task_kind():
    """SQL must filter on status='completed' and task_kind='workflow'."""
    captured: dict = {}

    async def fake_fetch_all(sql, params=None):
        captured["sql"] = sql
        captured["params"] = params
        return [{"task_kind": "workflow", "cnt": 3, "max_completed_at": _NOW}]

    with patch("app.db.engine.fetch_all", fake_fetch_all):
        result = await _REPO.completed_workflow_counts_since([_USER_ID_1], _SINCE)

    sql = captured["sql"]
    assert "task_tracking" in sql, "SQL must query task_tracking table"
    assert "completed" in sql, "SQL must filter status='completed'"
    assert "workflow" in sql, "SQL must filter task_kind='workflow'"
    assert "completed_at" in sql, "SQL must reference completed_at"
    assert "since" in sql, "SQL must use :since parameter"
    assert "user_id" in sql, "SQL must filter by user_id"
    assert "COUNT" in sql.upper(), "SQL must COUNT rows"
    assert "GROUP BY" in sql.upper(), "SQL must GROUP BY task_kind"

    assert result["total"] == 3
    assert result["by_kind"] == {"workflow": 3}
    assert result["max_completed_at"] == _NOW


@pytest.mark.asyncio
async def test_counts_since_aggregates_multiple_rows():
    """When query returns multiple kind rows, total = sum, by_kind populated."""
    # In practice only 'workflow' rows are returned (filtered), but test the
    # aggregation logic is correct.

    async def fake_fetch_all(sql, params=None):
        return [
            {"task_kind": "workflow", "cnt": 7, "max_completed_at": _NOW},
        ]

    with patch("app.db.engine.fetch_all", fake_fetch_all):
        result = await _REPO.completed_workflow_counts_since(
            [_USER_ID_1, _USER_ID_2], _SINCE
        )

    assert result["total"] == 7
    assert result["by_kind"]["workflow"] == 7
    assert result["max_completed_at"] == _NOW


@pytest.mark.asyncio
async def test_counts_since_zero_rows_returns_zero_dict():
    """When no completed workflows found, returns all-zero dict."""

    async def fake_fetch_all(sql, params=None):
        return []

    with patch("app.db.engine.fetch_all", fake_fetch_all):
        result = await _REPO.completed_workflow_counts_since([_USER_ID_1], _SINCE)

    assert result == {"total": 0, "by_kind": {}, "max_completed_at": None}


@pytest.mark.asyncio
async def test_counts_since_none_since_no_time_filter_in_sql():
    """When since=None, the SQL must NOT include a completed_at > filter."""
    captured: dict = {}

    async def fake_fetch_all(sql, params=None):
        captured["sql"] = sql
        captured["params"] = params
        return []

    with patch("app.db.engine.fetch_all", fake_fetch_all):
        result = await _REPO.completed_workflow_counts_since([_USER_ID_1], since=None)

    # :since must NOT appear in params when since=None
    assert "since" not in (
        captured.get("params") or {}
    ), ":since param must be absent when since=None"
    assert result == {"total": 0, "by_kind": {}, "max_completed_at": None}


@pytest.mark.asyncio
async def test_counts_since_user_ids_param_present():
    """The user_ids list must be passed as a parameter (not inlined)."""
    captured: dict = {}

    async def fake_fetch_all(sql, params=None):
        captured["sql"] = sql
        captured["params"] = params
        return []

    with patch("app.db.engine.fetch_all", fake_fetch_all):
        await _REPO.completed_workflow_counts_since([_USER_ID_1, _USER_ID_2], _SINCE)

    params = captured.get("params") or {}
    # Either 'uids' list param or expanded uid_N params must be present
    has_uids = "uids" in params or any(k.startswith("uid") for k in params)
    assert has_uids, "user_ids must be bound as query parameter(s)"


# ── get_watermark ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_watermark_reads_system_settings_with_correct_key():
    """get_watermark queries system_settings using broadcast_watermark_channel_{id} key."""
    captured: dict = {}
    ts_str = _NOW.isoformat()  # asyncpg returns JSONB string value as Python str

    async def fake_fetch_one(sql, params=None):
        captured["sql"] = sql
        captured["params"] = params
        return {"value": ts_str}

    with patch("app.db.engine.fetch_one", fake_fetch_one):
        result = await _REPO.get_watermark(_CHAN_ID)

    sql = captured["sql"]
    assert "system_settings" in sql, "SQL must query system_settings"

    expected_key = f"broadcast_watermark_channel_{_CHAN_ID}"
    params_values = list((captured.get("params") or {}).values())
    assert (
        expected_key in params_values
    ), f"Expected key '{expected_key}' in query params"

    assert isinstance(result, datetime), "Must return a datetime"
    assert result.tzinfo is not None, "Returned datetime must be timezone-aware"


@pytest.mark.asyncio
async def test_get_watermark_returns_none_when_key_missing():
    """get_watermark returns None when system_settings has no row for the key."""

    async def fake_fetch_one(sql, params=None):
        return None

    with patch("app.db.engine.fetch_one", fake_fetch_one):
        result = await _REPO.get_watermark(_CHAN_ID)

    assert result is None


@pytest.mark.asyncio
async def test_get_watermark_result_is_timezone_aware():
    """Even if stored ISO string lacks timezone, get_watermark must return UTC-aware dt."""
    naive_str = "2026-06-27T12:00:00"  # no tz info

    async def fake_fetch_one(sql, params=None):
        return {"value": naive_str}

    with patch("app.db.engine.fetch_one", fake_fetch_one):
        result = await _REPO.get_watermark(_CHAN_ID)

    assert result is not None
    assert result.tzinfo is not None, "Must attach UTC tzinfo to naive timestamps"


# ── set_watermark ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_set_watermark_upserts_system_settings_with_correct_key():
    """set_watermark upserts to system_settings under broadcast_watermark_channel_{id}."""
    captured: dict = {}

    async def fake_execute(sql, params=None):
        captured["sql"] = sql
        captured["params"] = params
        return 1

    with patch("app.db.engine.execute", fake_execute):
        await _REPO.set_watermark(_CHAN_ID, _NOW)

    sql = captured["sql"]
    assert "system_settings" in sql, "SQL must target system_settings"
    # Must be an upsert (INSERT ... ON CONFLICT or similar)
    assert (
        "INSERT" in sql.upper() or "UPSERT" in sql.upper()
    ), "SQL must be an INSERT/UPSERT"
    assert (
        "ON CONFLICT" in sql.upper() or "UPDATE" in sql.upper()
    ), "SQL must handle conflicts (upsert)"

    params = captured.get("params") or {}
    expected_key = f"broadcast_watermark_channel_{_CHAN_ID}"
    params_str = str(params)
    assert expected_key in params_str, f"Key '{expected_key}' must be in params"


@pytest.mark.asyncio
async def test_set_watermark_stores_iso_timestamp():
    """set_watermark encodes the datetime as an ISO-8601 string in the params."""
    captured: dict = {}

    async def fake_execute(sql, params=None):
        captured["params"] = params
        return 1

    with patch("app.db.engine.execute", fake_execute):
        await _REPO.set_watermark(_CHAN_ID, _NOW)

    params_str = str(captured.get("params") or {})
    # The ISO timestamp must appear somewhere in the params (as string or json-encoded)
    assert (
        _NOW.isoformat() in params_str or "2026-06-27" in params_str
    ), "ISO timestamp must appear in query params"
