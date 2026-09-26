"""fh5 A2 (recon-a §1.6, I4): a fork keeps what the model saw between the
summary and the run.

A run that opens with a summary saw ``[summary] + rows after the summary's
watermark + this run``. Before A2 the seed returned the run's own messages
only, so every verbatim row between the watermark and the run — the previous
turns' tail after a live compaction, or everything since a stored summary —
vanished from the forked session. The ``compaction_summary`` payload now
carries ``covers_up_to_seq``; the seed puts the origin rows past it back.
Events without the key (every run recorded before A2) seed exactly as before.
"""

import pytest

from app.boundary.summary_frame import frame_summary
from app.services.ai.runner.replay import summary_watermark_from_events
from app.services.issues import issue_fork as f

pytestmark = pytest.mark.unit

ORIGIN = [
    {"role": "user", "content": "old ask", "seq": 39},
    {"role": "assistant", "content": "old reply", "seq": 40},
    {"role": "user", "content": "recent ask", "seq": 41},
    {"role": "assistant", "content": "recent reply", "seq": 42},
    {"role": "user", "content": "go", "seq": 43},
]


def _events(summary_payload: dict) -> list[dict]:
    return [
        {"seq": 1, "event_type": "compaction_summary", "payload": summary_payload},
        {"seq": 2, "event_type": "user", "payload": {"content": "go"}},
        {"seq": 3, "event_type": "step_start", "payload": {}},
        {"seq": 4, "event_type": "assistant", "payload": {"content": "act 1"}},
        {"seq": 5, "event_type": "turn_end", "payload": {}},
    ]


def test_fork_seed_includes_rows_between_watermark_and_run():
    events = _events({"path": "stored", "summary": "S", "covers_up_to_seq": 40})
    assert f.seed_messages(ORIGIN, events) == [
        {"role": "system", "content": frame_summary("S")},
        {"role": "user", "content": "recent ask"},
        {"role": "assistant", "content": "recent reply"},
        {"role": "user", "content": "go"},
        {"role": "assistant", "content": "act 1"},
    ]


def test_fork_seed_after_a_fresh_compaction_keeps_the_verbatim_tail():
    """The live case the §1.6 gap was about: a warm summary covering up to 41
    leaves 42 and 43 verbatim in the model's view."""
    events = _events({"path": "warm", "summary": "S", "covers_up_to_seq": 41})
    seeded = f.seed_messages(ORIGIN, events)
    assert [m["content"] for m in seeded] == [
        frame_summary("S"),
        "recent reply",
        "go",
        "act 1",
    ]


def test_fork_seed_legacy_event_without_watermark_unchanged():
    events = _events({"path": "legacy", "summary": "S", "attempts": 1})
    assert f.seed_messages(ORIGIN, events) == [
        {"role": "system", "content": frame_summary("S")},
        {"role": "user", "content": "go"},
        {"role": "assistant", "content": "act 1"},
    ]


def test_fork_seed_without_a_summary_is_origin_plus_run():
    events = _events({"path": "emergency_cap", "attempts": 2, "error": "x"})
    seeded = f.seed_messages(ORIGIN, events)
    assert [m["content"] for m in seeded] == [
        "old ask",
        "old reply",
        "recent ask",
        "recent reply",
        "go",
        "act 1",
    ]


def test_watermark_follows_the_summary_that_won():
    """messages_from_events keeps the LAST summary; the watermark must be that
    summary's, and a later metrics-only row does not erase it."""
    events = [
        {
            "seq": 1,
            "event_type": "compaction_summary",
            "payload": {"summary": "A", "covers_up_to_seq": 10},
        },
        {
            "seq": 2,
            "event_type": "compaction_summary",
            "payload": {"summary": "B", "covers_up_to_seq": 20},
        },
        {
            "seq": 3,
            "event_type": "compaction_summary",
            "payload": {"path": "emergency_cap"},
        },
    ]
    assert summary_watermark_from_events(events) == 20
    assert summary_watermark_from_events(events[:1]) == 10
    assert summary_watermark_from_events([]) is None


def test_watermark_is_none_when_the_winning_summary_has_none():
    events = [
        {
            "seq": 1,
            "event_type": "compaction_summary",
            "payload": {"summary": "A", "covers_up_to_seq": 10},
        },
        {"seq": 2, "event_type": "compaction_summary", "payload": {"summary": "B"}},
    ]
    assert summary_watermark_from_events(events) is None
