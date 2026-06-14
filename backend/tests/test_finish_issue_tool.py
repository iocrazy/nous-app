"""FinishIssue tool — the agent's self-reported issue-turn outcome (Spec-2).

Covers the model-facing spec, the validating handler, and the trace extractor
that the execute_issue workflow uses to drive status + continuation.
"""

from __future__ import annotations

import pytest

from app.services.ai.tools.finish_issue_tool import (
    FINISH_ISSUE_OUTCOMES,
    FINISH_ISSUE_TOOL_NAME,
    extract_issue_outcome,
    finish_issue_handler,
    finish_issue_spec,
)


def test_spec_advertises_enum_and_name():
    spec = finish_issue_spec()
    assert spec["function"]["name"] == FINISH_ISSUE_TOOL_NAME
    enum = spec["function"]["parameters"]["properties"]["outcome"]["enum"]
    assert set(enum) == set(FINISH_ISSUE_OUTCOMES)
    assert spec["function"]["parameters"]["required"] == ["outcome"]


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", FINISH_ISSUE_OUTCOMES)
async def test_handler_accepts_valid_outcomes(outcome):
    res = await finish_issue_handler({"outcome": outcome, "reason": "because"})
    assert res["acknowledged"] is True
    assert res["outcome"] == outcome
    assert res["reason"] == "because"


@pytest.mark.asyncio
async def test_handler_rejects_invalid_outcome():
    res = await finish_issue_handler({"outcome": "lgtm"})
    assert "error" in res
    assert "acknowledged" not in res


@pytest.mark.asyncio
async def test_handler_missing_outcome_is_error_not_raise():
    res = await finish_issue_handler({})
    assert "error" in res


def test_extract_returns_none_when_no_finish_call():
    assert extract_issue_outcome(None) == (None, None)
    assert extract_issue_outcome([]) == (None, None)
    trace = [{"name": "Skill", "args": {"skill": "x"}, "result": {"ok": True}}]
    assert extract_issue_outcome(trace) == (None, None)


def test_extract_reads_last_acknowledged_declaration():
    trace = [
        {
            "name": FINISH_ISSUE_TOOL_NAME,
            "args": {"outcome": "continue"},
            "result": {"acknowledged": True, "outcome": "continue", "reason": "wip"},
        },
        {
            "name": FINISH_ISSUE_TOOL_NAME,
            "args": {"outcome": "completed"},
            "result": {"acknowledged": True, "outcome": "completed", "reason": "done"},
        },
    ]
    assert extract_issue_outcome(trace) == ("completed", "done")


def test_extract_ignores_rejected_declaration():
    trace = [
        {
            "name": FINISH_ISSUE_TOOL_NAME,
            "args": {"outcome": "bogus"},
            "result": {"error": "Invalid outcome"},
        }
    ]
    assert extract_issue_outcome(trace) == (None, None)


def test_extract_falls_back_to_args_when_result_minimal():
    # Some traces may only carry args; still honor a valid declared outcome.
    trace = [
        {
            "name": FINISH_ISSUE_TOOL_NAME,
            "args": {"outcome": "needs_input", "reason": "need API key"},
            "result": {},
        }
    ]
    assert extract_issue_outcome(trace) == ("needs_input", "need API key")
