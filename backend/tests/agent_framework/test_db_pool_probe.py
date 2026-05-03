"""D10-10 — db_pool_probe classify + log."""
from __future__ import annotations

import logging

import pytest

from app.agent_framework.db_pool_probe import (
    DbPoolCapacityReport,
    _classify,
    log_capacity_report,
)


# ─── _classify ──────────────────────────────────────────────────────


@pytest.mark.unit
def test_classify_green_under_50pct():
    color, advice = _classify(0.30, max_conn=100, cur_conn=30)
    assert color == "green"
    assert "30" in advice
    assert "100" in advice


@pytest.mark.unit
def test_classify_yellow_50_to_75pct():
    color, advice = _classify(0.60, max_conn=100, cur_conn=60)
    assert color == "yellow"
    assert "approaching" in advice


@pytest.mark.unit
def test_classify_red_over_75pct():
    color, advice = _classify(0.80, max_conn=100, cur_conn=80)
    assert color == "red"
    assert "danger" in advice
    assert "raise max_connections" in advice.lower() or "reduce" in advice.lower()


@pytest.mark.unit
def test_classify_boundary_at_50pct_is_yellow():
    color, _ = _classify(0.50, max_conn=100, cur_conn=50)
    assert color == "yellow"


@pytest.mark.unit
def test_classify_boundary_at_75pct_is_red():
    color, _ = _classify(0.75, max_conn=100, cur_conn=75)
    assert color == "red"


# ─── log_capacity_report ───────────────────────────────────────────


@pytest.mark.unit
def test_log_none_does_not_raise(caplog):
    """No probe data → debug log, doesn't raise."""
    log_capacity_report(None)


@pytest.mark.unit
def test_log_red_uses_error_level(caplog):
    report = DbPoolCapacityReport(
        max_connections=100,
        current_connections=85,
        current_pct=0.85,
        color="red",
        advice="DB pool danger: 85/100 (85%) — fix soon",
    )
    with caplog.at_level(logging.ERROR, logger="app.agent_framework.db_pool_probe"):
        log_capacity_report(report)
    assert any("danger" in r.message for r in caplog.records)


@pytest.mark.unit
def test_log_yellow_uses_warning_level(caplog):
    report = DbPoolCapacityReport(
        max_connections=100,
        current_connections=60,
        current_pct=0.60,
        color="yellow",
        advice="approaching capacity",
    )
    with caplog.at_level(logging.WARNING, logger="app.agent_framework.db_pool_probe"):
        log_capacity_report(report)
    assert any("approaching" in r.message for r in caplog.records)


@pytest.mark.unit
def test_log_green_uses_info_level(caplog):
    report = DbPoolCapacityReport(
        max_connections=100,
        current_connections=10,
        current_pct=0.10,
        color="green",
        advice="OK: 10/100",
    )
    with caplog.at_level(logging.INFO, logger="app.agent_framework.db_pool_probe"):
        log_capacity_report(report)
    assert any("OK" in r.message for r in caplog.records)
