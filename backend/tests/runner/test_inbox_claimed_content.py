"""What ``inbox_claimed`` has to carry for the trajectory to read it.

Task 7b defect G. The event was ``{inbox_id, kind, turn, step}`` — no
``content`` — while the emitter already held the whole ``InboxItem``. The
frontend fold reads ``content`` (``foldEvents.ts``), got ``{}``, and every
field defaulted:

* ``subagent_type`` fell to the literal ``'subagent'`` instead of the agent;
* ``summary`` was empty, so the summary row never rendered on any card;
* ``child_run_id`` was null, so the inbox row and the card never matched up;
* **``status`` fell to ``'completed'``** — a FAILED sub-agent rendered as
  ``✓ Done`` in ok colour. That last one is not a missing fact, it is the
  opposite conclusion, drawn from a default that happens to mean success.

Two things this pins besides presence:

* the projection is BOUNDED — transcript rows are read forever, so the raw
  body never goes in whole;
* the MODEL-visible frame is a separate thing and does not move. What the
  model reads is ``render_inbox_message``; this payload is for the transcript
  and the UI.
"""

from __future__ import annotations

import datetime as dt

import pytest

from app.services.ai.runner.inbox import (
    CLAIMED_TEXT_MAX,
    InboxItem,
    claimed_event_content,
    render_inbox_message,
)
from app.services.ai.runner.inbox_hook import InboxClaimHook
from app.services.ai.runner.step_hooks import StepContext

pytestmark = pytest.mark.unit
NOW = dt.datetime(2026, 9, 5, tzinfo=dt.timezone.utc)

#: The real ``agent_run_inbox.content`` a background sub-agent writes, copied
#: from the 2026-09-10 acceptance rather than idealised — the fold was correct
#: against a hand-written payload and wrong against this one.
REAL_SUBAGENT_CONTENT = {
    "child_run_id": "348057286155642",
    "subagent_type": "summarize",
    "description": "probe",
    "status": "success",
    "summary": "OK",
    "cost_cents": 0.0236,
    "tokens_used": 1725,
}

#: Likewise for a scheduled wake-up (MH-86).
REAL_STEER_CONTENT = {
    "text": "Steer probe: also add a one-line title",
    "source": {
        "kind": "schedule",
        "created_by": "user",
        "schedule_id": "c16bb735-82d3-4f0f-9684-d4a01bdbce44",
    },
    "dedupe_key": "sched:c16bb735-82d3-4f0f-9684-d4a01bdbce44:2026-09-10T12:15:04+00:00",
}


def _item(**kw) -> InboxItem:
    base = dict(
        id=310819108761499,
        target_kind="issue",
        target_id=7,
        kind="steer",
        content={"body": "focus on act two"},
        created_at=NOW,
        user_id="u",
    )
    base.update(kw)
    return InboxItem(**base)


# ── the sub-agent result arm ───────────────────────────────────────────


def test_a_subagent_result_carries_every_field_the_card_renders():
    out = claimed_event_content(
        _item(kind="subagent_result", content=REAL_SUBAGENT_CONTENT)
    )
    assert out == {
        "child_run_id": "348057286155642",
        "subagent_type": "summarize",
        "description": "probe",
        "status": "success",
        "summary": "OK",
        "cost_cents": 0.0236,
        "tokens_used": 1725,
    }


def test_a_failed_child_says_failed_rather_than_leaving_it_to_a_default():
    """The whole point. ``status`` absent meant ``completed`` downstream, so a
    failed child read as ✓ Done — the 2026-09-10 walkthrough saw exactly
    that on MH-80."""
    out = claimed_event_content(
        _item(
            kind="subagent_result",
            content={**REAL_SUBAGENT_CONTENT, "status": "failed", "summary": ""},
        )
    )
    assert out["status"] == "failed"


def test_a_long_summary_is_truncated_and_says_so():
    out = claimed_event_content(
        _item(kind="subagent_result", content={"summary": "x" * 5000})
    )
    assert len(out["summary"]) <= CLAIMED_TEXT_MAX
    assert out["summary"].endswith("…")


def test_a_subagent_result_with_an_empty_envelope_still_has_every_key():
    """A reader must never have to tell "no value" apart from "this arm forgot
    to answer" — the defect was exactly the second one wearing the first's
    clothes."""
    out = claimed_event_content(_item(kind="subagent_result", content={}))
    assert set(out) == set(REAL_SUBAGENT_CONTENT)
    assert out["status"] is None and out["child_run_id"] is None


# ── the text arm ───────────────────────────────────────────────────────


def test_a_steer_carries_its_text_and_its_provenance_intact():
    """``source`` is what the wake-up chip reads, so it goes through whole —
    it is a small fixed shape, not free text."""
    out = claimed_event_content(_item(kind="steer", content=REAL_STEER_CONTENT))
    assert out["text"] == "Steer probe: also add a one-line title"
    assert out["source"] == REAL_STEER_CONTENT["source"]


def test_a_steer_without_provenance_omits_the_key():
    out = claimed_event_content(_item(kind="steer", content={"body": "focus"}))
    assert out == {"text": "focus"}


def test_an_answer_carries_the_value_a_person_chose():
    out = claimed_event_content(
        _item(kind="answer", content={"question_id": "q1", "value": "option B"})
    )
    assert out["text"] == "option B"


def test_a_long_steer_is_truncated_too():
    out = claimed_event_content(_item(kind="steer", content={"text": "y" * 5000}))
    assert len(out["text"]) <= CLAIMED_TEXT_MAX


def test_bookkeeping_the_ui_never_shows_stays_out_of_the_transcript():
    """A transcript row is stored and re-read forever. ``dedupe_key`` is
    replay plumbing — carrying it would grow every row for a reader that does
    not exist."""
    out = claimed_event_content(_item(kind="steer", content=REAL_STEER_CONTENT))
    assert "dedupe_key" not in out


def test_every_kind_the_table_allows_produces_a_payload():
    """mig 461's CHECK is the list. A kind with no arm would emit nothing and
    the row would go back to being blank for that case only."""
    for kind in ("steer", "answer", "pause", "resume", "budget_reply"):
        out = claimed_event_content(_item(kind=kind, content={"body": "b"}))
        assert "text" in out, kind


# ── the hook emits it ──────────────────────────────────────────────────


class _Rec:
    def __init__(self, run_id=42, issue_id=7):
        self.run_id = run_id
        self.issue_id = issue_id
        self.conversation_id = None
        self.events = []

    async def record_event(self, event_type, payload, *, turn=None, step=None):
        self.events.append((event_type, payload, turn, step))


@pytest.mark.asyncio
async def test_the_hook_puts_the_content_on_the_event():
    item = _item(kind="subagent_result", content=REAL_SUBAGENT_CONTENT)

    async def claim(tg, run_id, turn, step):
        return [item]

    async def resolve(**_):
        return [("issue", 7)]

    rec = _Rec()
    hook = InboxClaimHook(claim=claim, resolve=resolve)
    await hook.before_llm_call(
        StepContext(turn=1, step=3, recorder=rec, parent_run_id=None)
    )

    (event_type, payload, turn, step) = rec.events[0]
    assert event_type == "inbox_claimed"
    assert payload["inbox_id"] == "310819108761499"
    assert payload["kind"] == "subagent_result"
    assert payload["content"]["status"] == "success"
    assert payload["content"]["subagent_type"] == "summarize"


# ── the model's side does not move ─────────────────────────────────────


def test_the_frame_the_model_reads_is_unchanged():
    """This payload is the transcript's, not the model's. The frame still
    carries only the summary as its body plus the two attributes — adding the
    envelope here must not quietly grow what every turn pays for."""
    out = render_inbox_message(
        _item(kind="subagent_result", content=REAL_SUBAGENT_CONTENT)
    )
    assert 'child_run_id="348057286155642"' in out
    assert 'subagent_type="summarize"' in out
    body = "\n".join(out.split("\n")[1:-1])
    assert body == "OK"
    assert "1725" not in out and "0.0236" not in out
