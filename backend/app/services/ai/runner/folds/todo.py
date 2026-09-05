from app.services.ai.runner.run_projection import register


@register("todo_write")
def fold_todo(views, payload):
    counts = payload.get("counts") or {}
    total, done = counts.get("total"), counts.get("completed")
    if not isinstance(total, int) or not isinstance(done, int):
        return None
    active = next(
        (t for t in payload.get("todos") or [] if t.get("status") == "in_progress"),
        None,
    )
    label = (active or {}).get("active_form") or (active or {}).get("content")
    views["view"]["step"] = {"done": done, "total": total, "label": label}
    return views
