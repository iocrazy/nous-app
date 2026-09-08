from app.services.ai.runner.run_projection import register

_PHASE_BY_REASON = {
    "paused": "paused",
    "awaiting_approval": "waiting_input",
    "awaiting_input": "waiting_input",
}


@register("turn_end")
def fold_turn_end(views, payload):
    reason = payload.get("reason")
    if not isinstance(reason, str):
        return None
    views["view"]["ended"] = {
        k: v
        for k, v in payload.items()
        if k in ("reason", "finish_reason", "error_code", "error")
    }
    views["view"]["phase"] = _PHASE_BY_REASON.get(reason, "ended")
    views["view"]["current"] = None
    return views
