"""Orchestration (spec §5.4): evidence → predicates → judge → verdict →
execution_state / thread row / transcript event → outcome conversion.

Runs INSIDE the existing step bodies (``run_issue_agent`` /
``run_issue_reply_step``); plain async, never a DBOS step. Every failure
degrades to ``unverified`` — never to ``pass`` (spec §7).

Import rule: the package ``__init__`` imports THIS module at its end, so
nothing here may import from ``app.services.issues.verification`` itself —
only from its submodules.
"""

from __future__ import annotations

import datetime as _dt
from dataclasses import asdict, dataclass
from typing import Any, Optional

from loguru import logger

from app.services.ai.tools.ask_user_tool import awaiting_input_outcome
from app.services.issues.acceptance_criteria import load_acceptance_criteria
from app.services.issues.execution_state import merge_execution_state
from app.services.issues.turn_outcome import BUDGET_QUESTION_KIND
from app.services.issues.verification.evidence import build_evidence_bundle
from app.services.issues.verification.judge import (
    JudgeError,
    judge,
    verification_enabled,
)
from app.services.issues.verification.predicates import (
    PredicateResult,
    run_predicates,
)

VERIFY_MAX_ATTEMPTS = 2
VERDICT_META_KIND = "verdict"
VERIFICATION_EVENT_TYPE = "verification"


@dataclass(frozen=True)
class Verdict:
    verdict: str  # pass | fail | unverified
    reason: str
    unmet: tuple[dict[str, str], ...]
    predicates: tuple[dict[str, Any], ...]
    facts: tuple[dict[str, Any], ...]
    confidence: Optional[float]
    source: str  # predicate | judge | none
    criteria_source: Optional[str]
    verifier_run_id: Optional[str]
    attempt: int = 0
    max_attempts: int = VERIFY_MAX_ATTEMPTS
    retry: bool = False
    checked_at: str = ""
    consumed_at: Optional[str] = None

    def as_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["unmet"] = list(self.unmet)
        d["predicates"] = list(self.predicates)
        d["facts"] = list(self.facts)
        return d


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat()


def _pred_dicts(results: tuple[PredicateResult, ...]) -> tuple[dict[str, Any], ...]:
    return tuple(
        {"name": r.name, "status": r.status, "facts": r.facts} for r in results
    )


def _skip_reason(result: dict[str, Any]) -> Optional[str]:
    if result.get("stop_reason") in ("cancelled", "paused"):
        return str(result.get("stop_reason"))
    parked = awaiting_input_outcome(result)
    if parked is not None and (parked[2] or {}).get("kind") == BUDGET_QUESTION_KIND:
        return "budget_wrap_up"
    return None


def _why(r: PredicateResult) -> str:
    return ", ".join(f"{k}={v}" for k, v in (r.facts or {}).items()) or "check failed"


async def verify_completion(
    *,
    issue_id: int,
    run_id: Optional[str],
    final_text: str,
    session_id: str,
    user_id: str,
    trigger: str,
    attribution: Optional[str],
) -> Verdict:
    criteria, criteria_source = await load_acceptance_criteria(issue_id)
    bundle = await build_evidence_bundle(
        issue_id=issue_id,
        run_id=run_id,
        final_text=final_text,
        session_id=session_id,
        user_id=user_id,
    )
    trigger_text = criteria or final_text
    results = run_predicates(trigger_text, bundle)
    preds = _pred_dicts(results)
    violated = [r for r in results if r.status == "violated"]
    if violated:
        unmet = tuple({"criterion": r.name, "why": _why(r)} for r in violated)
        return Verdict(
            "fail",
            f"predicate_violated: {violated[0].name}",
            unmet,
            preds,
            preds,
            None,
            "predicate",
            criteria_source,
            None,
        )
    if not criteria:
        return Verdict(
            "unverified", "criteria_missing", (), preds, preds, None, "none", None, None
        )
    try:
        out = await judge(
            criteria=criteria,
            bundle=bundle,
            predicates=results,
            session_id=session_id,
            user_id=user_id,
            issue_id=issue_id,
            trigger=trigger,
            attribution=attribution,
            parent_run_id=run_id,
        )
    except JudgeError as exc:
        logger.warning(
            f"[verification] issue {issue_id} run {run_id}: {exc.code}: {exc}"
        )
        return Verdict(
            "unverified",
            exc.code,
            (),
            preds,
            preds,
            None,
            "judge",
            criteria_source,
            None,
        )
    reason = (
        "criteria_met"
        if out.verdict == "pass"
        else (
            "; ".join(f"{u['criterion']}: {u['why']}" for u in out.unmet)
            or "criteria_not_met"
        )
    )
    return Verdict(
        out.verdict,
        reason,
        out.unmet,
        preds,
        preds,
        out.confidence,
        "judge",
        criteria_source,
        out.run_id,
    )


async def _load_issue_row(issue_id: int) -> Optional[dict[str, Any]]:
    from app.repositories.issue_repository import issue_repository

    return await issue_repository.get_by_id(int(issue_id))


def _attempts_of(row: Optional[dict[str, Any]]) -> int:
    import json

    state = (row or {}).get("execution_state")
    if isinstance(state, str):
        try:
            state = json.loads(state)
        except (TypeError, ValueError):
            return 0
    try:
        return (
            int((state or {}).get("verify_attempts") or 0)
            if isinstance(state, dict)
            else 0
        )
    except (TypeError, ValueError):
        return 0


async def _record_verdict_message(
    issue_id: int, agent_id: Optional[str], v: Verdict
) -> None:
    """Thread row (deviation 2): kind='comment' by the assignee agent,
    meta.kind='verdict'. Best-effort, logged."""
    if not agent_id:
        logger.warning(
            f"[verification] issue {issue_id}: no assignee agent; verdict row skipped"
        )
        return
    from sqlalchemy import insert

    from app.db.session import write_scope
    from app.models import IssueMessages

    label = {"pass": "Verified", "fail": "Rejected", "unverified": "Unverified"}[
        v.verdict
    ]
    body = f"{label} (attempt {v.attempt}/{v.max_attempts}): {v.reason}"
    try:
        async with write_scope() as session:
            await session.execute(
                insert(IssueMessages).values(
                    issue_id=int(issue_id),
                    kind="comment",
                    author_agent_id=str(agent_id),
                    body=body[:2000],
                    meta={
                        "kind": VERDICT_META_KIND,
                        "verdict": v.verdict,
                        "attempt": v.attempt,
                        "unmet": list(v.unmet)[:10],
                        "verifier_run_id": v.verifier_run_id,
                    },
                )
            )
    except Exception as exc:  # noqa: BLE001
        logger.opt(exception=True).error(
            f"[verification] issue {issue_id}: verdict row failed: {exc}"
        )


async def _record_verification_event(run_id: Optional[str], v: dict[str, Any]) -> None:
    if not run_id:
        return
    from app.services.ai.runner.run_recorder import RunEventWriter

    try:
        writer = await RunEventWriter.for_run(run_id)
        await writer.append(
            VERIFICATION_EVENT_TYPE,
            {
                "verdict": v["verdict"],
                "reason": v["reason"],
                "attempt": v["attempt"],
                "retry": v["retry"],
                "unmet": [u.get("criterion") for u in v.get("unmet") or []][:5],
                "verifier_run_id": v.get("verifier_run_id"),
            },
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"[verification] run {run_id}: event write failed: {exc!r}")


async def apply_completion_verification(
    *,
    issue_id: int,
    outcome: Optional[str],
    reason: Optional[str],
    result: dict[str, Any],
    content: str,
    session_id: str,
    user_id: str,
    trigger: str,
    attribution: Optional[str],
) -> tuple[Optional[str], Optional[str], Optional[dict[str, Any]]]:
    """The step-body hook. Only a ``completed`` declaration is reviewed; a
    cancel / pause / budget wrap-up is not (spec §7, §8). Returns the possibly
    converted ``(outcome, reason)`` and the verdict dict that goes into the
    step result as ``"verification"``."""
    if outcome != "completed":
        return outcome, reason, None
    skip = _skip_reason(result or {})
    if skip:
        logger.info(f"[verification] issue {issue_id}: not reviewed ({skip})")
        return outcome, reason, None
    if not await verification_enabled():
        return outcome, reason, None
    run_id = (result or {}).get("run_id")
    run_id = str(run_id) if run_id is not None else None
    row = None
    try:
        row = await _load_issue_row(issue_id)
        verdict = await verify_completion(
            issue_id=issue_id,
            run_id=run_id,
            final_text=content,
            session_id=session_id,
            user_id=user_id,
            trigger=trigger,
            attribution=attribution,
        )
    except Exception as exc:  # noqa: BLE001 — never pass, never break the turn
        logger.opt(exception=True).warning(
            f"[verification] issue {issue_id}: verifier_error: {exc!r}"
        )
        verdict = Verdict(
            "unverified", "verifier_error", (), (), (), None, "none", None, None
        )
    attempts = _attempts_of(row) + 1
    retry = verdict.verdict == "fail" and attempts <= VERIFY_MAX_ATTEMPTS
    stamped = Verdict(
        **{**asdict(verdict), "attempt": attempts, "retry": retry, "checked_at": _now()}
    )
    vd = stamped.as_dict()
    try:
        await merge_execution_state(
            int(issue_id), {"verification": vd, "verify_attempts": attempts}
        )
    except Exception as exc:  # noqa: BLE001
        logger.opt(exception=True).error(
            f"[verification] issue {issue_id}: execution_state write failed: {exc}"
        )
    await _record_verdict_message(
        issue_id, (row or {}).get("assignee_agent_id"), stamped
    )
    await _record_verification_event(run_id, vd)
    if retry:
        first = (
            vd["unmet"][0] if vd["unmet"] else {"criterion": vd["reason"], "why": ""}
        )
        why = (
            f"{first['criterion']}: {first['why']}"
            if first.get("why")
            else first["criterion"]
        )
        return "continue", f"verifier_rejected: {why}", vd
    return outcome, reason, vd


__all__ = [
    "VERIFY_MAX_ATTEMPTS",
    "Verdict",
    "apply_completion_verification",
    "verify_completion",
]
