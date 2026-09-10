"""``subagent_spawned`` / ``subagent_done`` → ``view.children`` + ``cost.by_child``
(phase 2b-2 §2.4). Both events live on the PARENT run.

A sync child is ``running`` between the two events; a background child is
``async_pending`` instead, because nothing is executing in this run's turn —
the workforce holds it.

``done`` must stand on its own. The worker writes it onto the parent run long
after that run may have ended, so the projection it lands on can be one that
never saw the matching ``spawned`` (a later ``for_run`` writer folds onto the
STORED views, but a replay from a truncated event range will not). A ``done``
with no live counter therefore raises ``total`` itself rather than leaving a
child that finished but was never counted.
"""

from app.services.ai.runner.run_projection import recompute_spent, register

_EMPTY = {"total": 0, "done": 0, "running": 0, "async_pending": 0, "last": None}


def _children(views):
    return {**_EMPTY, **(views["view"].get("children") or {})}


@register("subagent_spawned")
def fold_spawned(views, payload):
    mode = payload.get("mode")
    if mode not in ("sync", "async"):
        return None
    children = _children(views)
    children["total"] += 1
    children["running" if mode == "sync" else "async_pending"] += 1
    child_run_id = payload.get("child_run_id")
    children["last"] = {
        "child_run_id": str(child_run_id) if child_run_id else None,
        "subagent_type": str(payload.get("subagent_type") or ""),
        "status": "running" if mode == "sync" else "queued",
    }
    views["view"]["children"] = children
    return views


@register("subagent_done")
def fold_done(views, payload):
    child_run_id, mode = payload.get("child_run_id"), payload.get("mode")
    if not child_run_id or mode not in ("sync", "async"):
        return None
    children = _children(views)
    bucket = "running" if mode == "sync" else "async_pending"
    if children[bucket] > 0:
        children[bucket] -= 1
    else:
        children["total"] += 1
    children["done"] += 1
    children["last"] = {
        "child_run_id": str(child_run_id),
        "subagent_type": str(
            payload.get("subagent_type")
            or (children.get("last") or {}).get("subagent_type")
            or ""
        ),
        "status": str(payload.get("status") or "success"),
    }
    views["view"]["children"] = children

    cents = payload.get("cost_cents")
    if isinstance(cents, (int, float)) and not isinstance(cents, bool):
        # SET, never add: this event can arrive more than once for the same
        # child (a replayed DBOS step, a re-fold of stored views), and the
        # parent's cost must not grow once per arrival.
        views["cost"]["by_child"] = {
            **(views["cost"].get("by_child") or {}),
            str(child_run_id): float(cents),
        }
        recompute_spent(views["cost"])
    return views
