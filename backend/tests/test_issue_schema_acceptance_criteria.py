"""509: acceptance criteria on the issue read/write models."""

import pytest
from pydantic import ValidationError

from app.schemas.issue import (
    ACCEPTANCE_CRITERIA_MAX_CHARS,
    Issue,
    IssueCreate,
    IssueUpdate,
)

pytestmark = pytest.mark.unit


def _row(**over):
    base = {
        "id": 1,
        "issue_number": 1,
        "identifier": "MH-1",
        "title": "t",
        "created_at": "2026-09-26T00:00:00Z",
        "updated_at": "2026-09-26T00:00:00Z",
    }
    return {**base, **over}


def test_create_accepts_criteria_up_to_the_cap():
    IssueCreate(title="t", acceptance_criteria="x" * ACCEPTANCE_CRITERIA_MAX_CHARS)
    with pytest.raises(ValidationError):
        IssueCreate(
            title="t", acceptance_criteria="x" * (ACCEPTANCE_CRITERIA_MAX_CHARS + 1)
        )


def test_update_has_clear_flag():
    assert IssueUpdate(clear_acceptance_criteria=True).clear_acceptance_criteria is True


def test_read_model_lifts_verification_out_of_execution_state():
    v = {"verdict": "pass", "attempt": 1}
    issue = Issue.model_validate(_row(execution_state={"verification": v}))
    assert issue.verification == v
    assert Issue.model_validate(_row()).verification is None
    # The repository hands execution_state back as a native dict (JSONB), so
    # the shapes to cover are "no key" and "key present but not an object".
    assert Issue.model_validate(_row(execution_state={})).verification is None
    bad = _row(execution_state={"verification": "pass"})
    assert Issue.model_validate(bad).verification is None


def test_read_model_exposes_source():
    issue = Issue.model_validate(
        _row(acceptance_criteria="two shots", acceptance_criteria_source="agent")
    )
    assert issue.acceptance_criteria == "two shots"
    assert issue.acceptance_criteria_source == "agent"
