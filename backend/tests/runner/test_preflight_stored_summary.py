"""fh5 A2: a turn that starts from a stored summary says so in its transcript.

The chat service hands the runner the carried summary and the per-message
seqs (``runner.carried_summary`` / ``runner.turn_message_seqs``). The
preflight passes the seqs to the compactor, exposes its stats on the recorder
(``recorder.last_compaction`` — the chat service persists from it), and, when
no fresh summary was accepted but the turn still opens on the carried frame,
emits ``compaction_summary{path:"stored"}`` BEFORE the ``user`` event with no
start/end bracket (the Runs counter counts ``compaction_end`` only). Replay
and fork rebuild the head from that event exactly as they do for a fresh one.

Driven through ``stream_turn``'s buffered branch (adapter without ``stream``)
— production's only path (CLAUDE.md, 2026-09-08).
"""

from unittest.mock import patch
from uuid import UUID

import pytest

from app.agent_framework.context_compactor import CarriedSummary
from app.boundary.summary_frame import render_summary_message

CARRIED = CarriedSummary(text="What happened before, in brief.", covers_up_to_seq=40)


class _Rec:
    last_compaction = None

    def __init__(self):
        self.events: list[tuple[str, dict]] = []

    async def record_event(self, event_type, payload, *, turn=None, step=None):
        self.events.append((event_type, payload))

    def record_usage(self, **k):
        pass

    async def heartbeat(self):
        pass

    async def check_cancelled(self):
        return False

    def record_skill(self, s):
        pass

    def types(self) -> list[str]:
        return [t for t, _ in self.events]


class _NoStreamAdapter:  # no ``stream`` attribute on purpose
    def __init__(self):
        self.seen: list[list[dict]] = []

    async def call(self, composed, messages, *a, **k):
        self.seen.append(list(messages))
        return {
            "choices": [
                {
                    "message": {"role": "assistant", "content": "ok"},
                    "finish_reason": "stop",
                }
            ]
        }


def _composed():
    from app.schemas.ai_library import ComposedSystemPrompt

    return ComposedSystemPrompt(
        agent_id=UUID(int=1),
        agent_slug="t",
        model="m",
        temperature=0.0,
        max_tokens=16,
        system_message="sys",
        tools=[],
        skill_manifest=[],
        cache_fingerprint="f",
    )


def _runner(adapter):
    from app.services.ai.runner.agent_runner import AgentRunner
    from app.services.ai.runner.step_hooks import StepHookChain

    class _Tool:
        recorder = None

        async def execute(self, args):
            return {}

    return AgentRunner(
        adapter=adapter, skill_tool=_Tool(), step_hooks=StepHookChain([])
    )


def _turn_messages() -> tuple[list[dict], list]:
    msgs = [
        render_summary_message(CARRIED.text),
        {"role": "user", "content": "row 41"},
        {"role": "assistant", "content": "row 42"},
        {"role": "user", "content": "the new question"},
    ]
    return msgs, [CARRIED.covers_up_to_seq, 41, 42, None]


async def _drive(runner, messages, rec):
    return [
        ch
        async for ch in runner.stream_turn(
            _composed(), messages, recorder=rec, auto_recorder=False
        )
    ]


@pytest.mark.asyncio
async def test_preflight_emits_stored_summary_event_before_user():
    adapter = _NoStreamAdapter()
    runner = _runner(adapter)
    messages, seqs = _turn_messages()
    runner.carried_summary, runner.turn_message_seqs = CARRIED, seqs
    rec = _Rec()

    await _drive(runner, messages, rec)

    types = rec.types()
    assert "compaction_start" not in types and "compaction_end" not in types
    stored = [p for t, p in rec.events if t == "compaction_summary"]
    assert len(stored) == 1
    assert stored[0]["path"] == "stored"
    assert stored[0]["summary"] == CARRIED.text
    assert stored[0]["covers_up_to_seq"] == CARRIED.covers_up_to_seq
    assert stored[0]["summary_tokens"] > 0
    assert types.index("compaction_summary") < types.index("user")
    # the model saw the carried frame unchanged at index 0
    assert adapter.seen[0][0] == render_summary_message(CARRIED.text)


@pytest.mark.asyncio
async def test_preflight_exposes_stats_on_the_recorder_even_when_green():
    """``note_compaction`` early-returns on zero savings; the stats the chat
    service persists from must not depend on that."""
    runner = _runner(_NoStreamAdapter())
    messages, seqs = _turn_messages()
    runner.carried_summary, runner.turn_message_seqs = CARRIED, seqs
    rec = _Rec()

    await _drive(runner, messages, rec)

    assert rec.last_compaction is not None
    assert rec.last_compaction.summary_text is None  # green: nothing new


@pytest.mark.asyncio
async def test_no_stored_event_without_a_carried_summary():
    runner = _runner(_NoStreamAdapter())
    rec = _Rec()
    await _drive(runner, [{"role": "user", "content": "q"}], rec)
    assert "compaction_summary" not in rec.types()


@pytest.mark.asyncio
async def test_no_stored_event_when_the_frame_did_not_reach_the_model():
    """If the list no longer opens on the carried frame (the emergency cap
    truncated it), claiming the stored text would make replay rebuild a
    history the model never saw."""
    runner = _runner(_NoStreamAdapter())
    messages, seqs = _turn_messages()
    messages = [{"role": "system", "content": "something else"}] + messages[1:]
    runner.carried_summary, runner.turn_message_seqs = CARRIED, seqs
    rec = _Rec()
    await _drive(runner, messages, rec)
    assert "compaction_summary" not in rec.types()


@pytest.mark.asyncio
async def test_fresh_summary_replaces_the_stored_event_and_carries_the_watermark():
    """At orange the carried frame is rolled into the head: exactly one
    ``compaction_summary`` — the fresh one, with its own watermark."""
    from unittest.mock import AsyncMock

    adapter = _NoStreamAdapter()
    runner = _runner(adapter)
    rows = [
        {"role": "user" if i % 2 == 0 else "assistant", "content": "word " * 200}
        for i in range(10)
    ]
    messages = (
        [render_summary_message(CARRIED.text)]
        + rows
        + [{"role": "user", "content": "q"}]
    )
    seqs = [CARRIED.covers_up_to_seq] + list(range(41, 51)) + [None]
    runner.carried_summary, runner.turn_message_seqs = CARRIED, seqs
    rec = _Rec()
    warm = AsyncMock(return_value="merged summary")
    with (
        patch(
            "app.agent_framework.context_compactor.resolve_model_window",
            return_value=(3_000, "builtin"),
        ),
        patch("app.agent_framework.summarizer.summarize_warm_prefix", warm),
    ):
        await _drive(runner, messages, rec)

    summaries = [p for t, p in rec.events if t == "compaction_summary"]
    assert [p["path"] for p in summaries] == ["warm"]
    assert summaries[0]["covers_up_to_seq"] > CARRIED.covers_up_to_seq
    assert rec.last_compaction.summary_text == "merged summary"


@pytest.mark.asyncio
async def test_per_turn_history_state_starts_empty():
    runner = _runner(_NoStreamAdapter())
    assert runner.carried_summary is None and runner.turn_message_seqs is None
