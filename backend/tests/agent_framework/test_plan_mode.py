"""L4 — PlanMode primitive: parse / render / approval."""

from __future__ import annotations

import json

import pytest

from app.agent_framework.plan_mode import (
    MAX_DESCRIPTION_LEN,
    MAX_STEPS,
    MAX_SUMMARY_LEN,
    ApprovalDecision,
    PlanMode,
    PlanStep,
    PlanValidationError,
    ProposedPlan,
    build_plan_prompt,
    parse_approval,
    parse_plan_response,
    render_plan_for_user,
)

_VALID_PLAN_JSON = json.dumps(
    {
        "summary": "Refactor the auth module + run tests.",
        "steps": [
            {
                "id": 1,
                "description": "Read auth.py and identify duplication",
                "tool": None,
                "args_summary": None,
                "side_effects": [],
            },
            {
                "id": 2,
                "description": "Extract common code into auth_helpers.py",
                "tool": "Skill",
                "args_summary": "Edit 2 files",
                "side_effects": ["modify auth.py", "create auth_helpers.py"],
            },
            {
                "id": 3,
                "description": "Run full test suite",
                "tool": "Skill",
                "args_summary": "pytest",
                "side_effects": ["spawn subprocess pytest"],
            },
        ],
        "estimated_cost_usd": 0.05,
        "estimated_seconds": 60,
        "risks": ["may break existing imports"],
    }
)


# ─── PlanMode enum ────────────────────────────────────────────────────


@pytest.mark.unit
def test_plan_mode_values():
    assert PlanMode.AUTO.value == "auto"
    assert PlanMode.PROMPT_USER.value == "prompt_user"
    assert PlanMode.DRY_RUN.value == "dry_run"


# ─── parse_plan_response ──────────────────────────────────────────────


@pytest.mark.unit
def test_parse_valid_plan():
    plan = parse_plan_response(_VALID_PLAN_JSON)
    assert isinstance(plan, ProposedPlan)
    assert plan.summary.startswith("Refactor")
    assert len(plan.steps) == 3
    assert plan.steps[0].id == 1
    assert plan.estimated_cost_usd == 0.05
    assert plan.risks == ("may break existing imports",)


@pytest.mark.unit
def test_parse_strips_markdown_fence():
    wrapped = "```json\n" + _VALID_PLAN_JSON + "\n```"
    plan = parse_plan_response(wrapped)
    assert len(plan.steps) == 3


@pytest.mark.unit
def test_parse_extracts_from_trailing_prose():
    """LLM ignored 'no commentary' rule — extract anyway."""
    chatty = "Sure, here's the plan:\n" + _VALID_PLAN_JSON + "\nLet me know."
    plan = parse_plan_response(chatty)
    assert plan.summary.startswith("Refactor")


@pytest.mark.unit
def test_parse_empty_raises():
    with pytest.raises(PlanValidationError, match="empty"):
        parse_plan_response("")
    with pytest.raises(PlanValidationError, match="empty"):
        parse_plan_response("   ")


@pytest.mark.unit
def test_parse_garbled_raises():
    with pytest.raises(PlanValidationError):
        parse_plan_response("not json at all")


@pytest.mark.unit
def test_parse_missing_summary():
    bad = json.dumps({"steps": [{"id": 1, "description": "x"}]})
    with pytest.raises(PlanValidationError, match="summary"):
        parse_plan_response(bad)


@pytest.mark.unit
def test_parse_empty_steps_raises():
    bad = json.dumps({"summary": "x", "steps": []})
    with pytest.raises(PlanValidationError, match="steps"):
        parse_plan_response(bad)


@pytest.mark.unit
def test_parse_steps_must_be_sequential():
    bad = json.dumps(
        {
            "summary": "x",
            "steps": [
                {"id": 1, "description": "a"},
                {"id": 3, "description": "c"},  # skipped 2
            ],
        }
    )
    with pytest.raises(PlanValidationError, match="sequential"):
        parse_plan_response(bad)


@pytest.mark.unit
def test_parse_too_many_steps():
    bad = json.dumps(
        {
            "summary": "x",
            "steps": [
                {"id": i, "description": f"step {i}"} for i in range(1, MAX_STEPS + 2)
            ],
        }
    )
    with pytest.raises(PlanValidationError, match="too many"):
        parse_plan_response(bad)


@pytest.mark.unit
def test_parse_oversized_summary():
    bad = json.dumps(
        {
            "summary": "x" * (MAX_SUMMARY_LEN + 1),
            "steps": [{"id": 1, "description": "x"}],
        }
    )
    with pytest.raises(PlanValidationError, match="summary too long"):
        parse_plan_response(bad)


@pytest.mark.unit
def test_parse_oversized_description():
    bad = json.dumps(
        {
            "summary": "x",
            "steps": [{"id": 1, "description": "x" * (MAX_DESCRIPTION_LEN + 1)}],
        }
    )
    with pytest.raises(PlanValidationError, match="description too long"):
        parse_plan_response(bad)


@pytest.mark.unit
def test_parse_invalid_cost_silently_dropped():
    """LLM might emit 'estimated_cost_usd': '0.05' (string) — accept by
    coercing; on failure silently drop to None."""
    val = json.dumps(
        {
            "summary": "x",
            "steps": [{"id": 1, "description": "x"}],
            "estimated_cost_usd": "garbage",
        }
    )
    plan = parse_plan_response(val)
    assert plan.estimated_cost_usd is None


# ─── render_plan_for_user ─────────────────────────────────────────────


@pytest.mark.unit
def test_render_includes_summary_and_steps():
    plan = parse_plan_response(_VALID_PLAN_JSON)
    md = render_plan_for_user(plan)
    assert "Refactor" in md
    assert "1." in md
    assert "2." in md
    assert "3." in md
    assert "$0.050" in md
    assert "may break existing imports" in md
    assert "approve" in md.lower()


@pytest.mark.unit
def test_render_minimal_plan():
    minimal = ProposedPlan(
        summary="X",
        steps=(PlanStep(id=1, description="do X"),),
    )
    md = render_plan_for_user(minimal)
    assert "do X" in md
    # No cost/time/risks sections
    assert "$" not in md
    assert "Risks" not in md


@pytest.mark.unit
def test_render_marks_side_effects_visibly():
    plan = parse_plan_response(_VALID_PLAN_JSON)
    md = render_plan_for_user(plan)
    assert "⚠️" in md  # side-effect marker
    assert "modify auth.py" in md


# ─── parse_approval ────────────────────────────────────────────────


@pytest.mark.unit
def test_parse_approval_yes_words():
    for word in ["approve", "yes", "y", "ok", "go", "proceed", "approved"]:
        assert parse_approval(word) == ApprovalDecision.APPROVE


@pytest.mark.unit
def test_parse_approval_reject_words():
    for word in ["reject", "no", "n", "cancel", "abort", "stop"]:
        assert parse_approval(word) == ApprovalDecision.REJECT


@pytest.mark.unit
def test_parse_approval_case_insensitive():
    assert parse_approval("APPROVE") == ApprovalDecision.APPROVE
    assert parse_approval("Yes!") == ApprovalDecision.APPROVE


@pytest.mark.unit
def test_parse_approval_strips_punctuation():
    assert parse_approval("approve.") == ApprovalDecision.APPROVE
    assert parse_approval("yes,") == ApprovalDecision.APPROVE


@pytest.mark.unit
def test_parse_approval_takes_first_word():
    """User: 'yes go ahead' → approve."""
    assert parse_approval("yes go ahead") == ApprovalDecision.APPROVE


@pytest.mark.unit
def test_parse_approval_unclear_for_other_text():
    """Anything ambiguous → ask again rather than guess."""
    assert parse_approval("maybe later") == ApprovalDecision.UNCLEAR
    assert parse_approval("hmm") == ApprovalDecision.UNCLEAR
    assert parse_approval("") == ApprovalDecision.UNCLEAR
    assert parse_approval(None) == ApprovalDecision.UNCLEAR  # type: ignore[arg-type]


# ─── build_plan_prompt ────────────────────────────────────────────────


@pytest.mark.unit
def test_build_plan_prompt_includes_max_steps():
    p = build_plan_prompt()
    assert "PLAN MODE" in p
    assert str(MAX_STEPS) in p
    assert "JSON" in p
