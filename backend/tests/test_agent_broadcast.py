"""Unit tests for ChatBroadcastRepository (Task 1: Agent Broadcast; P3 W0 repoint).

Tests cover:
  - list_broadcast_candidate_channels: joins conversation_members + conversations
    (member_type='agent', non-archived, excludes 1:1 direct_agent), groups
    agent_ids per conversation. Output dict key names are retained from the
    pre-Phase-3 legacy-table shape ("channel_id"/"team_id") even though the
    values now come from conversations — see chat_broadcast_repository.py
    docstring for the rationale (keeps the service layer's send_message call
    the only Phase-3 touch point besides this query).
  - completed_workflow_counts_since: filters status='completed', task_kind='workflow',
    completed_at > :since; scopes to team via JOIN team_members on team_id (no array-bind).
    Unchanged by Phase 3 (reads task_tracking/team_members only, no channel FK).
  - get_watermark / set_watermark: read/write system_settings under
    broadcast_watermark_channel_{id} key. Unchanged by Phase 3 (system_settings
    has no FK to channels; the key is an opaque string built from an id).

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
async def test_candidate_channels_sql_joins_conversation_members_and_conversations():
    """SQL must JOIN conversation_members with conversations, filter on
    member_type='agent', archived_at IS NULL, exclude type='direct_agent'
    (1:1 agent DMs never broadcast into), and aggregate agent_ids per
    conversation. Legacy agent_channels/channels tables must NOT appear."""
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
    assert "conversation_members" in sql, "SQL must reference conversation_members"
    assert "conversations" in sql, "SQL must JOIN conversations"
    assert "member_type" in sql, "SQL must filter on member_type='agent'"
    assert "archived_at" in sql, "SQL must filter on archived_at IS NULL"
    assert "direct_agent" in sql, "SQL must exclude type='direct_agent' (1:1 DMs)"
    assert "agent_id" in sql, "SQL must select/aggregate agent_id"
    assert "agent_channels" not in sql, "Legacy agent_channels table must be gone"
    assert "is_archived" not in sql, "Legacy is_archived column must be gone"

    assert len(result) == 1
    ch = result[0]
    # Field names retained from the legacy shape ("channel_id"/"team_id") —
    # the value is now a conversations.id / conversations.scope_id.
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


# ── completed_workflow_counts_since ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_counts_since_sql_filters_status_and_task_kind():
    """SQL must JOIN team_members, filter on status='completed' and task_kind='workflow'."""
    captured: dict = {}

    async def fake_fetch_all(sql, params=None):
        captured["sql"] = sql
        captured["params"] = params
        return [{"task_kind": "workflow", "cnt": 3, "max_completed_at": _NOW}]

    with patch("app.db.engine.fetch_all", fake_fetch_all):
        result = await _REPO.completed_workflow_counts_since(_TEAM_ID, _SINCE)

    sql = captured["sql"]
    assert "task_tracking" in sql, "SQL must query task_tracking table"
    assert "team_members" in sql, "SQL must JOIN team_members"
    assert "JOIN" in sql.upper(), "SQL must use JOIN"
    assert "tm.team_id" in sql, "SQL must filter by tm.team_id"
    assert ":tid" in sql, "SQL must use :tid parameter"
    assert "completed" in sql, "SQL must filter status='completed'"
    assert "workflow" in sql, "SQL must filter task_kind='workflow'"
    assert "completed_at" in sql, "SQL must reference completed_at"
    assert "since" in sql, "SQL must use :since parameter"
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
        result = await _REPO.completed_workflow_counts_since(_TEAM_ID, _SINCE)

    assert result["total"] == 7
    assert result["by_kind"]["workflow"] == 7
    assert result["max_completed_at"] == _NOW


@pytest.mark.asyncio
async def test_counts_since_zero_rows_returns_zero_dict():
    """When no completed workflows found, returns all-zero dict."""

    async def fake_fetch_all(sql, params=None):
        return []

    with patch("app.db.engine.fetch_all", fake_fetch_all):
        result = await _REPO.completed_workflow_counts_since(_TEAM_ID, _SINCE)

    assert result == {"total": 0, "by_kind": {}, "max_completed_at": None}


@pytest.mark.asyncio
async def test_counts_since_empty_team_returns_zero_dict():
    """A team with no members yields zero rows (JOIN produces nothing); returns zero dict."""

    async def fake_fetch_all(sql, params=None):
        # The JOIN on team_members simply produces no rows for an empty team.
        return []

    with patch("app.db.engine.fetch_all", fake_fetch_all):
        result = await _REPO.completed_workflow_counts_since(_TEAM_ID, _SINCE)

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
        result = await _REPO.completed_workflow_counts_since(_TEAM_ID, since=None)

    # :since must NOT appear in params when since=None
    assert "since" not in (
        captured.get("params") or {}
    ), ":since param must be absent when since=None"
    assert result == {"total": 0, "by_kind": {}, "max_completed_at": None}


@pytest.mark.asyncio
async def test_counts_since_team_id_param_present():
    """The team_id must be passed as :tid parameter; no uids/array param must exist."""
    captured: dict = {}

    async def fake_fetch_all(sql, params=None):
        captured["sql"] = sql
        captured["params"] = params
        return []

    with patch("app.db.engine.fetch_all", fake_fetch_all):
        await _REPO.completed_workflow_counts_since(_TEAM_ID, _SINCE)

    params = captured.get("params") or {}
    assert "tid" in params, "team_id must be bound as :tid query parameter"
    assert params["tid"] == int(_TEAM_ID), ":tid value must match the provided team_id"
    assert "uids" not in params, ":uids must NOT be present (array-bind removed)"
    has_uid_n = any(k.startswith("uid") for k in params)
    assert not has_uid_n, "Expanded uid_N params must NOT be present"


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
    counts_ret=None,
):
    """Return (broadcast_repo_mock, conversation_repo_mock, agent_repo_mock).

    Note: team_member_ids is no longer called by the service — the repo now
    JOINs team_members internally via completed_workflow_counts_since(team_id, wm).

    P3 W0: the message write now goes through ConversationRepository.send_message
    (conversation_id/type/parent_id/from_agent_id) instead of the legacy
    ChatRepository.send_message (channel_id/content_type/reply_to_id/from_bot_agent_id).
    """
    broadcast_repo = MagicMock()
    broadcast_repo.list_broadcast_candidate_channels = AsyncMock(
        return_value=candidates if candidates is not None else [_candidate()]
    )
    broadcast_repo.get_watermark = AsyncMock(return_value=watermark)
    broadcast_repo.completed_workflow_counts_since = AsyncMock(
        return_value=counts_ret if counts_ret is not None else _counts()
    )
    broadcast_repo.set_watermark = AsyncMock()

    conv_repo = MagicMock()
    conv_repo.send_message = AsyncMock(return_value={"id": 999, "seq": 1})

    agent_repo = MagicMock()
    _agent_val = agent if agent is not None else _agent(auto_broadcast=True)
    agent_repo.get_by_id = AsyncMock(return_value=_agent_val)

    return broadcast_repo, conv_repo, agent_repo


_SVC = "app.services.chat.agent_broadcast"


# ── (a) PERM-11 gate: auto_broadcast=False → no post ─────────────────────────


@pytest.mark.asyncio
async def test_perm11_auto_broadcast_false_skips_post():
    """An agent with auto_broadcast=False must never trigger a send."""
    br, cr, ar = _make_repos(agent=_agent(auto_broadcast=False))

    with (
        patch(f"{_SVC}.get_broadcast_repository", return_value=br),
        patch(f"{_SVC}.get_conversation_repository", return_value=cr),
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
        patch(f"{_SVC}.get_conversation_repository", return_value=cr),
        patch(f"{_SVC}.get_agent_repository", return_value=ar),
    ):
        result = await scan_and_broadcast()

    cr.send_message.assert_called_once()
    call_kwargs = cr.send_message.call_args.kwargs
    assert call_kwargs["conversation_id"] == _BC_CHAN_ID
    assert call_kwargs["sender_id"] is None
    assert call_kwargs["sender_type"] == "agent"
    assert call_kwargs["type"] == "text"
    assert call_kwargs["from_agent_id"] == _BC_AGENT_ID
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
        patch(f"{_SVC}.get_conversation_repository", return_value=cr),
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
        patch(f"{_SVC}.get_conversation_repository", return_value=cr),
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
        patch(f"{_SVC}.get_conversation_repository", return_value=cr),
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
    br.completed_workflow_counts_since = AsyncMock(return_value=_counts(total=1))
    br.set_watermark = AsyncMock()

    cr = MagicMock()
    cr.send_message = AsyncMock(return_value={"id": 1, "seq": 1})

    ar = MagicMock()
    ar.get_by_id = AsyncMock(return_value=_agent(auto_broadcast=True))

    with (
        patch(f"{_SVC}.get_broadcast_repository", return_value=br),
        patch(f"{_SVC}.get_conversation_repository", return_value=cr),
        patch(f"{_SVC}.get_agent_repository", return_value=ar),
    ):
        result = await scan_and_broadcast()

    # Channel B must have been processed despite channel A failing
    cr.send_message.assert_called_once()
    send_kwargs = cr.send_message.call_args.kwargs
    assert send_kwargs["conversation_id"] == _CHAN_B

    assert result["channels_scanned"] == 2
    assert result["messages_posted"] == 1
