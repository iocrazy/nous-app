from app.services.ai.runner.run_projection import register


@register("inbox_claimed")
def fold_inbox_claimed(views, payload):
    v = views["view"]
    v["last_inbox"] = {
        "kind": payload.get("kind"),
        "turn": payload.get("turn"),
        "step": payload.get("step"),
    }
    if isinstance(v.get("inbox_pending"), int) and v["inbox_pending"] > 0:
        v["inbox_pending"] -= 1
    return views
