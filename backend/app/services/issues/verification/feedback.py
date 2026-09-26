"""``<verifier_feedback>`` — how a rejected declaration reaches the agent.

Rendered into the NEXT continuation user message (after CONTINUATION_NUDGE)
when ``execution_state.verification`` is a ``fail`` with ``retry`` and no
``consumed_at``. The body is model-written (judge output) → escaped.
"""

from __future__ import annotations

from typing import Any

from app.boundary.frame_markers import escape_frame_attr, escape_frame_body

VERIFIER_FEEDBACK_FRAME = "verifier_feedback"


def render_verifier_feedback(verification: dict[str, Any]) -> str:
    attempt = escape_frame_attr(str(int(verification.get("attempt") or 1)))
    of = escape_frame_attr(str(int(verification.get("max_attempts") or 2)))
    lines = [
        f'<{VERIFIER_FEEDBACK_FRAME} attempt="{attempt}" of="{of}">',
        "The completion check rejected your last declaration. Unmet:",
    ]
    for item in verification.get("unmet") or []:
        criterion = escape_frame_body(str((item or {}).get("criterion") or "").strip())
        why = escape_frame_body(str((item or {}).get("why") or "").strip())
        lines.append(f"- {criterion}: {why}" if why else f"- {criterion}")
    if len(lines) == 2:
        lines.append(
            f"- {escape_frame_body(str(verification.get('reason') or 'criteria not met'))}"
        )
    lines.append("Fix these and call FinishIssue again.")
    lines.append(f"</{VERIFIER_FEEDBACK_FRAME}>")
    return "\n".join(lines)


__all__ = ["VERIFIER_FEEDBACK_FRAME", "render_verifier_feedback"]
