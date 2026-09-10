"""agent_runs.issue_id must be set AT CREATION, not backfilled after the turn.

``backfill_issue_id`` runs from ``issue_lifecycle`` only after a turn RETURNS
— a crash, a cancel, or an empty-output turn leaves the row NULL forever, and
``issue_fork`` then has to reverse-derive the issue from conversation_id.

Every assertion here is BEHAVIOURAL: the value is followed to the object that
consumes it (RunRecorder's constructor kwargs, build_agent_runner_stack's
kwargs, run_session_turn's kwargs). Source-text assertions were written first
and rejected in review — commenting out a forwarding line left them all green,
and any black reflow would turn them red while the code was correct.
"""

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from app.services.ai.chat import ai_library_chat_service as svc_mod
from tests.test_parity_gap_coverage import (
    _chat_env,
    _FakeStore,
    _RunRecorderCM,
    _session_row,
)

pytestmark = pytest.mark.unit


async def _turn(**turn_kwargs) -> tuple[dict, AsyncMock]:
    """One real ``run_session_turn`` with the LLM stack stubbed. Returns the
    kwargs RunRecorder was constructed with, plus the patched
    ``build_agent_runner_stack`` so its kwargs can be inspected too."""
    user_id, agent_id = uuid4(), uuid4()
    store = _FakeStore(_session_row(user_id, agent_id))
    seen: dict = {}
    with _chat_env(
        run_turn_result={"content": "ok", "tool_calls": []}, agent_id=agent_id
    ) as env_recorder:

        def _rr(**kw):
            seen.update(kw)
            return _RunRecorderCM(env_recorder)

        with patch.object(svc_mod, "RunRecorder", side_effect=_rr):
            await svc_mod.AILibraryChatService(store=store).run_session_turn(
                uuid4(),
                user_id=user_id,
                content="go",
                trigger="issue_dispatch",
                **turn_kwargs,
            )
        return seen, svc_mod.build_agent_runner_stack


async def test_issue_id_reaches_the_recorder_at_creation():
    seen, _ = await _turn(issue_id=9)
    assert seen["issue_id"] == 9


async def test_a_string_issue_id_is_coerced_to_int():
    """Issue ids travel as strings across some seams (Snowflake BIGINT), and
    the column is BIGINT — the recorder must not be handed a string."""
    seen, _ = await _turn(issue_id="9")
    assert seen["issue_id"] == 9 and isinstance(seen["issue_id"], int)


async def test_a_plain_chat_turn_leaves_the_column_null():
    """Negative control: without it, ``issue_id`` could be hardcoded to 9 and
    the positive test above would still pass."""
    seen, _ = await _turn()
    assert seen["issue_id"] is None


async def test_issue_id_reaches_the_runner_stack_so_subruns_inherit():
    """chat service → wiring is the hop that gives every SPAWNED sub-run its
    issue. Dropping this one kwarg silently returns every sub-run to NULL and
    nothing else in the suite notices."""
    _, stack = await _turn(issue_id=9)
    assert stack.await_args.kwargs["issue_id"] == 9


async def test_the_stack_is_not_handed_an_issue_for_a_plain_chat_turn():
    _, stack = await _turn()
    assert stack.await_args.kwargs["issue_id"] is None


# ── The two issue callers that must supply it ──────────────────────────────


async def test_the_issue_dispatch_path_supplies_the_issue_id(monkeypatch):
    from app.services.issues import issue_agent_executor as m

    seen: dict = {}

    async def _turn_stub(session_id, **kw):
        seen.update(kw)
        return {"assistant_message": {"content": "done"}, "run_id": "r"}

    monkeypatch.setattr(
        m, "get_or_create_issue_session", AsyncMock(return_value="sess-1")
    )
    monkeypatch.setattr(
        m,
        "AILibraryChatService",
        lambda: type("C", (), {"run_session_turn": staticmethod(_turn_stub)})(),
    )
    monkeypatch.setattr(
        m, "attempt_forced_finish_declaration", AsyncMock(return_value=(None, None))
    )
    monkeypatch.setattr(m, "publish_chunk", AsyncMock())
    monkeypatch.setattr(m, "publish_message", AsyncMock())
    monkeypatch.setattr(m, "publish_status", AsyncMock())

    await m.run_issue_agent(
        issue={"id": 42, "title": "do thing", "description": "d"},
        agent_id="a",
        user_id="u",
    )
    assert seen["issue_id"] == 42


async def test_the_issue_reply_path_supplies_the_issue_id(monkeypatch):
    """The wake-a-turn-from-a-comment path. It has its own trigger
    (``issue_reply``) and its own DBOS step, and it is just as much an issue
    run as the dispatch path — a reply turn that never returns leaves exactly
    the NULL this whole change exists to stop."""
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
        user_id="11111111-1111-1111-1111-111111111111",
        reply_text="ping",
    )
    assert seen["issue_id"] == 42


# ── Sub-run inheritance (spec §2.5) ────────────────────────────────────────


class _Recorded(Exception):
    """Stop the spawn the instant RunRecorder has been constructed — the
    kwargs are the whole point and running the child turn is not."""


async def test_a_spawned_subagent_stamps_its_parents_issue_on_its_own_run():
    """A sub-run belongs to the same issue as the run that spawned it, or the
    issue's run tree has a hole in it on every fan-out."""
    from app.services.ai.runner.subagent_task_service import SubAgentTaskService

    service = SubAgentTaskService(
        caller_agent_id=uuid4(),
        caller_user_id=uuid4(),
        parent_run_id="1",
        session_id="55",
        issue_id=9,
    )

    seen: dict = {}

    def _rr(**kw):
        seen.update(kw)
        raise _Recorded()

    composed = MagicMock()
    composed.agent_id = uuid4()
    composed.model = "qwen-max"
    composer = MagicMock()
    composer.compose = AsyncMock(return_value=composed)

    agent_repo = MagicMock()
    agent_repo.get_by_slug = AsyncMock(
        return_value={"id": str(uuid4()), "slug": "researcher", "model": "qwen-max"}
    )

    stack = AsyncMock(return_value=MagicMock())
    with (
        patch(
            "app.repositories.agent_repository.get_agent_repository",
            return_value=agent_repo,
        ),
        patch("app.repositories.skill_repository.get_skill_repository"),
        patch(
            "app.services.ai.chat.ai_library_chat_wiring.build_agent_runner_stack",
            stack,
        ),
        patch(
            "app.services.ai.prompts.prompt_composer.PromptComposer",
            return_value=composer,
        ),
        patch(
            "app.services.ai.scope.scope_binding.resolve_dispatch_scope",
            AsyncMock(return_value=_Scope()),
        ),
        patch("app.services.ai.runner.run_recorder.RunRecorder", side_effect=_rr),
    ):
        out = await service._spawn({"subagent_type": "researcher", "prompt": "p"})

    # The child's OWN run row carries the issue …
    assert seen["issue_id"] == 9
    # … and so would a grandchild, because the inner stack inherits it too.
    assert stack.await_args.kwargs["issue_id"] == 9
    # _Recorded is swallowed into a failed envelope, never raised at the parent.
    assert out["status"] == "failed"


class _Scope:
    def as_recorder_kwargs(self):
        return {"project_id": None, "episode_id": None}
