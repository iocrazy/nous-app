"""Phase 2a Task 3: the AskUser tool spec + handler, and FinishIssue.options.

The option sub-schema is ONE constant (``question.OPTIONS_JSON_SCHEMA``)
shared by both tools so the model sees the same shape in both places."""

import pytest

from app.services.ai.runner import question as q
from app.services.ai.tools.ask_user_tool import (
    ASK_USER_TOOL_NAME,
    ask_user_handler,
    ask_user_spec,
    awaiting_input_outcome,
)
from app.services.ai.tools.finish_issue_tool import (
    extract_issue_options,
    finish_issue_handler,
    finish_issue_spec,
)

pytestmark = pytest.mark.unit


class _Rec:
    def __init__(self, run_id=42):
        self.run_id = run_id
        self.events = []
        self.views = {"view": {"question": None}}

    async def record_event(self, event_type, payload, *, turn=None, step=None):
        self.events.append((event_type, payload, turn, step))


def test_ask_user_schema_declares_question_options_free_text():
    spec = ask_user_spec()
    assert spec["type"] == "function"
    assert spec["function"]["name"] == ASK_USER_TOOL_NAME == "AskUser"
    p = spec["function"]["parameters"]
    assert set(p["properties"]) == {"question", "options", "allow_free_text"}
    assert p["required"] == ["question"]
    assert p["properties"]["question"]["maxLength"] == q.PROMPT_MAX
    opts = p["properties"]["options"]
    assert opts["maxItems"] == q.MAX_OPTIONS
    assert opts["items"]["properties"]["label"]["maxLength"] == q.LABEL_MAX
    assert opts["items"]["properties"]["description"]["maxLength"] == q.DESC_MAX
    assert opts["items"]["required"] == ["label"]
    assert p["properties"]["allow_free_text"]["default"] is True


def test_finish_issue_schema_gained_options_with_the_same_sub_schema():
    p = finish_issue_spec()["function"]["parameters"]
    assert "options" in p["properties"]
    assert p["properties"]["options"] is q.OPTIONS_JSON_SCHEMA
    assert ask_user_spec()["function"]["parameters"]["properties"]["options"] is (
        q.OPTIONS_JSON_SCHEMA
    )


async def test_ask_user_handler_records_question_asked_and_reports_id():
    rec = _Rec()
    out = await ask_user_handler(
        {"question": "Which ending?", "options": [{"label": "Twist"}]},
        recorder=rec,
        turn=1,
        step=3,
    )
    assert out == {"asked": True, "question_id": "q:42:3", "warnings": []}
    et, payload, turn, step = rec.events[-1]
    assert et == "question_asked" and payload["prompt"] == "Which ending?"
    assert payload["options"] == [{"label": "Twist", "description": None}]
    assert (turn, step) == (1, 3)


async def test_ask_user_handler_degrades_bad_options_and_reports_warnings():
    rec = _Rec()
    out = await ask_user_handler(
        {"question": "?", "options": [{"label": "A"}, {"label": "A"}]},
        recorder=rec,
        turn=1,
        step=1,
    )
    assert out["asked"] is True and out["warnings"]
    assert rec.events[-1][1]["options"] == []
    assert rec.events[-1][1]["allow_free_text"] is True


async def test_ask_user_handler_never_raises_on_missing_question_or_recorder():
    out = await ask_user_handler({}, recorder=_Rec(), turn=1, step=1)
    assert out["asked"] is False and "question" in out["error"]
    out = await ask_user_handler({"question": "?"}, recorder=None, turn=1, step=1)
    assert out["asked"] is False and out["error"]


async def test_finish_issue_handler_passes_options_through():
    out = await finish_issue_handler(
        {"outcome": "needs_input", "reason": "pick", "options": [{"label": "A"}]}
    )
    assert out["acknowledged"] is True and out["options"] == [{"label": "A"}]
    out = await finish_issue_handler({"outcome": "completed"})
    assert "options" not in out


def test_extract_issue_options_reads_the_last_needs_input_declaration():
    trace = [
        {
            "name": "FinishIssue",
            "args": {"outcome": "needs_input", "options": [{"label": "A"}]},
            "result": {
                "acknowledged": True,
                "outcome": "needs_input",
                "options": [{"label": "A"}],
            },
        }
    ]
    assert extract_issue_options(trace) == [{"label": "A"}]
    assert (
        extract_issue_options(
            [{"name": "FinishIssue", "args": {"outcome": "completed"}}]
        )
        is None
    )
    assert extract_issue_options(None) is None


def test_awaiting_input_outcome_turns_a_parked_turn_into_needs_input():
    qd = {"question_id": "q:1:2", "kind": "user", "prompt": "Which?", "options": []}
    assert awaiting_input_outcome({"awaiting_input": True, "question": qd}) == (
        "needs_input",
        "Which?",
        qd,
    )
    assert awaiting_input_outcome({"content": "x"}) is None
