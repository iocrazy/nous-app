"""``verification`` → ``view.verification`` (issue completion loop). The
verifier's last word on this run: what the Runs view shows next to the
FinishIssue declaration."""

from app.services.ai.runner.run_projection import register


@register("verification")
def fold_verification(views, payload):
    verdict = payload.get("verdict")
    if verdict not in ("pass", "fail", "unverified"):
        return None
    views["view"]["verification"] = {
        "verdict": verdict,
        "attempt": payload.get("attempt"),
        "reason": payload.get("reason"),
        "retry": bool(payload.get("retry")),
    }
    return views
