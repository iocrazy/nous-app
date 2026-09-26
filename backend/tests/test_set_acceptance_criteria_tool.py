"""SetAcceptanceCriteria: schema, per-turn handler rules, runner dispatch."""

import re
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from app.services.ai.tools.set_acceptance_criteria_tool import (
    ACCEPTANCE_CRITERIA_MAX_CHARS,
    SET_ACCEPTANCE_CRITERIA_TOOL_NAME,
    make_set_acceptance_criteria_handler,
    set_acceptance_criteria_spec,
)

pytestmark = pytest.mark.unit  # asyncio_mode = "auto" runs the async tests

ISSUE = 77
AGENT = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"

RUNNER_SRC = Path("app/services/ai/runner/agent_runner.py")
CHAT_SRC = Path("app/services/ai/chat/ai_library_chat_service.py")


def test_spec_names_tool_and_requires_criteria():
    spec = set_acceptance_criteria_spec()
    assert (
        spec["function"]["name"]
        == SET_ACCEPTANCE_CRITERIA_TOOL_NAME
        == "SetAcceptanceCriteria"
    )
    assert spec["function"]["parameters"]["required"] == ["criteria"]


@pytest.fixture
def db(monkeypatch):
    from app.services.ai.tools import set_acceptance_criteria_tool as m

    load = AsyncMock(return_value=(None, None))
    write = AsyncMock()
    monkeypatch.setattr(m, "load_acceptance_criteria", load)
    monkeypatch.setattr(m, "write_agent_criteria", write)
    # Review fix: agent criteria lock once verification started this dispatch.
    from app.services.issues import verification as v

    monkeypatch.setattr(v, "verification_started", AsyncMock(return_value=False))
    return load, write


async def test_writes_when_issue_has_none(db):
    load, write = db
    h = make_set_acceptance_criteria_handler(issue_id=ISSUE, agent_id=AGENT)
    out = await h({"criteria": "Scenes 1-2 each get 2 shots"}, None)
    assert out == {
        "ok": True,
        "criteria": "Scenes 1-2 each get 2 shots",
        "source": "agent",
    }
    write.assert_awaited_once_with(ISSUE, "Scenes 1-2 each get 2 shots", agent_id=AGENT)


async def test_user_criteria_are_locked(db):
    load, write = db
    load.return_value = ("Human wrote this", "user")
    h = make_set_acceptance_criteria_handler(issue_id=ISSUE, agent_id=AGENT)
    out = await h({"criteria": "mine"}, None)
    assert out == {"error": "criteria_locked", "criteria": "Human wrote this"}
    write.assert_not_awaited()


async def test_agent_criteria_may_be_replaced_by_the_agent(db):
    load, write = db
    load.return_value = ("old proposal", "agent")
    h = make_set_acceptance_criteria_handler(issue_id=ISSUE, agent_id=AGENT)
    assert (await h({"criteria": "new proposal"}, None))["ok"] is True


async def test_second_call_in_the_same_turn_is_refused(db):
    h = make_set_acceptance_criteria_handler(issue_id=ISSUE, agent_id=AGENT)
    await h({"criteria": "a"}, None)
    out = await h({"criteria": "b"}, None)
    assert out == {"error": "criteria_already_set", "criteria": "a"}


async def test_empty_and_oversized_are_typed_errors(db):
    h = make_set_acceptance_criteria_handler(issue_id=ISSUE, agent_id=AGENT)
    assert await h({"criteria": "  "}, None) == {"error": "criteria_required"}
    out = await h({"criteria": "x" * (ACCEPTANCE_CRITERIA_MAX_CHARS + 1)}, None)
    assert out == {
        "error": "criteria_too_long",
        "max_chars": ACCEPTANCE_CRITERIA_MAX_CHARS,
    }


async def test_write_failure_is_a_typed_error_not_a_raise(db):
    load, write = db
    write.side_effect = RuntimeError("db down")
    h = make_set_acceptance_criteria_handler(issue_id=ISSUE, agent_id=AGENT)
    assert await h({"criteria": "a"}, None) == {"error": "criteria_write_failed"}


async def test_runner_dispatches_by_name_without_a_handler_is_typed():
    from app.services.ai.runner.agent_runner import AgentRunner

    runner = AgentRunner.__new__(AgentRunner)
    runner.set_acceptance_criteria_handler = None
    out = await runner._dispatch_set_acceptance_criteria({"criteria": "a"}, None)
    assert "error" in out and "SetAcceptanceCriteria" in out["error"]


def test_the_criteria_sentence_is_its_own_constant_not_in_the_core():
    """The core FinishIssue instruction is also the forced-declaration
    request's system message, whose tools hold only FinishIssue — so the
    sentence naming SetAcceptanceCriteria must live outside it."""
    from app.services.ai.tools.finish_issue_tool import (
        ACCEPTANCE_CRITERIA_INSTRUCTION,
        FINISH_ISSUE_INSTRUCTION,
    )

    assert "SetAcceptanceCriteria" in ACCEPTANCE_CRITERIA_INSTRUCTION
    assert "SetAcceptanceCriteria" not in FINISH_ISSUE_INSTRUCTION


# ── wiring (same shape as tests/runner/test_schedule_wakeup_wiring.py) ──


def test_the_tool_is_supported_by_the_runner():
    """Without this the runner answers 'unsupported tool' before dispatch."""
    from app.services.ai.runner.agent_runner import SUPPORTED_TOOLS

    assert SET_ACCEPTANCE_CRITERIA_TOOL_NAME in SUPPORTED_TOOLS


def test_both_tool_ladders_dispatch_it():
    src = RUNNER_SRC.read_text(encoding="utf-8")
    hits = re.findall(r"elif tool_name == SET_ACCEPTANCE_CRITERIA_TOOL_NAME:", src)
    assert len(hits) == 2, "must be in the stream AND the non-stream ladder"
    assert src.count("self._dispatch_set_acceptance_criteria(") == 2


def test_the_chat_service_advertises_it_only_on_the_issue_road():
    """Inside the issue-trigger block, at the same depth as ScheduleWakeup."""
    src = CHAT_SRC.read_text(encoding="utf-8")
    sac = src.index("set_acceptance_criteria_spec()")
    sw = src.index("schedule_wakeup_spec()")
    sac_line = src[:sac].rsplit("\n", 1)[-1]
    sw_line = src[:sw].rsplit("\n", 1)[-1]
    assert len(sac_line) - len(sac_line.lstrip()) == len(sw_line) - len(
        sw_line.lstrip()
    )


# ── review fixes: lock once verified; the UPDATE refuses a user-owned row ──


async def test_agent_criteria_lock_once_verification_started(db, monkeypatch):
    from app.services.issues import verification as v

    load, write = db
    load.return_value = ("old proposal", "agent")
    monkeypatch.setattr(v, "verification_started", AsyncMock(return_value=True))
    h = make_set_acceptance_criteria_handler(issue_id=ISSUE, agent_id=AGENT)
    out = await h({"criteria": "moved goalposts"}, None)
    assert out == {
        "error": "criteria_locked",
        "reason": "verification_started",
        "criteria": "old proposal",
    }
    write.assert_not_awaited()


async def test_agent_criteria_write_when_verification_not_started(db):
    load, write = db
    load.return_value = ("old proposal", "agent")
    h = make_set_acceptance_criteria_handler(issue_id=ISSUE, agent_id=AGENT)
    assert (await h({"criteria": "new"}, None))["ok"] is True
    write.assert_awaited_once()


async def test_a_user_patch_racing_the_write_is_criteria_locked(db):
    from app.services.issues.acceptance_criteria import CriteriaLockedError

    load, write = db
    write.side_effect = CriteriaLockedError("user owns the criteria")
    h = make_set_acceptance_criteria_handler(issue_id=ISSUE, agent_id=AGENT)
    out = await h({"criteria": "a"}, None)
    assert out == {"error": "criteria_locked", "reason": "set_by_user"}


def _capturing_write_scope(monkeypatch, rowcount):
    from contextlib import asynccontextmanager

    from sqlalchemy.dialects import postgresql

    compiled: list[str] = []

    class _Res:
        def __init__(self):
            self.rowcount = rowcount

    class _Session:
        async def execute(self, stmt):
            compiled.append(str(stmt.compile(dialect=postgresql.dialect())))
            return _Res()

    @asynccontextmanager
    async def _scope():
        yield _Session()

    import app.db.session as db_session

    monkeypatch.setattr(db_session, "write_scope", _scope)
    return compiled


async def test_write_agent_criteria_update_skips_user_owned_rows(monkeypatch):
    from app.services.issues import acceptance_criteria as ac

    compiled = _capturing_write_scope(monkeypatch, rowcount=1)
    await ac.write_agent_criteria(ISSUE, "c", agent_id=None)
    assert "UPDATE public.issues" in compiled[0]
    assert "acceptance_criteria_source IS DISTINCT FROM" in compiled[0]


async def test_write_agent_criteria_raises_typed_when_nothing_updated(monkeypatch):
    from app.services.issues import acceptance_criteria as ac

    _capturing_write_scope(monkeypatch, rowcount=0)
    with pytest.raises(ac.CriteriaLockedError):
        await ac.write_agent_criteria(ISSUE, "c", agent_id=AGENT)
