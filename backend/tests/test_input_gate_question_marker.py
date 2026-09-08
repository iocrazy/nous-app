"""Phase 2a Task 3: the awaiting_input marker carries the typed question."""

import pytest

from app.agent_framework.input_gate import build_awaiting_marker

pytestmark = pytest.mark.unit


def test_marker_without_question_is_the_pre_2a_shape():
    m = build_awaiting_marker(prompt="why?", issue_id=5, now="T")
    assert m == {"prompt": "why?", "since": "T", "issue_id": 5}


def test_marker_with_question_adds_typed_fields_and_run_id():
    m = build_awaiting_marker(
        prompt="Which?",
        issue_id=5,
        now="T",
        question={
            "question_id": "q:7:2",
            "kind": "user",
            "prompt": "Which?",
            "options": [{"label": "A", "description": None}],
            "allow_free_text": False,
            "asked_at": "T0",
            "run_id": "7",
            "junk": "dropped",
        },
    )
    assert m["question_id"] == "q:7:2" and m["kind"] == "user"
    assert m["options"] == [{"label": "A", "description": None}]
    assert m["allow_free_text"] is False and m["run_id"] == "7"
    assert "junk" not in m and m["prompt"] == "Which?"
