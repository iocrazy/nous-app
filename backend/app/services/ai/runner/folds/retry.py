from app.services.ai.runner.run_projection import register


@register("llm_retry")
def fold_retry(views, payload):
    attempt, mx = payload.get("attempt"), payload.get("max_retries")
    if not isinstance(attempt, int) or not isinstance(mx, int):
        return None
    views["view"]["retry"] = {
        "attempt": attempt,
        "max": mx,
        "delay_ms": payload.get("delay_ms"),
        "model": payload.get("model"),
        # stamped by the emitter (retry_events) — folds never read the clock
        "at": payload.get("at"),
    }
    return views
