"""The reply turn step is idempotent under DBOS recovery, like the dispatch step.

``run_issue_reply_step`` runs ``run_session_turn`` inside a ``@DBOS.step``. A
worker that dies mid-turn makes recovery re-execute the step: before this it
appended the reply's user message again, opened a fresh unlinked billed run,
and had no cap beyond DBOS ``recovery_attempts``. The step now does what
``run_issue_agent`` does (fh2): count the attempt under
``<workflow_id>:<step_id>`` before any turn work and thread the same key into
``run_session_turn`` (which reuses the stamped user message and links the run
via ``recovered_from``).
"""

from __future__ import annotations

import functools
import hashlib
import inspect
from unittest.mock import AsyncMock

import pytest

from app.services.issues import turn_recovery as tr

pytestmark = pytest.mark.unit

ISSUE_ID = 7
SESSION_ID = "1234567890123456789"
USER = "22222222-2222-2222-2222-222222222222"


def _turn_result() -> dict:
    return {
        "assistant_message": {
            "id": "m1",
            "role": "assistant",
            "content": "ok",
            "agent_id": None,
            "metadata_json": {},
            "created_at": "2026-09-25T00:00:00+00:00",
        },
        "run_id": "r1",
    }


@pytest.fixture
def stubbed(monkeypatch):
    from app.workflows import issue_lifecycle as m

    chat_svc = AsyncMock()
    chat_svc.run_session_turn = AsyncMock(return_value=_turn_result())
    monkeypatch.setattr(m, "AILibraryChatService", lambda: chat_svc)
    publish_chunk, publish_message = AsyncMock(), AsyncMock()
    monkeypatch.setattr(m, "publish_chunk", publish_chunk)
    monkeypatch.setattr(m, "publish_message", publish_message)
    return m, chat_svc, publish_chunk, publish_message


async def _run_step(m, **extra):
    return await m.run_issue_reply_step.__wrapped__(
        issue_id=ISSUE_ID,
        session_id=SESSION_ID,
        user_id=USER,
        reply_text="hi",
        **extra,
    )


async def test_reply_step_threads_the_step_key_into_the_turn(stubbed, monkeypatch):
    m, chat_svc, _, _ = stubbed
    monkeypatch.setattr(m, "current_dbos_step_key", lambda: "wf:5")
    monkeypatch.setattr(tr, "record_step_attempt", AsyncMock(return_value=1))

    await _run_step(m)
    assert chat_svc.run_session_turn.await_args.kwargs["dbos_step_key"] == "wf:5"


async def test_reply_step_without_dbos_context_is_todays_call(stubbed, monkeypatch):
    """Negative control: no key → no counting and no new kwarg (fakes that pin
    run_session_turn's exact kwarg set keep working)."""
    m, chat_svc, _, _ = stubbed
    monkeypatch.setattr(m, "current_dbos_step_key", lambda: None)
    counted = AsyncMock()
    monkeypatch.setattr(tr, "record_step_attempt", counted)

    await _run_step(m)
    counted.assert_not_awaited()
    assert "dbos_step_key" not in chat_svc.run_session_turn.await_args.kwargs


async def test_reply_step_refuses_past_the_recovery_limit_before_any_turn_work(
    stubbed, monkeypatch
):
    m, chat_svc, publish_chunk, publish_message = stubbed
    monkeypatch.setattr(m, "current_dbos_step_key", lambda: "wf:5")
    monkeypatch.setattr(tr, "record_step_attempt", AsyncMock(return_value=4))

    with pytest.raises(tr.IssueTurnRecoveryLimitExceeded) as info:
        await _run_step(m)
    assert str(info.value).startswith("[issue_turn_recovery_limit]")
    chat_svc.run_session_turn.assert_not_awaited()
    publish_chunk.assert_not_awaited()
    publish_message.assert_not_awaited()


async def test_reply_step_counts_the_attempt_under_the_issue(stubbed, monkeypatch):
    m, _, _, _ = stubbed
    monkeypatch.setattr(m, "current_dbos_step_key", lambda: "wf:5")
    counted = AsyncMock(return_value=2)
    monkeypatch.setattr(tr, "record_step_attempt", counted)

    await _run_step(m)
    counted.assert_awaited_once_with(ISSUE_ID, "wf:5")


async def test_reply_step_source_and_key_both_reach_the_turn(stubbed, monkeypatch):
    """The barrier / wake-up shape: ``_run_reply_turns`` calls a
    ``functools.partial(run_issue_reply_step, source=...)``."""
    m, chat_svc, _, _ = stubbed
    monkeypatch.setattr(m, "current_dbos_step_key", lambda: "wf:5")
    monkeypatch.setattr(tr, "record_step_attempt", AsyncMock(return_value=1))
    source = {"kind": "subissue_barrier", "created_by": "agent"}

    run_turn = functools.partial(m.run_issue_reply_step.__wrapped__, source=source)
    await run_turn(
        issue_id=ISSUE_ID,
        session_id=SESSION_ID,
        user_id=USER,
        reply_text="children done",
        attachments=None,
    )
    kwargs = chat_svc.run_session_turn.await_args.kwargs
    assert kwargs["message_source"] == source
    assert kwargs["dbos_step_key"] == "wf:5"


async def test_resuming_reply_that_hits_the_cap_parks_blocked_with_typed_prefix():
    """No new routing: the existing resume ``except`` parks the issue blocked
    and the typed code rides in the message prefix."""
    from app.workflows import issue_lifecycle as m

    set_status = AsyncMock(return_value=True)
    release = AsyncMock()
    run_turn = AsyncMock(side_effect=tr.IssueTurnRecoveryLimitExceeded(1, "wf:5", 4))

    with pytest.raises(tr.IssueTurnRecoveryLimitExceeded):
        await m._run_reply_turns(
            1,
            USER,
            "ping",
            session_id=SESSION_ID,
            acquire=AsyncMock(return_value=True),
            run_turn=run_turn,
            release=release,
            sleep=AsyncMock(),
            load_issue=AsyncMock(
                return_value={
                    "id": 1,
                    "status": "needs_followup",
                    "execution_state": '{"agent_outcome": "needs_input"}',
                }
            ),
            set_status=set_status,
        )
    blocked = set_status.await_args_list[-1]
    assert blocked.args == (1, "blocked")
    assert blocked.kwargs["error_code"] == "issue_reply_resume_failed"
    assert blocked.kwargs["error_message"].startswith("[issue_turn_recovery_limit]")
    release.assert_awaited_once_with(1)


# Workflow bodies (and the helpers they inline) are hashed into the DBOS app
# version; this fix must live only inside the step. If one of these changes on
# purpose, update the hash in the same PR and say why in its description.
_PINNED_SOURCES = {
    "respond_to_issue_reply": "331838c6e2f8eea5ffef1a5de45b2bd23c7e17e5932870eb88175b67775883d9",
    "execute_issue": "a5f3f2460c673b834e83449f5b812fb7de9112b954cc4365af0aff79d3744ad6",
    "_run_reply_turns": "6d1bd3ecbc13cde09741c8d0ce65526ff553e902368f88926fe315861a6b83c9",
    "run_issue_reply_for_wait": "750f359404f04bd6646376f055b9c7b6c29be6f994af9ed6948995e618609647",
}


@pytest.mark.parametrize("fn_name", sorted(_PINNED_SOURCES))
def test_workflow_side_sources_are_untouched(fn_name):
    from app.workflows import issue_lifecycle as m

    source = inspect.getsource(getattr(m, fn_name))
    assert hashlib.sha256(source.encode()).hexdigest() == _PINNED_SOURCES[fn_name]
