"""Admin connection panel: aggregate shape and the failure rendering.

The panel exists so an operator can answer "who is holding the connections?"
during an incident without shelling into the database — which is precisely
the moment ``psql`` may itself be unable to connect (at 106/100 on
2026-08-21, it could not).

The handler is called directly rather than through the ASGI app: the admin
dependency is an authorisation concern covered by its own tests, and driving
it here would only add a fixture that could pass for the wrong reason.
"""

from __future__ import annotations

import pytest

from app.api.admin.monitoring_router import get_db_connections

_AUTH = object()  # stands in for AdminAuthDep; the handler never reads it


def _patch(monkeypatch, summary, groups=None):
    async def _summary():
        return summary

    async def _groups(limit=50):
        return groups or []

    monkeypatch.setattr(
        "app.services.infra.pg_connection_monitor.sample_connection_usage", _summary
    )
    monkeypatch.setattr(
        "app.services.infra.pg_connection_monitor.fetch_connection_breakdown", _groups
    )


async def test_panel_returns_summary_and_grouped_breakdown(monkeypatch):
    """The shape the UI renders: totals, the ceiling, and who is holding what."""
    _patch(
        monkeypatch,
        {
            "status": "warning",
            "used": 165,
            "max_connections": 200,
            "percent": 82.5,
            "idle_in_transaction": 2,
            "oldest_idle_in_transaction_seconds": 340,
        },
        [
            {
                "application_name": "dbos_transact",
                "usename": "postgres",
                "state": "idle",
                "count": 7,
                "oldest_state_seconds": 120,
            },
            {
                "application_name": "<none>",
                "usename": "supabase_admin",
                "state": "idle",
                "count": 41,
                "oldest_state_seconds": 9000,
            },
        ],
    )

    result = await get_db_connections(_AUTH)

    assert result.status == "warning"
    assert result.used == 165
    assert result.max_connections == 200
    assert result.percent == 82.5
    assert result.idle_in_transaction == 2
    assert result.oldest_idle_in_transaction_seconds == 340
    assert len(result.groups) == 2
    assert result.groups[0].application_name == "dbos_transact"
    assert result.groups[1].count == 41


async def test_panel_ships_the_thresholds_it_was_classified_with(monkeypatch):
    """The UI must not hardcode 80/95.

    If the panel carried its own copy of the thresholds, changing them in
    ``pg_connection_monitor`` would leave the badge disagreeing with the log
    line about what counts as high — two monitors telling different stories
    about one number.
    """
    from app.services.infra import pg_connection_monitor as mon

    _patch(monkeypatch, {"status": "ok", "used": 10, "max_connections": 200})

    result = await get_db_connections(_AUTH)

    assert result.warn_pct == mon.WARN_PCT
    assert result.critical_pct == mon.CRITICAL_PCT


async def test_an_unreadable_database_renders_as_unknown_with_no_groups(monkeypatch):
    """Failure must not be dressed up as an empty-but-healthy database.

    ``used=0 of max=0`` would render as a 0% badge — a green light produced by
    a broken probe, the exact failure the ``not_probed`` bucket was introduced
    to eliminate elsewhere in this codebase.
    """
    from app.services.infra.pg_connection_monitor import unknown_sample

    called = False

    async def _groups_must_not_run(limit=50):
        nonlocal called
        called = True
        return [{"application_name": "x"}]

    async def _summary():
        return unknown_sample("too many clients already")

    monkeypatch.setattr(
        "app.services.infra.pg_connection_monitor.sample_connection_usage", _summary
    )
    monkeypatch.setattr(
        "app.services.infra.pg_connection_monitor.fetch_connection_breakdown",
        _groups_must_not_run,
    )

    result = await get_db_connections(_AUTH)

    assert result.status == "unknown"
    assert result.used is None
    assert result.percent is None
    assert result.reason == "too many clients already"
    assert result.groups == []
    # The GROUP BY is skipped rather than attempted-and-swallowed: it would
    # fail the same way, and one log line about it is enough.
    assert called is False


@pytest.mark.parametrize("status", ["ok", "warning", "critical"])
async def test_every_healthy_path_still_fetches_the_breakdown(monkeypatch, status):
    """Only ``unknown`` short-circuits — a ``critical`` reading is exactly when
    the operator most needs to see who is holding the connections."""
    _patch(
        monkeypatch,
        {"status": status, "used": 190, "max_connections": 200, "percent": 95.0},
        [
            {
                "application_name": "PostgREST 14.8",
                "usename": "authenticator",
                "state": "idle",
                "count": 5,
                "oldest_state_seconds": 30,
            }
        ],
    )

    result = await get_db_connections(_AUTH)

    assert len(result.groups) == 1
