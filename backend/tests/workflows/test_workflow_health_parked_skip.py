"""FH3 T6 upstream stop-bleed: the zombie reaper's 6h AGE backstop must not
kill an ``execute_issue`` that is legitimately parked on ``await_user_input``
(the gate waits NEEDS_INPUT_RECV_TTL_HOURS = 72h). All five zombie locks in
production on 2026-09-25 were made by exactly that kill: a bare UPDATE to
CANCELLED runs no ``finally``, so the lock and the marker stayed forever.

The VERSION-orphan branch is untouched: a parked workflow of an old version
has no executor and startup ``reap_stale_input_waits`` releases it (lock too).
"""

from __future__ import annotations

import datetime as dt
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.dialects import postgresql

import app.workflows.workflow_health_sweeper as hs

pytestmark = pytest.mark.unit

SIX_H_PLUS = 6 * 3600 + 60


def _body(fn):
    while hasattr(fn, "__wrapped__"):
        fn = fn.__wrapped__
    return fn


def _wf(uuid, *, version="v2", age=SIX_H_PLUS):
    return {
        "workflow_uuid": uuid,
        "queue_name": "issue_dispatch",
        "name": "execute_issue",
        "application_version": version,
        "status": "PENDING",
        "age_s": age,
    }


@pytest.fixture
def reaper(monkeypatch):
    """Run the step body against ``rows`` with ``parked`` as the lookup's
    answer; returns (result, cancelled ids, parked lookups)."""
    import app.db.engine as db_engine
    import app.workflows.sweep_guard as guard

    monkeypatch.setattr(db_engine, "is_configured", lambda: True)
    monkeypatch.setattr(guard, "within_boot_grace", lambda: False)
    monkeypatch.setattr(hs, "_resolve_pinned_app_version", lambda: "v2")
    monkeypatch.delenv("DBOS_ZOMBIE_MAX_AGE_SECONDS", raising=False)

    async def _run(rows, parked):
        cancelled: list[str] = []
        lookups: list[list[str]] = []

        async def _cancel(wid):
            cancelled.append(wid)
            return True

        async def _parked(ids):
            lookups.append(sorted(ids))
            if isinstance(parked, Exception):
                raise parked
            return frozenset(parked)

        monkeypatch.setattr(db_engine, "fetch_all", AsyncMock(return_value=rows))
        monkeypatch.setattr(hs, "_cancel_dbos_zombie", _cancel)
        monkeypatch.setattr(hs, "_parked_issue_workflow_ids", _parked)
        out = await _body(hs.reap_dbos_zombies_step)()
        return out, cancelled, lookups

    return _run


async def test_age_backstop_spares_a_legitimately_parked_issue(reaper):
    out, cancelled, _ = await reaper([_wf("issue-1-aaa")], {"issue-1-aaa"})
    assert cancelled == []
    assert out["parked_skipped"] == 1
    assert out["zombies_cancelled"] == 0


async def test_version_orphan_is_still_cancelled_even_when_parked(reaper):
    rows = [_wf("issue-1-aaa", version="v1")]
    out, cancelled, lookups = await reaper(rows, {"issue-1-aaa"})
    assert cancelled == ["issue-1-aaa"]
    assert out["parked_skipped"] == 0
    # the version branch never needs the parked lookup
    assert lookups == []


async def test_age_orphan_that_is_not_parked_is_cancelled(reaper):
    out, cancelled, lookups = await reaper([_wf("issue-2-bbb")], set())
    assert cancelled == ["issue-2-bbb"]
    assert lookups == [["issue-2-bbb"]]


async def test_parked_lookup_failure_fails_closed_for_age_candidates_only(reaper):
    rows = [_wf("issue-1-aaa"), _wf("issue-3-ccc", version="v1")]
    out, cancelled, _ = await reaper(rows, RuntimeError("db down"))
    # the version orphan does not depend on the lookup; the age one waits a tick
    assert cancelled == ["issue-3-ccc"]
    assert out["parked_skipped"] == 1


async def test_log_line_carries_parked_skipped(reaper, monkeypatch):
    seen: list[str] = []
    monkeypatch.setattr(hs.logger, "warning", lambda m, *a, **k: seen.append(m))
    rows = [_wf("issue-1-aaa"), _wf("issue-2-bbb")]
    await reaper(rows, {"issue-1-aaa"})
    assert any("reaped 1 DBOS zombie" in m and "parked_skipped=1" in m for m in seen)


async def test_a_tick_that_only_spares_stays_quiet(reaper, monkeypatch):
    """A parked issue is spared every tick for up to 73h: that must not become
    an INFO/WARN line every two minutes. DEBUG still says so."""
    loud: list[str] = []
    debug: list[str] = []
    monkeypatch.setattr(hs.logger, "info", lambda m, *a, **k: loud.append(m))
    monkeypatch.setattr(hs.logger, "warning", lambda m, *a, **k: loud.append(m))
    monkeypatch.setattr(hs.logger, "debug", lambda m, *a, **k: debug.append(m))
    await reaper([_wf("issue-1-aaa")], {"issue-1-aaa"})
    assert loud == []
    assert any("parked_skipped=1" in m for m in debug), debug


# ── the lookup's SQL ──────────────────────────────────────────────────────


def test_parked_lookup_statement_is_the_parked_predicate_by_workflow_id():
    from app.repositories.agent_run_inbox_repository import (
        parked_workflow_ids_stmt,
    )

    floor = dt.datetime(2026, 9, 22, tzinfo=dt.timezone.utc)
    stmt = parked_workflow_ids_stmt(["issue-1-aaa"], floor)
    compiled = stmt.compile(dialect=postgresql.dialect())
    sql = " ".join(str(compiled).split())
    assert sql.startswith("SELECT public.issues.dbos_workflow_id FROM public.issues")
    assert "public.issues.dbos_workflow_id IN" in sql
    assert "public.issues.execution_locked_at IS NOT NULL" in sql
    assert "->> %(param_1)s) IS NULL" in sql  # answered_at unanswered
    assert "AS TIMESTAMP WITH TIME ZONE" in sql and "> %(param_4)s" in sql
    params = compiled.params
    assert params["execution_state_1"] == "awaiting_input"
    assert (params["param_1"], params["param_2"]) == ("answered_at", "since")
    assert params["param_4"] == floor
    assert params["dbos_workflow_id_1"] == ["issue-1-aaa"]


def test_parked_floor_is_ttl_plus_the_same_slack_as_the_inbox_sweep(monkeypatch):
    from app.core.config import settings
    from app.workflows.agent_runs_sweeper import PARKED_EXPIRY_GRACE_SECONDS

    assert hs.PARKED_WAIT_GRACE_SECONDS == PARKED_EXPIRY_GRACE_SECONDS
    now = dt.datetime(2026, 9, 25, tzinfo=dt.timezone.utc)
    assert hs._parked_floor(now) == now - dt.timedelta(
        hours=settings.NEEDS_INPUT_RECV_TTL_HOURS, seconds=PARKED_EXPIRY_GRACE_SECONDS
    )
