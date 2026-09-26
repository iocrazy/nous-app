"""Issue completion verification (spec 2026-09-26). Public face:

* ``pending_verifier_feedback`` — the continuation message hook (Task 3)
* ``apply_completion_verification`` — the step-body hook (Task 6)

Plain async helpers — never ``@DBOS.step`` (tests pin this).
"""

from __future__ import annotations

import datetime as _dt
from typing import Any, Optional

from loguru import logger

from app.services.issues.execution_state import merge_execution_state
from app.services.issues.verification.feedback import render_verifier_feedback


async def _load_issue_row(issue_id: int) -> Optional[dict[str, Any]]:
    from app.repositories.issue_repository import issue_repository

    return await issue_repository.get_by_id(int(issue_id))


def _verification_of(row: Optional[dict[str, Any]]) -> Optional[dict[str, Any]]:
    import json

    state = (row or {}).get("execution_state")
    if isinstance(state, str):
        try:
            state = json.loads(state)
        except (TypeError, ValueError):
            return None
    v = (state or {}).get("verification") if isinstance(state, dict) else None
    return v if isinstance(v, dict) else None


async def pending_verifier_feedback(issue_id: int) -> Optional[str]:
    """The rendered frame for an unconsumed rejection, or None. Marks it
    consumed (``consumed_at``) so the same verdict is injected once. Never
    raises — a feedback miss costs one unguided turn, not the turn itself."""
    try:
        v = _verification_of(await _load_issue_row(issue_id))
        if (
            not v
            or v.get("verdict") != "fail"
            or not v.get("retry")
            or v.get("consumed_at")
        ):
            return None
        stamped = {**v, "consumed_at": _dt.datetime.now(_dt.timezone.utc).isoformat()}
        await merge_execution_state(int(issue_id), {"verification": stamped})
        return render_verifier_feedback(stamped)
    except Exception as exc:  # noqa: BLE001 — decoration, never break the turn
        logger.warning(
            f"[verification] issue {issue_id}: feedback read failed: {exc!r}"
        )
        return None


# Last, on purpose: service.py imports only this package's SUBMODULES (never
# the package itself), so importing it after everything above is cycle-free.
from app.services.issues.verification.service import (  # noqa: E402
    VERIFY_MAX_ATTEMPTS,
    apply_completion_verification,
    reset_verify_attempts,
)

__all__ = [
    "VERIFY_MAX_ATTEMPTS",
    "apply_completion_verification",
    "pending_verifier_feedback",
    "reset_verify_attempts",
]
