from app.services.ai.runner.run_projection import register

# warn → yellow; halt → red (parked on the budget question); wrap_up → the
# one-run grace a "Wrap up" answer granted (phase 2a §3).
_STATE_BY_ACTION = {"warn": "warn", "halt": "over", "wrap_up": "wrap_up"}


@register("budget_check")
def fold_budget(views, payload):
    pct, action = payload.get("pct"), payload.get("action")
    if not isinstance(pct, (int, float)) or action not in _STATE_BY_ACTION:
        return None
    views["view"]["budget"] = {
        "pct": round(pct),
        "state": _STATE_BY_ACTION[action],
        # issue-level spend (earlier runs + this one) — cost.spent_cents is
        # this run only
        "spent_cents": payload.get("spent_cents"),
    }
    budget = payload.get("budget_cents")
    if isinstance(budget, int):
        views["cost"]["budget_cents"] = budget
        views["cost"]["pct"] = round(pct)
    return views
