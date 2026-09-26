"""fh5 A2: the compactor names the seq its summary covers.

``maybe_compact`` takes an optional ``message_seqs`` list aligned 1:1 with the
messages (the carried frame carries the old watermark, history rows their own
``seq``, the new user message ``None``). When a summary is accepted it reports
``covers_up_to_seq`` = the largest seq in the summarized head — in the stats
and in the ``compaction_summary`` payload — plus the raw text and its path, so
the chat service can persist it. A carried frame in the head is summarized
along with it: the new summary supersedes the old one, one frame out.
"""

from unittest.mock import AsyncMock, patch

import pytest

from app.agent_framework.context_compactor import CompactionTier, ContextCompactor
from app.boundary.summary_frame import SUMMARY_PREFIX, render_summary_message

WINDOW = 2_000
SYSTEM = "sys"


class _Rec:
    def __init__(self):
        self.events: list[tuple[str, dict]] = []

    async def record_event(self, event_type, payload, **_):
        self.events.append((event_type, payload))


def _adapter(text: str = "a short rolling summary"):
    adapter = AsyncMock()
    adapter.call = AsyncMock(
        return_value={
            "choices": [
                {
                    "message": {"role": "assistant", "content": text},
                    "finish_reason": "stop",
                }
            ]
        }
    )
    return adapter


def _rows(n: int, start_seq: int) -> tuple[list[dict], list[int]]:
    msgs, seqs = [], []
    for i in range(n):
        role = "user" if i % 2 == 0 else "assistant"
        msgs.append({"role": role, "content": f"row {start_seq + i} " + "word " * 120})
        seqs.append(start_seq + i)
    return msgs, seqs


async def _compact(messages, seqs, recorder, adapter=None):
    with patch(
        "app.agent_framework.context_compactor.resolve_model_window",
        return_value=(WINDOW, "builtin"),
    ):
        return await ContextCompactor().maybe_compact(
            system_message=SYSTEM,
            user_messages=messages,
            model="bench-model",
            adapter=adapter or _adapter(),
            recorder=recorder,
            message_seqs=seqs,
        )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_compactor_reports_covers_up_to_seq_and_summary_text():
    rows, seqs = _rows(12, start_seq=41)
    messages = rows + [{"role": "user", "content": "the new question"}]
    rec = _Rec()

    out, stats = await _compact(messages, seqs + [None], rec)

    assert stats.tier in (CompactionTier.ORANGE, CompactionTier.RED)
    kept = len(out) - 1  # everything after the frame is the verbatim tail
    head_seqs = (seqs + [None])[: len(messages) - kept]
    assert stats.covers_up_to_seq == max(s for s in head_seqs if s is not None)
    assert stats.summary_text == "a short rolling summary"
    assert stats.summary_path == "warm"
    summary_events = [p for t, p in rec.events if t == "compaction_summary"]
    assert len(summary_events) == 1
    assert summary_events[0]["covers_up_to_seq"] == stats.covers_up_to_seq


@pytest.mark.unit
@pytest.mark.asyncio
async def test_compactor_without_seqs_reports_no_watermark():
    """Callers that pass no seqs (issue runner paths, background jobs) keep
    today's payload exactly: no ``covers_up_to_seq`` key at all."""
    rows, _ = _rows(12, start_seq=1)
    rec = _Rec()

    _, stats = await _compact(rows + [{"role": "user", "content": "q"}], None, rec)

    assert stats.summary_text == "a short rolling summary"
    assert stats.covers_up_to_seq is None
    payload = next(p for t, p in rec.events if t == "compaction_summary")
    assert "covers_up_to_seq" not in payload


@pytest.mark.unit
@pytest.mark.asyncio
async def test_misaligned_seqs_are_ignored_not_trusted():
    """A seq list of the wrong length cannot say which rows the head holds;
    the summary is still accepted but no watermark is claimed."""
    rows, seqs = _rows(12, start_seq=1)
    _, stats = await _compact(rows + [{"role": "user", "content": "q"}], seqs, _Rec())
    assert stats.summary_text and stats.covers_up_to_seq is None


@pytest.mark.unit
@pytest.mark.asyncio
async def test_compactor_rolls_carried_summary_into_head():
    """The carried frame sits at index 0 with the old watermark; the head it
    belongs to is re-summarized and the output holds ONE frame — the new one."""
    carried = render_summary_message("OLD SUMMARY TEXT")
    rows, seqs = _rows(12, start_seq=101)
    messages = [carried] + rows + [{"role": "user", "content": "q"}]
    adapter = _adapter("NEW MERGED SUMMARY")

    out, stats = await _compact(messages, [100] + seqs + [None], _Rec(), adapter)

    frames = [m for m in out if str(m.get("content", "")).startswith(SUMMARY_PREFIX)]
    assert frames == [render_summary_message("NEW MERGED SUMMARY")]
    assert out[0] == frames[0]
    # the summarizer saw the old frame as part of the head it merged
    _, sent = adapter.call.await_args.args
    assert sent[0] == carried
    assert stats.covers_up_to_seq > 100


@pytest.mark.unit
@pytest.mark.asyncio
async def test_emergency_cap_reports_no_summary():
    """I9: the emergency path writes nothing — no text, no watermark."""
    rows, seqs = _rows(12, start_seq=1)
    adapter = _adapter("x " * 5000)  # never shrinks → both attempts rejected
    with patch(
        "app.agent_framework.summarizer.summarize",
        AsyncMock(side_effect=RuntimeError("legacy down")),
    ):
        _, stats = await _compact(
            rows + [{"role": "user", "content": "q"}], seqs + [None], _Rec(), adapter
        )
    assert stats.emergency_dropped_chars >= 0
    assert stats.summary_text is None and stats.covers_up_to_seq is None
