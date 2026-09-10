"""ScheduleWakeup reaches the model on the issue road and nowhere else.

Two halves, both worth pinning: the RUNNER must dispatch the tool on both
ladders (the streaming one is the only path production takes — see the
``stream_turn`` buffered-fallback note in CLAUDE.md), and the CHAT SERVICE
must advertise it only inside the issue-trigger block, next to FinishIssue.
"""

import re
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from app.services.ai.tools.schedule_wakeup_tool import (
    SCHEDULE_WAKEUP_TOOL_NAME,
    schedule_wakeup_spec,
)

pytestmark = pytest.mark.unit

RUNNER_SRC = Path("app/services/ai/runner/agent_runner.py")
CHAT_SRC = Path("app/services/ai/chat/ai_library_chat_service.py")


def test_the_tool_is_supported_by_the_runner():
    from app.services.ai.runner.agent_runner import SUPPORTED_TOOLS

    assert SCHEDULE_WAKEUP_TOOL_NAME in SUPPORTED_TOOLS


def test_both_tool_ladders_dispatch_schedule_wakeup():
    src = RUNNER_SRC.read_text(encoding="utf-8")
    hits = re.findall(r"elif tool_name == SCHEDULE_WAKEUP_TOOL_NAME:", src)
    assert (
        len(hits) == 2
    ), "ScheduleWakeup must be in the stream AND the non-stream ladder"
    assert src.count("self._dispatch_schedule_wakeup(") == 2


def test_the_chat_service_advertises_it_only_on_the_issue_road():
    """It sits INSIDE the issue-trigger block (indented deeper than the
    unconditional AskUser injection), so chat turns never see it."""
    src = CHAT_SRC.read_text(encoding="utf-8")
    sw = src.index("schedule_wakeup_spec()")
    au = src.index("[ask_user_spec()]")
    sw_line = src[:sw].rsplit("\n", 1)[-1]
    au_line = src[:au].rsplit("\n", 1)[-1]
    assert len(sw_line) - len(sw_line.lstrip()) > len(au_line) - len(au_line.lstrip())


class _Rec:
    run_id = 7

    async def record_event(self, event_type, payload, *, turn=None, step=None):
        pass


def _runner():
    from app.services.ai.runner.agent_runner import AgentRunner

    class _Tool:
        recorder = None

        async def execute(self, args):
            return {}

    return AgentRunner(adapter=AsyncMock(), skill_tool=_Tool())


@pytest.mark.asyncio
async def test_an_unconfigured_turn_answers_with_a_typed_result():
    """A chat turn that hallucinates the tool gets told it is unavailable —
    an unanswered tool_call would poison the next request."""
    runner = _runner()
    assert runner.schedule_wakeup_handler is None
    out = await runner._dispatch_schedule_wakeup({"note": "x"}, _Rec())
    assert "not available" in out["error"]


@pytest.mark.asyncio
async def test_a_raising_handler_becomes_a_tool_result_not_a_crash():
    runner = _runner()

    async def _boom(args, recorder):
        raise RuntimeError("nope")

    runner.schedule_wakeup_handler = _boom
    out = await runner._dispatch_schedule_wakeup({"note": "x"}, _Rec())
    assert out["error"].startswith("ScheduleWakeup failed")


@pytest.mark.asyncio
async def test_the_recorder_reaches_the_handler():
    """The run id stamped on the row and the transcript the event lands on
    both come from the recorder, which only exists at call time."""
    runner = _runner()
    seen = {}

    async def _handler(args, recorder):
        seen["run_id"] = recorder.run_id
        return {"schedule_id": "s1", "fire_at": "2026-09-11T09:00:00+00:00"}

    runner.schedule_wakeup_handler = _handler
    out = await runner._dispatch_schedule_wakeup({"note": "x"}, _Rec())
    assert seen["run_id"] == 7 and out["schedule_id"] == "s1"


def test_the_spec_is_advertised_verbatim():
    """The description is a stable literal the prompts README quotes."""
    assert schedule_wakeup_spec()["function"]["description"].startswith(
        "Schedule a one-time wake-up for this issue"
    )
