"""An all-whitespace summary is a failed summary, not an accepted one.

``count_messages_tokens`` of an empty framed summary is always below the head,
so the shrink check accepted it and the live turn carried an empty
``<conversation_summary>`` frame — while ``replay.messages_from_events`` skips
empty summaries and keeps the original history. Treating it as a failed
attempt sends the compactor down the existing emergency-cap path, and the two
shapes agree again.
"""

from __future__ import annotations

import pytest

from app.agent_framework import context_compactor as cc
from tests.agent_framework.compaction_stubs import SUMMARY_MARKER

pytestmark = pytest.mark.unit


async def test_blank_summary_is_rejected_and_falls_back_to_emergency_cap(monkeypatch):
    compactor = cc.ContextCompactor()
    monkeypatch.setattr(compactor, "SUMMARY_ATTEMPTS", 2)
    produced: list[str] = []

    async def _blank(head, **kw):
        produced.append("   \n")
        return "   \n", "warm"

    monkeypatch.setattr(compactor, "_produce_summary", _blank)
    events: list[tuple[str, dict]] = []

    class _Rec:
        async def record_event(self, kind, payload):
            events.append((kind, payload))

    messages = [
        {"role": "user", "content": f"turn {i} " + "x" * 400} for i in range(12)
    ]
    # Force the orange tier regardless of the model table.
    monkeypatch.setattr(cc, "resolve_model_window", lambda model: (1000, True))
    monkeypatch.setattr(cc, "count_messages_tokens", lambda msgs, model: 70 * len(msgs))
    out, stats = await compactor.maybe_compact(
        system_message="sys", user_messages=messages, model="m", recorder=_Rec()
    )
    assert len(produced) == 2, "every attempt must be tried before giving up"
    assert not any(SUMMARY_MARKER in str(m.get("content", "")) for m in out), out
    summary_events = [p for k, p in events if k == "compaction_summary"]
    assert summary_events and summary_events[-1]["path"] == "emergency_cap", events
    assert any("emergency-cap fallback" in n for n in stats.notes), stats.notes
