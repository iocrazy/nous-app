"""agent_runs.issue_id must be set AT CREATION, not backfilled after the turn.
backfill_issue_id runs from issue_lifecycle only after a turn RETURNS — a
crash, a cancel, or an empty-output turn leaves the row NULL forever, and
issue_fork then has to reverse-derive the issue from conversation_id."""

import inspect
import pathlib

import pytest

pytestmark = pytest.mark.unit


def test_run_session_turn_accepts_and_forwards_issue_id():
    from app.services.ai.chat.ai_library_chat_service import AILibraryChatService

    for fn in (
        AILibraryChatService.run_session_turn,
        AILibraryChatService._run_session_turn_inner,
    ):
        p = inspect.signature(fn).parameters["issue_id"]
        assert p.default is None and p.kind is inspect.Parameter.KEYWORD_ONLY


def _source(module_name: str) -> str:
    import importlib

    module = importlib.import_module(module_name)
    return pathlib.Path(module.__file__).read_text("utf-8")


def test_the_recorder_and_the_issue_executor_are_actually_wired():
    """Signature alone proves nothing — the value has to reach RunRecorder and
    the issue path has to supply it."""
    chat = _source("app.services.ai.chat.ai_library_chat_service")
    exe = _source("app.services.issues.issue_agent_executor")
    assert "issue_id=int(issue_id) if issue_id else None," in chat
    assert "issue_id=iid," in exe


def test_a_spawned_subagent_inherits_its_parent_runs_issue():
    """spec §2.5: a sub-run belongs to the same issue as the run that spawned
    it, or the issue's own run tree is incomplete on every fan-out."""
    from app.services.ai.runner.subagent_task_service import SubAgentTaskService

    p = inspect.signature(SubAgentTaskService.__init__).parameters["issue_id"]
    assert p.default is None and p.kind is inspect.Parameter.KEYWORD_ONLY
    sub = _source("app.services.ai.runner.subagent_task_service")
    assert "issue_id=self.issue_id," in sub
    wiring = _source("app.services.ai.chat.ai_library_chat_wiring")
    assert "issue_id=issue_id," in wiring
