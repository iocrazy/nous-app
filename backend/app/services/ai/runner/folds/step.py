from app.services.ai.runner.run_projection import register


@register("step_start")
def fold_step_start(views, payload):
    views["view"]["phase"] = "running"
    views["view"]["current"] = {
        "turn": payload.get("turn"),
        "step": payload.get("step"),
        "model": payload.get("model"),
    }
    return views


@register("step_end")
def fold_step_end(views, payload):
    cost = views["cost"]
    cents = payload.get("cost_cents")
    usage = payload.get("usage") or {}
    entry = {
        "turn": payload.get("turn"),
        "step": payload.get("step"),
        "cost_cents": float(cents) if isinstance(cents, (int, float)) else None,
        "prompt": usage.get("prompt"),
        "completion": usage.get("completion"),
        "duration_ms": payload.get("duration_ms"),
    }
    cost["by_step"].append(entry)
    if entry["cost_cents"] is not None:
        cost["spent_cents"] = round(cost["spent_cents"] + entry["cost_cents"], 4)
        model = payload.get("model") or "unknown"
        cost["by_model"][model] = round(
            cost["by_model"].get(model, 0.0) + entry["cost_cents"], 4
        )
        if cost.get("budget_cents"):
            cost["pct"] = round(cost["spent_cents"] * 100 / cost["budget_cents"])
    return views
