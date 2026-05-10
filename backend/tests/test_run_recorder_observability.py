"""Phase 5 of #199 — RunRecorder observability hooks.

These pin the contract between the agent_framework (compactor /
subagent dispatcher) and the RunRecorder.metadata payload that ends
up in agent_runs.metadata_json.

Goals:
  - calling note_compaction(stats) accumulates into metadata.compaction
  - calling note_subagent(envelope) accumulates into metadata.subagents
  - both are duck-typed and never raise — telemetry must not crash a
    real workflow

We don't open a real RunRecorder lifecycle (that needs DB) — we
construct one with mocked async hooks so the metadata accumulator
under test runs without supabase.
"""

from __future__ import annotations

from uuid import uuid4

from app.agent_framework.context_compactor import (
    CompactionStats,
    CompactionTier,
)
from app.services.ai.runner.run_recorder import RunRecorder


def _make_recorder() -> RunRecorder:
    """Construct a RunRecorder without entering its async context — we
    just want the mutable .metadata + the new accumulator methods.
    The ``input_summary`` carries the same shape SubAgentTaskService
    would pass."""
    return RunRecorder(
        agent_id=uuid4(),
        user_id=uuid4(),
        trigger="test",
        session_id=None,
        team_id=None,
        project_id=None,
        model="claude-sonnet-4-6",
        provider="claude",
        input_summary="test prompt",
        metadata={},
    )


# ─── note_compaction ──────────────────────────────────────────────────


def test_note_compaction_accumulates_per_tier_count():
    """Every yellow / orange / red call must increment the matching
    tier counter. The Runs UI surfaces these to show how often a long
    conversation hit the compactor."""
    rec = _make_recorder()
    rec.note_compaction(
        CompactionStats(
            tier=CompactionTier.YELLOW,
            tokens_before=1000,
            tokens_after=700,
            tokens_saved=300,
        )
    )
    rec.note_compaction(
        CompactionStats(
            tier=CompactionTier.YELLOW,
            tokens_before=900,
            tokens_after=600,
            tokens_saved=300,
        )
    )
    rec.note_compaction(
        CompactionStats(
            tier=CompactionTier.ORANGE,
            tokens_before=2000,
            tokens_after=1200,
            tokens_saved=800,
        )
    )

    comp = rec.metadata["compaction"]
    assert comp["yellow_count"] == 2
    assert comp["orange_count"] == 1
    assert comp["total_tokens_saved"] == 1400


def test_note_compaction_skips_zero_savings():
    """Green tier returns CompactionStats with tokens_saved=0 — those
    are noop calls and shouldn't pollute the per-tier counters."""
    rec = _make_recorder()
    rec.note_compaction(
        CompactionStats(
            tier=CompactionTier.GREEN,
            tokens_before=100,
            tokens_after=100,
            tokens_saved=0,
        )
    )
    assert "compaction" not in rec.metadata


def test_note_compaction_tolerates_garbage():
    """A future caller passing the wrong shape (or a unit test mock
    that forgot a field) must not break the recorder — telemetry is
    best-effort."""
    rec = _make_recorder()

    class BadStats:
        pass  # no tier, no tokens_saved

    rec.note_compaction(BadStats())  # must not raise
    assert "compaction" not in rec.metadata


# ─── note_subagent ────────────────────────────────────────────────────


def test_note_subagent_counts_spawn_and_tokens():
    """Each Task tool call produces one envelope. The recorder rolls
    them up so the parent run's metadata exposes how many sub-agents
    fan out and how many tokens that cost across the tree."""
    rec = _make_recorder()
    rec.note_subagent({"status": "success", "tokens_used": 1234, "sub_run_id": "a"})
    rec.note_subagent({"status": "success", "tokens_used": 567, "sub_run_id": "b"})

    sub = rec.metadata["subagents"]
    assert sub["count"] == 2
    assert sub["tokens_used"] == 1801
    assert "failed_count" not in sub


def test_note_subagent_tracks_failures_separately():
    """Failed sub-agents (timeout / unknown slug / depth cap) must
    bump a distinct counter so the Runs UI can render error rate
    without re-parsing every sub-run row."""
    rec = _make_recorder()
    rec.note_subagent({"status": "success", "tokens_used": 100})
    rec.note_subagent({"status": "failed", "tokens_used": 0})
    rec.note_subagent({"status": "failed", "error": "depth"})

    sub = rec.metadata["subagents"]
    assert sub["count"] == 3
    assert sub["failed_count"] == 2


def test_note_subagent_handles_missing_tokens():
    """Failure envelopes from the validation path (depth / slug)
    don't include ``tokens_used``. Counter still bumps; tokens stay 0."""
    rec = _make_recorder()
    rec.note_subagent({"status": "failed", "error": "no slug"})
    sub = rec.metadata["subagents"]
    assert sub["count"] == 1
    assert sub.get("tokens_used", 0) == 0


def test_note_subagent_handles_garbage_envelope():
    """A non-dict envelope must not crash the recorder."""
    rec = _make_recorder()
    rec.note_subagent(None)  # type: ignore[arg-type]
    # Counter still bumps (we observed the spawn happened); other
    # fields stay clean.
    assert rec.metadata["subagents"]["count"] == 1
