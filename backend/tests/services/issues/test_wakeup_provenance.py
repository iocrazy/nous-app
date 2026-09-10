"""A wake-up's provenance has to reach the thread the UI actually reads.

Task 7a defects 6 + 7 (2026-09-10 frontend acceptance). One structure, two
symptoms:

* **6 (high)** ``meta.source`` was written ONLY into ``issue_messages``, but
  ``GET /issues/{id}/messages`` reads the CONVERSATION whenever
  ``issues.ai_session_id`` is set — which every modern issue has. The row the
  UI got back carried ``meta: {}``, so ``startedByWakeup`` was false forever
  and the "Started By Wake-up" chip could not render on production, ever.
* **7 (medium)** the idle branch appended the body to the conversation and
  then handed the same body to ``respond_to_issue_reply``, which appends it
  again — two identical "You commented" bubbles 189 ms apart, and the same
  sentence twice in the agent's own history.

Both are fixed by appending ONCE, inside the turn, with the provenance
attached to that single message. This file follows the value across every hop
of that chain, because a break at any one of them is invisible at the others:

    deliver_or_dispatch → dispatch_issue_reply → respond_to_issue_reply
      → run_issue_reply_step → run_session_turn → append_user_message
      → body.meta.source → metadata_json.source → IssueMessage.meta.source
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from tests.services.issues.test_inbox_or_dispatch import ME, _issue, _wire

pytestmark = pytest.mark.unit

SOURCE = {"kind": "schedule", "schedule_id": "s1", "created_by": "user"}


# ── hop 1: the delivery must not append the bubble itself ───────────────


async def test_the_idle_branch_appends_no_conversation_message(monkeypatch):
    """Defect 7. The turn appends the user message itself; doing it here too
    is how the same sentence landed twice."""
    from app.services.issues.inbox_or_dispatch import deliver_or_dispatch

    _inbox, store, _dispatch, _rows = _wire(monkeypatch, issue=_issue(), running=None)
    out = await deliver_or_dispatch(
        5,
        kind="schedule_fired",
        content={"body": "wake"},
        user_id=ME,
        message_body="wake",
        source=SOURCE,
    )
    assert out.mode == "dispatched"
    store.append_user_message.assert_not_awaited()


async def test_the_idle_branch_still_writes_the_issue_messages_mirror(monkeypatch):
    """The mirror row is what the legacy (no-session) read path renders, and
    it is the audit trail. Only the DOUBLE append goes away."""
    from app.services.issues.inbox_or_dispatch import deliver_or_dispatch

    _inbox, _store, _dispatch, rows = _wire(monkeypatch, issue=_issue(), running=None)
    await deliver_or_dispatch(
        5,
        kind="schedule_fired",
        content={},
        user_id=ME,
        message_body="wake",
        source=SOURCE,
    )
    assert rows[0]["meta"] == {"source": SOURCE}
    assert rows[0]["kind"] == "comment"


async def test_the_idle_branch_hands_the_source_to_the_dispatch(monkeypatch):
    """Defect 6. This is the hop that carries provenance to the message the
    UI reads; without it the chip has no input."""
    from app.services.issues.inbox_or_dispatch import deliver_or_dispatch

    _inbox, _store, dispatch, _rows = _wire(monkeypatch, issue=_issue(), running=None)
    await deliver_or_dispatch(
        5,
        kind="schedule_fired",
        content={},
        user_id=ME,
        message_body="wake",
        source=SOURCE,
    )
    assert dispatch.call_args.args[5] == SOURCE


# ── hop 2: dispatch_issue_reply → the workflow ──────────────────────────


async def test_dispatch_issue_reply_forwards_the_source(monkeypatch):
    import app.services.issues.issue_reply_dispatch as dispatch_mod
    from app.services.issues.inbox_or_dispatch import dispatch_issue_reply

    sent = MagicMock()
    monkeypatch.setattr(dispatch_mod, "dispatch_respond_to_issue_reply", sent)

    out = await dispatch_issue_reply(5, user_id=ME, body="wake", source=SOURCE)

    assert out.mode == "dispatched"
    assert sent.call_args.args[5] == SOURCE


async def test_a_dispatch_without_provenance_sends_none(monkeypatch):
    import app.services.issues.issue_reply_dispatch as dispatch_mod
    from app.services.issues.inbox_or_dispatch import dispatch_issue_reply

    sent = MagicMock()
    monkeypatch.setattr(dispatch_mod, "dispatch_respond_to_issue_reply", sent)
    await dispatch_issue_reply(5, user_id=ME, body="hi")
    assert sent.call_args.args[5] is None


# ── hop 3: the workflow binds it to the turn it runs ────────────────────


async def test_the_reply_workflow_binds_the_source_to_its_turn(monkeypatch):
    """``_run_reply_turns`` calls the injected ``run_turn`` with a fixed
    kwarg set, so the source rides in bound to the callable rather than
    widening a signature every test fake would have to follow."""
    from app.workflows import issue_lifecycle as m

    seen: dict = {}

    async def _fake_run_reply_turns(*a, **kw):
        seen.update(kw)
        return {"ok": True}

    monkeypatch.setattr(m, "ensure_issue_session_step", AsyncMock(return_value="55"))
    monkeypatch.setattr(m, "load_auto_close_flag", AsyncMock(return_value=False))
    monkeypatch.setattr(m, "publish_status", AsyncMock())
    monkeypatch.setattr(m, "_run_reply_turns", _fake_run_reply_turns)

    _raw = m.respond_to_issue_reply.__wrapped__.__wrapped__
    await _raw(5, ME, "wake", None, SOURCE)

    bound = getattr(seen["run_turn"], "keywords", {})
    assert bound.get("source") == SOURCE


async def test_a_plain_reply_runs_the_bare_step(monkeypatch):
    """Negative control: no provenance → the step goes in unwrapped, exactly
    as before, so the common path keeps its identity."""
    from app.workflows import issue_lifecycle as m

    seen: dict = {}

    async def _fake_run_reply_turns(*a, **kw):
        seen.update(kw)
        return {"ok": True}

    monkeypatch.setattr(m, "ensure_issue_session_step", AsyncMock(return_value="55"))
    monkeypatch.setattr(m, "load_auto_close_flag", AsyncMock(return_value=False))
    monkeypatch.setattr(m, "publish_status", AsyncMock())
    monkeypatch.setattr(m, "_run_reply_turns", _fake_run_reply_turns)

    _raw = m.respond_to_issue_reply.__wrapped__.__wrapped__
    await _raw(5, ME, "hi", None)

    assert seen["run_turn"] is m.run_issue_reply_step


# ── hop 4: the step → the turn ──────────────────────────────────────────


async def test_run_issue_reply_step_forwards_the_source(monkeypatch):
    from app.workflows import issue_lifecycle as m

    seen: dict = {}

    async def _turn_stub(session_id, **kw):
        seen.update(kw)
        return {"assistant_message": {"content": "hi", "metadata_json": {}}}

    monkeypatch.setattr(
        m,
        "AILibraryChatService",
        lambda: type("C", (), {"run_session_turn": staticmethod(_turn_stub)})(),
    )
    monkeypatch.setattr(m, "publish_chunk", AsyncMock())
    monkeypatch.setattr(m, "publish_message", AsyncMock())

    await m.run_issue_reply_step(
        issue_id=42,
        session_id="55",
        user_id=ME,
        reply_text="wake",
        source=SOURCE,
    )
    assert seen["message_source"] == SOURCE


# ── hop 5: the turn writes it onto the ONE user message ─────────────────


async def _turn(**turn_kwargs):
    """A real ``run_session_turn`` with the LLM stack stubbed; returns the
    fake store so the appended user message can be inspected."""
    from app.services.ai.chat import ai_library_chat_service as svc_mod
    from tests.test_parity_gap_coverage import (
        _chat_env,
        _FakeStore,
        _RunRecorderCM,
        _session_row,
    )

    user_id, agent_id = uuid4(), uuid4()
    store = _FakeStore(_session_row(user_id, agent_id))
    with _chat_env(
        run_turn_result={"content": "ok", "tool_calls": []}, agent_id=agent_id
    ) as env_recorder:
        with patch.object(
            svc_mod,
            "RunRecorder",
            side_effect=lambda **kw: _RunRecorderCM(env_recorder),
        ):
            await svc_mod.AILibraryChatService(store=store).run_session_turn(
                uuid4(),
                user_id=user_id,
                content="wake",
                trigger="issue_reply",
                **turn_kwargs,
            )
    return store


def _user_bubbles(store) -> list[dict]:
    return [r for r in store.appended if r.get("role") == "user"]


async def test_the_turn_writes_the_source_onto_the_user_message():
    store = await _turn(message_source=SOURCE)
    bubbles = _user_bubbles(store)
    assert len(bubbles) == 1, "the turn must append exactly one user bubble"
    assert bubbles[0]["metadata"] == {"source": SOURCE}


async def test_a_turn_without_provenance_writes_no_metadata():
    store = await _turn()
    bubbles = _user_bubbles(store)
    assert len(bubbles) == 1
    assert bubbles[0].get("metadata") in (None, {})


# ── hop 6: the store round-trips it as metadata_json ────────────────────


async def test_the_store_persists_source_under_body_meta(monkeypatch):
    import app.repositories.conversation_repository as repo_mod
    from app.services.ai.chat.conversations_ai_store import ConversationsAiStore

    sent: dict = {}

    async def _send_message(**kw):
        sent.update(kw)
        return {"id": 1, "conversation_id": 55, "created_at": "now"}

    monkeypatch.setattr(
        repo_mod,
        "get_conversation_repository",
        lambda: type("R", (), {"send_message": staticmethod(_send_message)})(),
    )

    out = await ConversationsAiStore().append_user_message(
        session_id=55, user_id=ME, content="wake", metadata={"source": SOURCE}
    )

    assert sent["body"]["meta"] == {"source": SOURCE}
    assert sent["body"]["text"] == "wake"
    assert out["metadata_json"] == {"source": SOURCE}


def test_the_read_path_reconstructs_metadata_json_from_body_meta():
    """The half that makes the write worth doing: what ``get_messages``
    hands the mapper."""
    from app.services.ai.chat.conversations_ai_store import ConversationsAiStore

    row = ConversationsAiStore._to_legacy_message_shape(
        {
            "id": 1,
            "conversation_id": 55,
            "sender_type": "user",
            "body": {"text": "wake", "meta": {"source": SOURCE}},
            "created_at": "now",
        }
    )
    assert row["role"] == "user"
    assert (row["metadata_json"] or {})["source"] == SOURCE


# ── hop 7: the endpoint's item shape (the frontend contract) ────────────


def test_the_thread_item_carries_meta_source():
    """``meta.source.kind === 'schedule'`` is what the chip reads
    (issueMessageService.startedByWakeup)."""
    from app.services.issues.issue_message_mapper import (
        map_ai_message_to_issue_message,
    )

    item = map_ai_message_to_issue_message(
        {
            "id": 1,
            "role": "user",
            "content": "wake",
            "metadata_json": {"source": SOURCE},
            "created_at": "2026-09-10T09:44:04Z",
        },
        issue_id=5,
        session_user_id=None,
    )
    assert item.meta["source"]["kind"] == "schedule"


def test_a_plain_comment_item_has_no_source_key():
    from app.services.issues.issue_message_mapper import (
        map_ai_message_to_issue_message,
    )

    item = map_ai_message_to_issue_message(
        {
            "id": 1,
            "role": "user",
            "content": "hi",
            "metadata_json": None,
            "created_at": "2026-09-10T09:44:04Z",
        },
        issue_id=5,
        session_user_id=None,
    )
    assert "source" not in (item.meta or {})
