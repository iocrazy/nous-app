"""Phase 2b-1: events[:at_seq] → the messages a forked run starts from.
Mirrors build_history_messages: user / assistant / system only; a
compaction_summary replaces everything before it; tool_call never becomes a
message (it is not in cross-turn history either)."""

import pytest

from app.services.ai.runner import replay as r

pytestmark = pytest.mark.unit


def _ev(seq, t, **p):
    return {"seq": seq, "event_type": t, "payload": p}


def test_user_and_assistant_become_messages_in_order():
    evs = [
        _ev(1, "user", content="write act 1"),
        _ev(2, "step_start", turn=1, step=1),
        _ev(3, "tool_call", tool="Skill", args={"skill": "x"}, result={"ok": True}),
        _ev(4, "assistant", content="Act 1 …"),
        _ev(5, "step_end", turn=1, step=1),
    ]
    assert r.messages_from_events(evs) == [
        {"role": "user", "content": "write act 1"},
        {"role": "assistant", "content": "Act 1 …"},
    ]


def _compaction(seq, **extra):
    """The wire shape ContextCompactor._compact_with_summary emits (metrics +
    the accepted summary text) — not an idealised {summary} payload."""
    return _ev(
        seq,
        "compaction_summary",
        summary_tokens=50,
        head_tokens=800,
        attempts=1,
        path="legacy",
        **extra,
    )


def test_compaction_summary_replaces_everything_before_it_with_the_runtime_prefix():
    evs = [
        _ev(1, "user", content="a"),
        _ev(2, "assistant", content="b"),
        _compaction(3, summary="a and b, condensed"),
        _ev(4, "user", content="c"),
    ]
    assert r.messages_from_events(evs) == [
        {"role": "system", "content": r.SUMMARY_PREFIX + "a and b, condensed"},
        {"role": "user", "content": "c"},
    ]


def test_metrics_only_compaction_row_keeps_the_history_it_cannot_replace():
    """Emergency-cap rows (and runs recorded before the text was stamped)
    carry no summary — a missing summary must never wipe the history."""
    evs = [
        _ev(1, "user", content="a"),
        _ev(2, "assistant", content="b"),
        _ev(3, "compaction_summary", path="emergency_cap", attempts=3, error="x"),
        _ev(4, "user", content="c"),
    ]
    assert r.messages_from_events(evs) == [
        {"role": "user", "content": "a"},
        {"role": "assistant", "content": "b"},
        {"role": "user", "content": "c"},
    ]


def test_events_upto_slices_by_seq_not_by_index():
    evs = [_ev(2, "user", content="a"), _ev(5, "step_start"), _ev(9, "assistant")]
    assert [e["seq"] for e in r.events_upto(evs, 5)] == [2, 5]
    assert r.events_upto(evs, 1) == []
    assert [e["seq"] for e in r.events_upto(evs, 9)] == [2, 5, 9]


def test_empty_content_events_are_skipped_and_unknown_types_ignored():
    evs = [
        _ev(1, "user", content=""),
        _ev(2, "fork", of_run_id=1, at_seq=2),
        _ev(3, "assistant", content="x"),
    ]
    assert r.messages_from_events(evs) == [{"role": "assistant", "content": "x"}]


def test_is_step_boundary_accepts_step_start_and_turn_end_only():
    evs = [
        _ev(1, "user"),
        _ev(2, "step_start", turn=1, step=1),
        _ev(3, "step_end", turn=1, step=1),
        _ev(4, "turn_end", reason="completed"),
    ]
    assert r.is_step_boundary(evs, 2) is True
    assert r.is_step_boundary(evs, 4) is True
    assert r.is_step_boundary(evs, 3) is False
    assert r.is_step_boundary(evs, 99) is False
