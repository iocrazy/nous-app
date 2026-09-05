from app.services.ai.runner.run_projection import register


@register("compaction_start")
def fold_compaction_start(views, payload):
    views["view"]["phase"] = "compacting"
    window, before = payload.get("window"), payload.get("tokens_before")
    if isinstance(window, int) and window > 0 and isinstance(before, int):
        views["view"]["context"] = {
            "used_pct": round(before * 100 / window),
            "window": window,
        }
    return views


@register("compaction_end")
def fold_compaction_end(views, payload):
    if views["view"]["phase"] == "compacting":
        views["view"]["phase"] = "running"
    after, ctx = payload.get("tokens_after"), views["view"].get("context")
    if isinstance(after, int) and ctx and ctx.get("window"):
        views["view"]["context"] = {
            "used_pct": round(after * 100 / ctx["window"]),
            "window": ctx["window"],
        }
    return views
