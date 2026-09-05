from app.services.ai.runner.run_projection import register


@register("budget_check")
def fold_budget(views, payload):
    pct, action = payload.get("pct"), payload.get("action")
    if not isinstance(pct, (int, float)) or action not in ("warn", "halt"):
        return None
    views["view"]["budget"] = {
        "pct": round(pct),
        "state": "over" if action == "halt" else "warn",
        # issue-level spend (earlier runs + this one) — cost.spent_cents is
        # this run only
        "spent_cents": payload.get("spent_cents"),
    }
    budget = payload.get("budget_cents")
    if isinstance(budget, int):
        views["cost"]["budget_cents"] = budget
        views["cost"]["pct"] = round(pct)
    return views
