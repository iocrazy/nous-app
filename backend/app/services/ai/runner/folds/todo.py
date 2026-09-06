from app.agent_framework.agent_todo import MAX_TODO_ITEMS
from app.services.ai.runner.run_projection import register

# The fields of one todo item the view keeps. Whitelisted so a future field on
# the tool payload cannot grow the mirrored row without a decision here.
_ITEM_FIELDS = ("id", "content", "status", "active_form")


@register("todo_write")
def fold_todo(views, payload):
    counts = payload.get("counts") or {}
    total, done = counts.get("total"), counts.get("completed")
    if not isinstance(total, int) or not isinstance(done, int):
        return None
    items = [t for t in (payload.get("todos") or []) if isinstance(t, dict)]
    active = next((t for t in items if t.get("status") == "in_progress"), None)
    label = (active or {}).get("active_form") or (active or {}).get("content")
    views["view"]["step"] = {"done": done, "total": total, "label": label}
    # The whole list, not just n/m: the detail page's Steps table renders rows
    # from it. Whole-value replace on every snapshot (the tool emits the full
    # list each time), bounded by the tool's own MAX_TODO_ITEMS — the last
    # snapshot wins, so the mirrored row never grows with the run.
    views["view"]["todos"] = [
        {k: t.get(k) for k in _ITEM_FIELDS} for t in items[:MAX_TODO_ITEMS]
    ]
    return views
