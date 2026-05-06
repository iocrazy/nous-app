"""K4 — Prometheus text-format exporter."""
from __future__ import annotations

import pytest

from app.agent_framework.prometheus_exporter import render_prometheus
from app.agent_framework.telemetry import AgentMetrics, COUNTER_NAMES


@pytest.mark.unit
def test_render_includes_all_canonical_counters():
    m = AgentMetrics()
    body = render_prometheus(m)
    for name in COUNTER_NAMES:
        assert f"agent_{name}" in body


@pytest.mark.unit
def test_render_format_per_counter():
    m = AgentMetrics()
    m.inc("compaction_triggered", by=5)
    body = render_prometheus(m)
    # Three lines per counter: HELP / TYPE / value
    assert "# HELP agent_compaction_triggered" in body
    assert "# TYPE agent_compaction_triggered counter" in body
    assert 'agent_compaction_triggered{namespace="harness"} 5' in body


@pytest.mark.unit
def test_render_zero_for_uninc_counters():
    m = AgentMetrics()
    body = render_prometheus(m)
    assert 'agent_loop_guard_observed{namespace="harness"} 0' in body


@pytest.mark.unit
def test_render_unknown_counters_surfaced():
    """Typo'd counters appear under agent_unknown_<name>."""
    m = AgentMetrics()
    m.inc("typo_name", by=3)
    body = render_prometheus(m)
    assert "agent_unknown_typo_name" in body
    assert 'agent_unknown_typo_name{namespace="harness"} 3' in body


@pytest.mark.unit
def test_render_namespace_label_consistent():
    """Every value line carries namespace='harness' label."""
    m = AgentMetrics()
    m.inc("compaction_triggered")
    body = render_prometheus(m)
    value_lines = [
        ln for ln in body.splitlines()
        if ln and not ln.startswith("#")
    ]
    for ln in value_lines:
        assert 'namespace="harness"' in ln


@pytest.mark.unit
def test_render_ends_with_newline():
    """Prometheus expfmt requires trailing newline."""
    m = AgentMetrics()
    body = render_prometheus(m)
    assert body.endswith("\n")


@pytest.mark.unit
def test_render_canonical_lines_sorted():
    """Output lines for canonical counters appear in sorted order
    (deterministic for diff in CI)."""
    m = AgentMetrics()
    body = render_prometheus(m)
    # Extract just the value lines (3 per counter; pick the value line)
    value_lines = [
        ln.split("{")[0]
        for ln in body.splitlines()
        if ln.startswith("agent_") and not ln.startswith("agent_unknown_")
    ]
    assert value_lines == sorted(value_lines)


@pytest.mark.unit
def test_canonical_counters_all_have_help_text():
    """Every canonical counter SHOULD have a help_text override —
    surface missing entries as test failures so docs stay current."""
    from app.agent_framework.prometheus_exporter import _HELP

    missing = [n for n in COUNTER_NAMES if n not in _HELP]
    assert not missing, f"counters missing help text: {missing}"


@pytest.mark.unit
def test_render_value_int_cast():
    """Even if AgentMetrics carried floats internally, we render ints
    (counters are monotonic integer)."""
    m = AgentMetrics()
    m.counters["compaction_triggered"] = 7  # explicit int
    body = render_prometheus(m)
    assert 'agent_compaction_triggered{namespace="harness"} 7' in body
