"""Phase 2a Task 3: NeedsInputItem carries the typed question from the marker."""

import pytest

from app.api.issues_router import _needs_input_item
from app.schemas.issue import NeedsInputItem

pytestmark = pytest.mark.unit


def _row(**state):
    return {
        "id": 1,
        "title": "t",
        "project_id": None,
        "team_id": None,
        "updated_at": "2026-09-08T00:00:00Z",
        "assignee_agent_id": None,
        "identifier": "N-1",
        "execution_state": state,
    }


def test_item_maps_marker_question_fields():
    item = _needs_input_item(
        _row(
            outcome_reason="Which?",
            awaiting_input={
                "prompt": "Which?",
                "question_id": "q:1:2",
                "kind": "user",
                "options": [{"label": "A", "description": None}],
                "allow_free_text": False,
            },
        )
    )
    assert isinstance(item, NeedsInputItem)
    assert item.question == "Which?" and item.question_id == "q:1:2"
    assert item.kind == "user" and item.allow_free_text is False
    assert [o["label"] for o in item.options] == ["A"]


def test_item_without_a_typed_question_keeps_legacy_shape():
    item = _needs_input_item(
        _row(outcome_reason="why?", awaiting_input={"prompt": "why?"})
    )
    assert item.question == "why?" and item.question_id is None
    assert item.options == [] and item.allow_free_text is True
    assert _needs_input_item(_row()).question_id is None
