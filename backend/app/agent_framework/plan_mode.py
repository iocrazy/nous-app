"""PlanMode — explicit plan-then-execute two-phase loop.

Phase L (L4). Without PlanMode, complex agent tasks go straight from
user request to action — the agent guesses what to do, dispatches
tool calls immediately, and the user sees the result + side effects
already applied. Mistakes are expensive (committed to git, sent to
production, deleted files).

PlanMode introduces a checkpoint:
  Phase 1: agent proposes a structured plan (no tool calls allowed)
  Phase 2 (after approval): agent executes the plan

Three approval modes per call:
  - auto: skip checkpoint (back-compat — same as no PlanMode)
  - prompt_user: surface plan to user, wait for approve/reject
  - dry_run: emit plan only, never execute (useful for what-if queries)

This module is the SHAPE-only layer:
  - PlanMode enum
  - ProposedPlan dataclass + validator
  - parse_plan_response(): extract structured plan from LLM output
  - render_plan_for_user(): markdown for UI
  - PLAN_PROMPT_TEMPLATE: system addendum that nudges agent into plan mode

Wiring (chat service / runner integration) is L4.5 / future. The
primitive here is testable end-to-end without DB or LLM.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class PlanMode(str, Enum):
    """How the runner should treat agent plans this turn."""

    AUTO = "auto"  # skip plan checkpoint (back-compat)
    PROMPT_USER = "prompt_user"  # surface plan; wait for explicit approve
    DRY_RUN = "dry_run"  # emit plan only, never execute


class PlanValidationError(ValueError):
    """Plan structure failed validation (missing field, bad type, etc)."""


@dataclass(frozen=True)
class PlanStep:
    """One step in a proposed plan."""

    id: int
    description: str  # what this step does, plain language
    tool: Optional[str] = None  # 'Skill' / 'Delegate' / None for thinking-only
    args_summary: Optional[str] = None  # one-line human-readable args
    side_effects: tuple[str, ...] = field(default_factory=tuple)  # explicit list


@dataclass(frozen=True)
class ProposedPlan:
    """Structured plan returned by agent in plan phase."""

    summary: str  # 1-2 sentence what+why
    steps: tuple[PlanStep, ...]
    estimated_cost_usd: Optional[float] = None
    estimated_seconds: Optional[float] = None
    risks: tuple[str, ...] = field(default_factory=tuple)
    raw_text: Optional[str] = None  # original LLM output for audit


# Constraints — keep plans focused
MIN_STEPS = 1
MAX_STEPS = 20
MAX_SUMMARY_LEN = 500
MAX_DESCRIPTION_LEN = 200


PLAN_PROMPT_TEMPLATE = """\
You are in PLAN MODE. Do NOT execute anything yet. Output a structured
plan for the user to approve.

Output ONLY a JSON object with this shape (no commentary):

{{
  "summary": "1-2 sentence summary of what you'll do and why",
  "steps": [
    {{
      "id": 1,
      "description": "Plain-language what this step does",
      "tool": "Skill" | "Delegate" | null,
      "args_summary": "human-readable argument summary",
      "side_effects": ["files modified", "API call to X", "..."]
    }}
  ],
  "estimated_cost_usd": null | <number>,
  "estimated_seconds": null | <number>,
  "risks": ["thing that could go wrong", "..."]
}}

Rules:
- 1-{max_steps} steps, sequentially numbered from 1.
- side_effects: explicit list of EVERY observable effect (file writes,
  external API calls, DB mutations, notifications). Be conservative.
- If a step is purely thinking / analysis, set tool=null.
- DO NOT use markdown. DO NOT add preamble. JSON only.
"""


def build_plan_prompt() -> str:
    return PLAN_PROMPT_TEMPLATE.format(max_steps=MAX_STEPS)


def parse_plan_response(raw: str) -> ProposedPlan:
    """Parse JSON output into a ProposedPlan.

    Tolerates markdown fences + trailing commentary (LLMs ignore the
    "no preamble" rule sometimes).
    """
    if not isinstance(raw, str) or not raw.strip():
        raise PlanValidationError("empty plan response")

    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
        text = re.sub(r"\n?```$", "", text)

    try:
        decoded = json.loads(text)
    except json.JSONDecodeError:
        # Try to find a JSON object within the text
        match = re.search(r"(\{.*\})", text, re.DOTALL)
        if not match:
            raise PlanValidationError("could not parse JSON from plan response")
        try:
            decoded = json.loads(match.group(1))
        except json.JSONDecodeError as exc:
            raise PlanValidationError(f"malformed JSON: {exc.msg}") from None

    if not isinstance(decoded, dict):
        raise PlanValidationError("plan must be a JSON object")

    summary = str(decoded.get("summary") or "").strip()
    if not summary:
        raise PlanValidationError("plan.summary is required")
    if len(summary) > MAX_SUMMARY_LEN:
        raise PlanValidationError(
            f"summary too long ({len(summary)} > {MAX_SUMMARY_LEN})"
        )

    steps_raw = decoded.get("steps")
    if not isinstance(steps_raw, list) or not steps_raw:
        raise PlanValidationError("plan.steps is required and non-empty")
    if len(steps_raw) > MAX_STEPS:
        raise PlanValidationError(f"too many steps ({len(steps_raw)} > {MAX_STEPS})")

    steps: list[PlanStep] = []
    for i, raw_step in enumerate(steps_raw, start=1):
        if not isinstance(raw_step, dict):
            raise PlanValidationError(f"step {i}: must be an object")
        sid = raw_step.get("id")
        if not isinstance(sid, int) or sid != i:
            raise PlanValidationError(
                f"step {i}: id must be sequential integer (got {sid!r})"
            )
        description = str(raw_step.get("description") or "").strip()
        if not description:
            raise PlanValidationError(f"step {i}: description required")
        if len(description) > MAX_DESCRIPTION_LEN:
            raise PlanValidationError(
                f"step {i}: description too long "
                f"({len(description)} > {MAX_DESCRIPTION_LEN})"
            )
        tool = raw_step.get("tool")
        if tool is not None:
            tool = str(tool)
        args_summary = raw_step.get("args_summary")
        if args_summary is not None:
            args_summary = str(args_summary)
        side_effects_raw = raw_step.get("side_effects") or []
        if not isinstance(side_effects_raw, list):
            raise PlanValidationError(f"step {i}: side_effects must be a list")
        side_effects = tuple(str(s) for s in side_effects_raw)

        steps.append(
            PlanStep(
                id=sid,
                description=description,
                tool=tool,
                args_summary=args_summary,
                side_effects=side_effects,
            )
        )

    cost = decoded.get("estimated_cost_usd")
    cost_f: Optional[float] = None
    if cost is not None:
        try:
            cost_f = float(cost)
        except (TypeError, ValueError):
            cost_f = None

    secs = decoded.get("estimated_seconds")
    secs_f: Optional[float] = None
    if secs is not None:
        try:
            secs_f = float(secs)
        except (TypeError, ValueError):
            secs_f = None

    risks_raw = decoded.get("risks") or []
    if not isinstance(risks_raw, list):
        risks_raw = []
    risks = tuple(str(r) for r in risks_raw)

    return ProposedPlan(
        summary=summary,
        steps=tuple(steps),
        estimated_cost_usd=cost_f,
        estimated_seconds=secs_f,
        risks=risks,
        raw_text=raw,
    )


def render_plan_for_user(plan: ProposedPlan) -> str:
    """Markdown rendering — what the user sees in the UI before approving."""
    lines = [f"## Proposed plan", "", f"**Summary**: {plan.summary}", ""]
    if plan.estimated_cost_usd is not None:
        lines.append(f"**Estimated cost**: ${plan.estimated_cost_usd:.3f}")
    if plan.estimated_seconds is not None:
        lines.append(f"**Estimated time**: ~{plan.estimated_seconds:.0f}s")
    if plan.estimated_cost_usd is not None or plan.estimated_seconds is not None:
        lines.append("")
    lines.append("**Steps**:")
    for s in plan.steps:
        prefix = f"{s.id}. {s.description}"
        if s.tool:
            prefix += f"  _({s.tool}"
            if s.args_summary:
                prefix += f": {s.args_summary}"
            prefix += ")_"
        lines.append(prefix)
        for se in s.side_effects:
            lines.append(f"   - ⚠️ {se}")
    if plan.risks:
        lines.extend(["", "**Risks**:"])
        for r in plan.risks:
            lines.append(f"- {r}")
    lines.extend(["", "_Reply with `approve` to execute or `reject` to abort._"])
    return "\n".join(lines)


_APPROVE_TOKENS = frozenset({"approve", "approved", "yes", "y", "ok", "go", "proceed"})
_REJECT_TOKENS = frozenset({"reject", "rejected", "no", "n", "cancel", "abort", "stop"})


class ApprovalDecision(str, Enum):
    APPROVE = "approve"
    REJECT = "reject"
    UNCLEAR = "unclear"


def parse_approval(user_reply: str) -> ApprovalDecision:
    """Tolerant parse of user's approve/reject reply. UNCLEAR = ask again."""
    if not isinstance(user_reply, str):
        return ApprovalDecision.UNCLEAR
    cleaned = user_reply.strip().lower()
    if not cleaned:
        return ApprovalDecision.UNCLEAR
    # Take first whitespace-separated token, strip punctuation
    first = cleaned.split()[0].rstrip(".!?,;:")
    if first in _APPROVE_TOKENS:
        return ApprovalDecision.APPROVE
    if first in _REJECT_TOKENS:
        return ApprovalDecision.REJECT
    return ApprovalDecision.UNCLEAR


__all__ = [
    "MAX_DESCRIPTION_LEN",
    "MAX_STEPS",
    "MAX_SUMMARY_LEN",
    "MIN_STEPS",
    "PLAN_PROMPT_TEMPLATE",
    "ApprovalDecision",
    "PlanMode",
    "PlanStep",
    "PlanValidationError",
    "ProposedPlan",
    "build_plan_prompt",
    "parse_approval",
    "parse_plan_response",
    "render_plan_for_user",
]
