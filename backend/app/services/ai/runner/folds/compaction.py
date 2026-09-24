from app.services.ai.runner.folds.context import context_view
from app.services.ai.runner.run_projection import register


@register("compaction_start")
def fold_compaction_start(views, payload):
    views["view"]["phase"] = "compacting"
    window, before = payload.get("window"), payload.get("tokens_before")
    if isinstance(window, int) and window > 0 and isinstance(before, int):
        views["view"]["context"] = context_view(
            before, window, payload.get("window_source")
        )
    return views


@register("compaction_end")
def fold_compaction_end(views, payload):
    if views["view"]["phase"] == "compacting":
        views["view"]["phase"] = "running"
    after, ctx = payload.get("tokens_after"), views["view"].get("context")
    if isinstance(after, int) and ctx and ctx.get("window"):
        # the end event carries no window: keep the start's denominator and
        # the source that came with it
        views["view"]["context"] = context_view(
            after, ctx["window"], ctx.get("window_source")
        )
    return views
