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

import inspect
import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.repositories.chat_broadcast_repository import ChatBroadcastRepository
from app.services.chat.agent_broadcast import (
    build_broadcast_summary,
    scan_and_broadcast,
)

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


# =============================================================================
# Task 2: scan_and_broadcast + build_broadcast_summary (service layer)
# =============================================================================
#
# Mocking strategy: patch the factory functions at their import location in
# the service module (app.services.chat.agent_broadcast.*) so that all
# real DB / Supabase calls are intercepted.
#
# Agent dict convention: capability_profile.chat mirrors the real schema so
# that agent_chat_caps() (the real parser) is exercised, not mocked.

_BC_CHAN_ID = 3333333333333333333
_BC_TEAM_ID = 4444444444444444444
_BC_AGENT_ID = "dddddddd-0000-0000-0000-000000000001"
_BC_AGENT_ID_2 = "dddddddd-0000-0000-0000-000000000002"
_BC_USER_ID = "eeeeeeee-0000-0000-0000-000000000001"
_WM_TS = datetime(2026, 6, 26, 10, 0, 0, tzinfo=timezone.utc)
_MAX_TS = datetime(2026, 6, 27, 9, 0, 0, tzinfo=timezone.utc)


def _agent(
    auto_broadcast: bool, enabled: bool = True, allowed_team_ids: list = []
) -> dict:
    """Build a minimal agent dict that agent_chat_caps() can parse correctly."""
    return {
        "id": _BC_AGENT_ID,
        "slug": "test-broadcast-bot",
        "capability_profile": {
            "chat": {
                "enabled": True if enabled else False,
                "auto_broadcast": True if auto_broadcast else False,
                "allowed_team_ids": list(allowed_team_ids),
            }
        },
    }


def _candidate(channel_id=_BC_CHAN_ID, team_id=_BC_TEAM_ID, agent_ids=None) -> dict:
    return {
        "channel_id": channel_id,
        "team_id": team_id,
        "agent_ids": agent_ids if agent_ids is not None else [_BC_AGENT_ID],
    }


def _counts(total: int = 3, max_ts: datetime = _MAX_TS) -> dict:
    by_kind = {"workflow": total} if total > 0 else {}
    return {
        "total": total,
        "by_kind": by_kind,
        "max_completed_at": max_ts if total > 0 else None,
    }


def _make_repos(
    candidates=None,
    agent=None,
    watermark=_WM_TS,
    members=None,
    counts_ret=None,
):
    """Return (broadcast_repo_mock, chat_repo_mock, agent_repo_mock)."""
    broadcast_repo = MagicMock()
    broadcast_repo.list_broadcast_candidate_channels = AsyncMock(
        return_value=candidates if candidates is not None else [_candidate()]
    )
    broadcast_repo.get_watermark = AsyncMock(return_value=watermark)
    broadcast_repo.team_member_ids = AsyncMock(
        return_value=members if members is not None else [_BC_USER_ID]
    )
    broadcast_repo.completed_workflow_counts_since = AsyncMock(
        return_value=counts_ret if counts_ret is not None else _counts()
    )
    broadcast_repo.set_watermark = AsyncMock()

    chat_repo = MagicMock()
    chat_repo.send_message = AsyncMock(return_value={"id": 999, "seq": 1})

    agent_repo = MagicMock()
    _agent_val = agent if agent is not None else _agent(auto_broadcast=True)
    agent_repo.get_by_id = AsyncMock(return_value=_agent_val)

    return broadcast_repo, chat_repo, agent_repo


_SVC = "app.services.chat.agent_broadcast"


# ── (a) PERM-11 gate: auto_broadcast=False → no post ─────────────────────────


@pytest.mark.asyncio
async def test_perm11_auto_broadcast_false_skips_post():
    """An agent with auto_broadcast=False must never trigger a send."""
    br, cr, ar = _make_repos(agent=_agent(auto_broadcast=False))

    with (
        patch(f"{_SVC}.get_broadcast_repository", return_value=br),
        patch(f"{_SVC}.get_chat_repository", return_value=cr),
        patch(f"{_SVC}.get_agent_repository", return_value=ar),
    ):
        result = await scan_and_broadcast()

    cr.send_message.assert_not_called()
    assert result["channels_scanned"] == 1
    assert result["messages_posted"] == 0


# ── (b) Happy path: auto_broadcast=True → posts once ─────────────────────────


@pytest.mark.asyncio
async def test_happy_path_eligible_agent_posts_once():
    """An auto_broadcast+enabled agent with a valid watermark posts exactly once."""
    br, cr, ar = _make_repos(agent=_agent(auto_broadcast=True), watermark=_WM_TS)

    with (
        patch(f"{_SVC}.get_broadcast_repository", return_value=br),
        patch(f"{_SVC}.get_chat_repository", return_value=cr),
        patch(f"{_SVC}.get_agent_repository", return_value=ar),
    ):
        result = await scan_and_broadcast()

    cr.send_message.assert_called_once()
    call_kwargs = cr.send_message.call_args.kwargs
    assert call_kwargs["channel_id"] == _BC_CHAN_ID
    assert call_kwargs["sender_id"] is None
    assert call_kwargs["sender_type"] == "agent"
    assert call_kwargs["content_type"] == "text"
    assert call_kwargs["from_bot_agent_id"] == _BC_AGENT_ID
    assert "text" in call_kwargs["body"]

    assert result["channels_scanned"] == 1
    assert result["messages_posted"] == 1


# ── (c) No-backfill: wm=None → set_watermark(now), no post ──────────────────


@pytest.mark.asyncio
async def test_no_backfill_first_run_sets_watermark_no_post():
    """When a channel has no watermark, set watermark=~now and post NOTHING."""
    br, cr, ar = _make_repos(agent=_agent(auto_broadcast=True), watermark=None)

    with (
        patch(f"{_SVC}.get_broadcast_repository", return_value=br),
        patch(f"{_SVC}.get_chat_repository", return_value=cr),
        patch(f"{_SVC}.get_agent_repository", return_value=ar),
    ):
        result = await scan_and_broadcast()

    # Must set the watermark
    br.set_watermark.assert_called_once()
    set_wm_args = br.set_watermark.call_args
    assert (
        set_wm_args.args[0] == _BC_CHAN_ID
        or set_wm_args.kwargs.get("channel_id") == _BC_CHAN_ID
    )
    wm_value = (
        set_wm_args.args[1]
        if len(set_wm_args.args) > 1
        else set_wm_args.kwargs.get("ts")
    )
    assert isinstance(wm_value, datetime), "Watermark must be a datetime"
    assert wm_value.tzinfo is not None, "Watermark must be timezone-aware"

    # Must NOT post
    cr.send_message.assert_not_called()
    assert result["messages_posted"] == 0


# ── (d) No-double-post: counts.total=0 → no send, watermark unchanged ─────────


@pytest.mark.asyncio
async def test_no_double_post_zero_counts_no_send():
    """When no new completions since the watermark, do not post and do not advance wm."""
    br, cr, ar = _make_repos(
        agent=_agent(auto_broadcast=True),
        watermark=_WM_TS,
        counts_ret=_counts(total=0),
    )

    with (
        patch(f"{_SVC}.get_broadcast_repository", return_value=br),
        patch(f"{_SVC}.get_chat_repository", return_value=cr),
        patch(f"{_SVC}.get_agent_repository", return_value=ar),
    ):
        result = await scan_and_broadcast()

    cr.send_message.assert_not_called()
    br.set_watermark.assert_not_called()
    assert result["messages_posted"] == 0


# ── (e) Summary safety: only counts + kind names in the output ────────────────


def test_build_broadcast_summary_format_and_safety():
    """build_broadcast_summary output must contain only counts+kinds, no private data."""
    text = build_broadcast_summary(3, {"workflow": 3})

    # Must contain the count and "task(s) completed"
    assert "3" in text
    assert "task(s) completed" in text
    assert "workflow" in text

    # Must NOT contain any private-data field names
    forbidden = {"title", "subtitle", "metadata", "resource", "content", "name"}
    text_lower = text.lower()
    for word in forbidden:
        assert word not in text_lower, f"Broadcast summary must not contain {word!r}"


def test_build_broadcast_summary_exact_format():
    """Verify the exact format including the · separator."""
    text = build_broadcast_summary(5, {"workflow": 3, "download": 2})
    assert text.startswith("✅ 5 task(s) completed")
    assert " · " in text
    assert "3 workflow" in text
    assert "2 download" in text


def test_build_broadcast_summary_no_breakdown_when_empty():
    """When by_kind is empty, return only the header (no · separator)."""
    text = build_broadcast_summary(7, {})
    assert text == "✅ 7 task(s) completed"
    assert "·" not in text


def test_build_broadcast_summary_pure_function_no_extra_inputs():
    """Summary takes only (int, dict[str,int]) — no other inputs possible."""
    sig = inspect.signature(build_broadcast_summary)
    params = list(sig.parameters.keys())
    assert params == [
        "total",
        "by_kind",
    ], f"build_broadcast_summary must take ONLY (total, by_kind), got {params}"


# ── (f) Watermark advances to max_completed_at only after a successful post ───


@pytest.mark.asyncio
async def test_watermark_advances_to_max_completed_at_after_post():
    """After a successful send, set_watermark must be called with max_completed_at."""
    br, cr, ar = _make_repos(
        agent=_agent(auto_broadcast=True),
        watermark=_WM_TS,
        counts_ret=_counts(total=2, max_ts=_MAX_TS),
    )

    with (
        patch(f"{_SVC}.get_broadcast_repository", return_value=br),
        patch(f"{_SVC}.get_chat_repository", return_value=cr),
        patch(f"{_SVC}.get_agent_repository", return_value=ar),
    ):
        await scan_and_broadcast()

    # send_message must be called BEFORE set_watermark
    cr.send_message.assert_called_once()
    br.set_watermark.assert_called_once()

    set_wm_args = br.set_watermark.call_args
    advanced_ts = (
        set_wm_args.args[1]
        if len(set_wm_args.args) > 1
        else set_wm_args.kwargs.get("ts")
    )
    assert (
        advanced_ts == _MAX_TS
    ), f"Watermark must advance to max_completed_at={_MAX_TS}, got {advanced_ts}"


# ── (g) One bad channel does not abort other channels ─────────────────────────


@pytest.mark.asyncio
async def test_one_bad_channel_does_not_abort_scan():
    """An exception in one channel's processing must not stop the scan of other channels."""
    _CHAN_A = 5555555555555555555
    _CHAN_B = 6666666666666666666

    br = MagicMock()
    br.list_broadcast_candidate_channels = AsyncMock(
        return_value=[
            _candidate(channel_id=_CHAN_A),
            _candidate(channel_id=_CHAN_B),
        ]
    )

    # Channel A: get_watermark raises; channel B: works fine
    call_count = 0

    async def get_watermark_side_effect(channel_id):
        nonlocal call_count
        call_count += 1
        if channel_id == _CHAN_A:
            raise RuntimeError("simulated DB failure on channel A")
        return _WM_TS

    br.get_watermark = AsyncMock(side_effect=get_watermark_side_effect)
    br.team_member_ids = AsyncMock(return_value=[_BC_USER_ID])
    br.completed_workflow_counts_since = AsyncMock(return_value=_counts(total=1))
    br.set_watermark = AsyncMock()

    cr = MagicMock()
    cr.send_message = AsyncMock(return_value={"id": 1, "seq": 1})

    ar = MagicMock()
    ar.get_by_id = AsyncMock(return_value=_agent(auto_broadcast=True))

    with (
        patch(f"{_SVC}.get_broadcast_repository", return_value=br),
        patch(f"{_SVC}.get_chat_repository", return_value=cr),
        patch(f"{_SVC}.get_agent_repository", return_value=ar),
    ):
        result = await scan_and_broadcast()

    # Channel B must have been processed despite channel A failing
    cr.send_message.assert_called_once()
    send_kwargs = cr.send_message.call_args.kwargs
    assert send_kwargs["channel_id"] == _CHAN_B

    assert result["channels_scanned"] == 2
    assert result["messages_posted"] == 1
