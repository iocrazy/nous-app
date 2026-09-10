"""``schedule_set`` → ``view.wakeups`` (phase 2b-2 §3).

Only wake-ups THIS run armed are recorded, and cancelling one produces no
event (the user cancels from the side panel, which calls DELETE on the
schedule). So this list is the history of what was set, NOT what is still
pending — the card in the side panel reads the live table and is the truth.
"""

from app.services.ai.runner.run_projection import register


@register("schedule_set")
def fold_schedule_set(views, payload):
    sid, fire_at = payload.get("schedule_id"), payload.get("fire_at")
    if not sid or not fire_at:
        return None
    views["view"]["wakeups"] = [
        *(views["view"].get("wakeups") or []),
        {
            "schedule_id": str(sid),
            "fire_at": str(fire_at),
            "note": str(payload.get("note") or ""),
        },
    ]
    return views
