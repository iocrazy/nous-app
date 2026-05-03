"""I3 — AgentMetrics counter store."""
from __future__ import annotations

import pytest

from app.agent_framework.telemetry import COUNTER_NAMES, AgentMetrics


@pytest.mark.unit
def test_all_canonical_counters_initialized_zero():
    m = AgentMetrics()
    snap = m.snapshot()
    for name in COUNTER_NAMES:
        assert snap[name] == 0


@pytest.mark.unit
def test_inc_default_step():
    m = AgentMetrics()
    m.inc("compaction_triggered")
    m.inc("compaction_triggered")
    assert m.get("compaction_triggered") == 2


@pytest.mark.unit
def test_inc_custom_step():
    m = AgentMetrics()
    m.inc("memory_archived", by=10)
    assert m.get("memory_archived") == 10


@pytest.mark.unit
def test_inc_empty_name_silent_noop():
    m = AgentMetrics()
    m.inc("")
    assert "_unknown" not in m.snapshot()


@pytest.mark.unit
def test_unknown_counter_surfaces_in_snapshot():
    """Typo'd counter — gets stored AND surfaced under _unknown so the
    next admin glance catches it (vs silently lost forever)."""
    m = AgentMetrics()
    m.inc("compactionn_typo")  # extra n
    snap = m.snapshot()
    assert "_unknown" in snap
    assert snap["_unknown"]["compactionn_typo"] == 1


@pytest.mark.unit
def test_get_unknown_returns_zero():
    m = AgentMetrics()
    assert m.get("never_incremented") == 0


@pytest.mark.unit
def test_snapshot_sorted_keys():
    m = AgentMetrics()
    snap = m.snapshot()
    keys = [k for k in snap if not k.startswith("_")]
    assert keys == sorted(keys)


@pytest.mark.unit
def test_reset_clears_all_canonical():
    m = AgentMetrics()
    m.inc("compaction_triggered", by=5)
    m.inc("loop_guard_tripped", by=3)
    m.reset()
    assert m.get("compaction_triggered") == 0
    assert m.get("loop_guard_tripped") == 0


@pytest.mark.unit
def test_reset_clears_unknown_too():
    m = AgentMetrics()
    m.inc("typo_counter")
    m.reset()
    snap = m.snapshot()
    assert "_unknown" not in snap


@pytest.mark.unit
def test_canonical_names_have_no_duplicates():
    """Sanity: COUNTER_NAMES tuple has no accidental duplicates."""
    assert len(COUNTER_NAMES) == len(set(COUNTER_NAMES))


@pytest.mark.unit
def test_canonical_names_use_snake_case():
    """Convention: lowercase + underscores only."""
    for name in COUNTER_NAMES:
        assert name.islower()
        assert " " not in name
        assert "-" not in name
