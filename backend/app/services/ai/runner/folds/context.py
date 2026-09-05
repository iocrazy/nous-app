"""``context_measured`` is a LOCAL measurement fed by the recorder on every
compaction check (green/yellow tiers write no transcript event, yet the
pressure gauge should move each turn). Never inserted as an event."""

from app.services.ai.runner.run_projection import register


@register("context_measured")
def fold_context(views, payload):
    used, window = payload.get("used"), payload.get("window")
    if not isinstance(used, int) or not isinstance(window, int) or window <= 0:
        return None
    views["view"]["context"] = {
        "used_pct": round(used * 100 / window),
        "window": window,
    }
    return views
