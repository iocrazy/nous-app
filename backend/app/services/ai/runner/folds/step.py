from app.services.ai.runner.run_projection import recompute_spent, register


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
        # This run's OWN spend. ``spent_cents`` is derived from it plus the
        # children's, so a sub-agent's cost survives into agent_runs.cost_cents
        # instead of vanishing when the parent finishes (review I3).
        cost["own_cents"] = round(
            float(cost.get("own_cents") or 0.0) + entry["cost_cents"], 4
        )
        model = payload.get("model") or "unknown"
        cost["by_model"][model] = round(
            cost["by_model"].get(model, 0.0) + entry["cost_cents"], 4
        )
        recompute_spent(cost)
    return views
