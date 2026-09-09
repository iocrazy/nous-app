"""``tool_call`` fold → ``view.tools`` (phase 2b-1 §3)."""

import pytest

from app.services.ai.runner.run_projection import apply, empty_views

pytestmark = pytest.mark.unit


def _timeout(tool):
    return {"tool": tool, "args": {}, "result": {"error": "timeout", "timed_out": True}}


def test_a_timeout_counts_and_names_the_tool():
    v = apply(empty_views(), "tool_call", _timeout("ResourceFetch"))
    assert v["view"]["tools"] == {"timed_out": 1, "last_timed_out": "ResourceFetch"}


def test_a_plain_error_or_success_does_not_move_the_gauge():
    e = empty_views()
    v = apply(
        e,
        "tool_call",
        {"tool": "Skill", "result": {"error": "bad", "timed_out": False}},
    )
    assert v is e or v["view"]["tools"] == {"timed_out": 0, "last_timed_out": None}
    v = apply(e, "tool_call", {"tool": "Skill", "result": {"ok": True}})
    assert v is e or v["view"]["tools"] == {"timed_out": 0, "last_timed_out": None}


def test_timeouts_accumulate_and_last_follows_the_newest():
    v = apply(empty_views(), "tool_call", _timeout("ResourceFetch"))
    v = apply(v, "tool_call", _timeout("Skill"))
    assert v["view"]["tools"] == {"timed_out": 2, "last_timed_out": "Skill"}


def test_empty_views_carries_the_gauge():
    assert empty_views()["view"]["tools"] == {"timed_out": 0, "last_timed_out": None}
