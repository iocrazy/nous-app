"""Tests for RoutedAiStore — the FEATURE_DIRECT_CONVERSATIONS dispatcher.

Covers:
  * off  → every op hits legacy only, new store never called;
  * on   → fresh create hits the new store; per-session ops route by
           existence-probe (dual-serving: an id only legacy knows about
           still routes legacy even in 'on' mode); the owner resolution
           is cached per session_id (no re-probe on a second op);
  * shadow → create returns the LEGACY result while the new store is
           mirrored best-effort (mirror failures swallowed, never raise);
           a forced mismatch between the legacy/mirror rows logs
           `[p2-shadow] MISMATCH`;
  * unknown mode values warn once and behave as 'off';
  * list_sessions in 'on' mode merges + sorts + truncates across both
    stores — including the THE critical regression case: legacy rows carry
    ISO strings, new-store rows carry tz-aware datetimes, and the merge
    must not raise on the mixed types;
  * list_sessions in 'shadow' mode logs counts only (never a field-by-field
    MISMATCH — the two lists are unrelated collections, not the same rows);
  * the owner cache key is normalized to `int` so a session created in 'on'
    mode is still found when later looked up by a `str` session_id (as the
    API layer passes it).

No DB needed — both stores are AsyncMocks; the loguru sink pattern mirrors
``tests/test_shadow_compare.py``.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock

import pytest

from app.services.ai.chat import store_router
from app.services.ai.chat.store_router import RoutedAiStore


@pytest.fixture
def shadow_logs(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Capture every record emitted by store_router's logger."""
    captured: list[dict[str, Any]] = []

    def _sink(message: Any) -> None:
        rec = message.record
        captured.append({"level": rec["level"].name, "message": str(rec["message"])})

    handler_id = store_router.logger.add(_sink, level="INFO")
    yield captured
    store_router.logger.remove(handler_id)


def _make_stores() -> tuple[AsyncMock, AsyncMock]:
    legacy = AsyncMock()
    new = AsyncMock()
    return legacy, new


def _set_mode(monkeypatch: pytest.MonkeyPatch, mode: str) -> None:
    from app.core.config import settings

    monkeypatch.setattr(settings, "FEATURE_DIRECT_CONVERSATIONS", mode)


# ---------------------------------------------------------------------------
# off — legacy only
# ---------------------------------------------------------------------------


async def test_off_create_hits_legacy_only(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_mode(monkeypatch, "off")
    legacy, new = _make_stores()
    legacy.create_session.return_value = {"id": 1, "title": "hi", "status": "active"}
    router = RoutedAiStore(legacy=legacy, new=new)

    row = await router.create_session(
        user_id="u1",
        agent_slug="a",
        agent_id="agent-1",
        title="hi",
        project_id=None,
        team_id=None,
        context_type=None,
        context_id=None,
    )

    assert row == {"id": 1, "title": "hi", "status": "active"}
    legacy.create_session.assert_awaited_once()
    new.create_session.assert_not_awaited()


async def test_off_get_and_append_hit_legacy_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_mode(monkeypatch, "off")
    legacy, new = _make_stores()
    legacy.get_session.return_value = {"id": 5, "title": "t", "status": "active"}
    legacy.append_user_message.return_value = {"id": 99, "role": "user"}
    router = RoutedAiStore(legacy=legacy, new=new)

    session = await router.get_session(session_id=5)
    msg = await router.append_user_message(session_id=5, user_id="u1", content="hey")

    assert session == {"id": 5, "title": "t", "status": "active"}
    assert msg == {"id": 99, "role": "user"}
    new.get_session.assert_not_awaited()
    new.append_user_message.assert_not_awaited()


# ---------------------------------------------------------------------------
# on — new store for fresh creates; dual-serving by existence-probe
# ---------------------------------------------------------------------------


async def test_on_create_hits_new_store(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_mode(monkeypatch, "on")
    legacy, new = _make_stores()
    new.create_session.return_value = {"id": 42, "title": "hi", "status": "active"}
    router = RoutedAiStore(legacy=legacy, new=new)

    row = await router.create_session(
        user_id="u1",
        agent_slug="a",
        agent_id="agent-1",
        title="hi",
        project_id=None,
        team_id=None,
        context_type=None,
        context_id=None,
    )

    assert row == {"id": 42, "title": "hi", "status": "active"}
    new.create_session.assert_awaited_once()
    legacy.create_session.assert_not_awaited()


async def test_get_routes_by_existence_probe_and_caches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An id legacy owns routes to legacy; an id legacy doesn't know about
    routes to the new store — and the second op on the SAME id does not
    re-probe legacy (cached)."""
    _set_mode(monkeypatch, "on")
    legacy, new = _make_stores()
    legacy.get_session.side_effect = lambda session_id: (
        {"id": 1, "title": "legacy-row"} if session_id == 1 else None
    )
    new.get_session.return_value = {"id": 2, "title": "new-row"}
    router = RoutedAiStore(legacy=legacy, new=new)

    legacy_row = await router.get_session(session_id=1)
    new_row = await router.get_session(session_id=2)

    assert legacy_row == {"id": 1, "title": "legacy-row"}
    assert new_row == {"id": 2, "title": "new-row"}
    assert legacy.get_session.await_count == 2  # both probed once

    # Second op on session_id=2 (new store) must NOT re-probe legacy.
    await router.rename_session(session_id=2, title="renamed")
    assert legacy.get_session.await_count == 2  # unchanged — cache hit
    new.rename_session.assert_awaited_once_with(session_id=2, title="renamed")

    # Second op on session_id=1 (legacy) also must not re-probe.
    await router.rename_session(session_id=1, title="renamed-legacy")
    assert legacy.get_session.await_count == 2  # still unchanged


async def test_on_create_then_get_by_str_id_uses_normalized_cache_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fix 5 regression: create_session seeds the owner cache with an int id
    (whatever the store returns); the API layer looks sessions up by `str`
    session_id. Without normalizing both sides to `int`, this is a
    guaranteed cache miss that falls through to a needless (and, in 'on'
    mode, semantically wrong) legacy probe."""
    _set_mode(monkeypatch, "on")
    legacy, new = _make_stores()
    new.create_session.return_value = {"id": 42, "title": "hi", "status": "active"}
    new.get_session.return_value = {"id": 42, "title": "hi", "status": "active"}
    router = RoutedAiStore(legacy=legacy, new=new)

    created = await router.create_session(
        user_id="u1",
        agent_slug="a",
        agent_id="agent-1",
        title="hi",
        project_id=None,
        team_id=None,
        context_type=None,
        context_id=None,
    )
    assert created["id"] == 42

    fetched = await router.get_session(session_id=str(created["id"]))

    assert fetched == {"id": 42, "title": "hi", "status": "active"}
    new.get_session.assert_awaited_once_with(session_id="42")
    legacy.get_session.assert_not_awaited()


# ---------------------------------------------------------------------------
# shadow — legacy authoritative + best-effort mirror + diff
# ---------------------------------------------------------------------------


async def test_shadow_create_returns_legacy_result_and_mirrors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_mode(monkeypatch, "shadow")
    legacy, new = _make_stores()
    legacy.create_session.return_value = {"id": 1, "title": "hi", "status": "active"}
    new.create_session.return_value = {"id": 999, "title": "hi", "status": "active"}
    router = RoutedAiStore(legacy=legacy, new=new)

    row = await router.create_session(
        user_id="u1",
        agent_slug="a",
        agent_id="agent-1",
        title="hi",
        project_id=None,
        team_id=None,
        context_type=None,
        context_id=None,
    )

    # Caller gets the LEGACY result.
    assert row == {"id": 1, "title": "hi", "status": "active"}
    legacy.create_session.assert_awaited_once()
    new.create_session.assert_awaited_once()  # mirrored, best-effort


async def test_shadow_mirror_failure_is_swallowed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_mode(monkeypatch, "shadow")
    legacy, new = _make_stores()
    legacy.create_session.return_value = {"id": 1, "title": "hi", "status": "active"}
    new.create_session.side_effect = Exception("mirror boom")
    router = RoutedAiStore(legacy=legacy, new=new)

    row = await router.create_session(
        user_id="u1",
        agent_slug="a",
        agent_id="agent-1",
        title="hi",
        project_id=None,
        team_id=None,
        context_type=None,
        context_id=None,
    )

    # Must NOT raise — legacy result still returned.
    assert row == {"id": 1, "title": "hi", "status": "active"}


async def test_shadow_mismatch_is_logged(
    monkeypatch: pytest.MonkeyPatch, shadow_logs: list[dict[str, Any]]
) -> None:
    _set_mode(monkeypatch, "shadow")
    legacy, new = _make_stores()
    legacy.create_session.return_value = {"id": 1, "title": "hi", "status": "active"}
    new.create_session.return_value = {
        "id": 999,
        "title": "DIFFERENT",
        "status": "active",
    }
    router = RoutedAiStore(legacy=legacy, new=new)

    await router.create_session(
        user_id="u1",
        agent_slug="a",
        agent_id="agent-1",
        title="hi",
        project_id=None,
        team_id=None,
        context_type=None,
        context_id=None,
    )

    mismatches = [r for r in shadow_logs if "MISMATCH" in r["message"]]
    assert len(mismatches) == 1
    assert "title" in mismatches[0]["message"]
    # entity (Fix 3) identifies WHICH session mismatched.
    assert "entity=1" in mismatches[0]["message"]


# ---------------------------------------------------------------------------
# unknown mode → warn once + behave as off
# ---------------------------------------------------------------------------


async def test_unknown_mode_warns_and_behaves_as_off(
    monkeypatch: pytest.MonkeyPatch, shadow_logs: list[dict[str, Any]]
) -> None:
    # Fix 7's throttle is a module-level "already warned" set — clear it so
    # this test doesn't depend on whether some earlier test in the same
    # process already warned about this exact bad value.
    monkeypatch.setattr(store_router, "_warned_unknown_modes", set())
    _set_mode(monkeypatch, "  Bogus  ")
    legacy, new = _make_stores()
    legacy.create_session.return_value = {"id": 1, "title": "hi", "status": "active"}
    router = RoutedAiStore(legacy=legacy, new=new)

    row = await router.create_session(
        user_id="u1",
        agent_slug="a",
        agent_id="agent-1",
        title="hi",
        project_id=None,
        team_id=None,
        context_type=None,
        context_id=None,
    )

    assert row == {"id": 1, "title": "hi", "status": "active"}
    new.create_session.assert_not_awaited()
    warnings = [
        r for r in shadow_logs if "unknown FEATURE_DIRECT_CONVERSATIONS" in r["message"]
    ]
    assert len(warnings) == 1


async def test_shadow_list_sessions_logs_counts_not_mismatch(
    monkeypatch: pytest.MonkeyPatch, shadow_logs: list[dict[str, Any]]
) -> None:
    """Fix 4 regression: the two lists are unrelated collections (not the
    "same" rows in different stores, unlike create_session's mirror), so
    diffing e.g. their first rows would be a guaranteed false positive.
    list_sessions' shadow branch must log counts only — never a field
    MISMATCH — and the counts line must carry the user entity."""
    _set_mode(monkeypatch, "shadow")
    legacy, new = _make_stores()
    legacy.list_sessions.return_value = [
        {"id": 1, "title": "legacy-only", "updated_at": "2026-01-01"}
    ]
    new.list_sessions.return_value = [
        {"id": 999, "title": "totally-unrelated", "updated_at": "2026-01-02"}
    ]
    router = RoutedAiStore(legacy=legacy, new=new)

    rows = await router.list_sessions(
        user_id="u1", agent_slug=None, project_id=None, limit=20
    )

    # Legacy stays authoritative for the caller-visible result.
    assert rows == legacy.list_sessions.return_value

    mismatches = [r for r in shadow_logs if "MISMATCH" in r["message"]]
    assert mismatches == []

    counts = [r for r in shadow_logs if "list_sessions counts" in r["message"]]
    assert len(counts) == 1
    assert "legacy=1" in counts[0]["message"]
    assert "mirror=1" in counts[0]["message"]
    assert "entity=user:u1" in counts[0]["message"]


# ---------------------------------------------------------------------------
# list_sessions
# ---------------------------------------------------------------------------


async def test_off_list_sessions_legacy_only(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_mode(monkeypatch, "off")
    legacy, new = _make_stores()
    legacy.list_sessions.return_value = [{"id": 1, "updated_at": "2026-01-01"}]
    router = RoutedAiStore(legacy=legacy, new=new)

    rows = await router.list_sessions(
        user_id="u1", agent_slug=None, project_id=None, limit=20
    )

    assert rows == [{"id": 1, "updated_at": "2026-01-01"}]
    new.list_sessions.assert_not_awaited()


async def test_on_list_sessions_merges_sorts_and_truncates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_mode(monkeypatch, "on")
    legacy, new = _make_stores()
    legacy.list_sessions.return_value = [
        {"id": 1, "updated_at": "2026-01-01T00:00:00"},
        {"id": 2, "updated_at": "2026-01-03T00:00:00"},
    ]
    new.list_sessions.return_value = [
        {"id": 101, "updated_at": "2026-01-05T00:00:00"},
        {"id": 102, "updated_at": "2026-01-02T00:00:00"},
    ]
    router = RoutedAiStore(legacy=legacy, new=new)

    rows = await router.list_sessions(
        user_id="u1", agent_slug=None, project_id=None, limit=3
    )

    assert [r["id"] for r in rows] == [101, 2, 102]
    legacy.list_sessions.assert_awaited_once()
    new.list_sessions.assert_awaited_once()


async def test_on_list_sessions_merges_heterogeneous_timestamp_types(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """THE critical regression test (Fix 1): legacy rows carry ISO strings
    (PostgREST JSON — one with a trailing 'Z', one naive), new-store rows
    carry tz-aware `datetime` objects (asyncpg). Sorting the merged list by
    raw `row.get("updated_at")` crashes with `TypeError: '<' not supported
    between instances of 'str' and 'datetime.datetime'` (and naive vs. aware
    datetimes ALSO raise). This must not raise, and must interleave both
    stores in correct newest-first order."""
    _set_mode(monkeypatch, "on")
    legacy, new = _make_stores()
    legacy.list_sessions.return_value = [
        {"id": 1, "updated_at": "2026-01-01T00:00:00Z"},
        {"id": 2, "updated_at": "2026-01-03T00:00:00"},
    ]
    new.list_sessions.return_value = [
        {"id": 101, "updated_at": datetime(2026, 1, 5, tzinfo=timezone.utc)},
        {"id": 102, "updated_at": datetime(2026, 1, 2, tzinfo=timezone.utc)},
    ]
    router = RoutedAiStore(legacy=legacy, new=new)

    rows = await router.list_sessions(
        user_id="u1", agent_slug=None, project_id=None, limit=10
    )

    assert [r["id"] for r in rows] == [101, 2, 102, 1]
