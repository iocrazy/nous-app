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


def _settle_key(payload) -> str:
    """Which child a ``done`` settles. A background child is its TASK — a
    replayed worker step re-runs it under a new run id, and that second run is
    the same child. A synchronous child has no task; its run id is the key."""
    task_id = payload.get("task_id")
    if task_id:
        return f"task:{task_id}"
    return f"run:{payload.get('child_run_id')}"


@register("subagent_done")
def fold_done(views, payload):
    """Idempotent per child (fh4 E2d). A second ``done`` for a settled child —
    a replayed step, the reaper racing the worker, a re-fold — leaves the
    counters and ``last`` alone; before fh4 it counted the child twice. The
    cost below is SET per run id and was always safe to repeat.

    ``children.settled`` holds the keys. It only appears once a child has
    settled, so the empty-views shape the UI pins is unchanged."""
    child_run_id, mode = payload.get("child_run_id"), payload.get("mode")
    if not child_run_id or mode not in ("sync", "async"):
        return None
    children = _children(views)
    key = _settle_key(payload)
    settled = list(children.get("settled") or [])
    if key not in settled:
        bucket = "running" if mode == "sync" else "async_pending"
        children = {
            **children,
            **(
                {bucket: children[bucket] - 1}
                if children[bucket] > 0
                else {"total": children["total"] + 1}
            ),
            "done": children["done"] + 1,
            "settled": [*settled, key],
            "last": {
                "child_run_id": str(child_run_id),
                "subagent_type": str(
                    payload.get("subagent_type")
                    or (children.get("last") or {}).get("subagent_type")
                    or ""
                ),
                "status": str(payload.get("status") or "success"),
            },
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
        # 同样 SET。两条道**同海拔**：都是那棵子树的合计（见发射点
        # ``_cost_cents_of`` / ``_byok_cents_of`` 的 docstring）。
        #
        # ⚠️ **这条道当前没有任何消费方**（终审 I3）。扣费按**行**聚合，只读每条
        # run 自己的 ``own_cents`` / ``media_cents`` 及其两条 BYOK 道，``by_child``
        # 与 ``by_child_byok`` 都不参与 —— 见 ``ai/billing/tree_charge.py`` 模块
        # docstring「金额怎么算」。这里留着是因为它是 ``subagent_done`` 的诚实字段
        # （子树 BYOK 合计），异步链将来若改回按 ``by_child`` 聚合还要用它。
        # **不要**照着「父行减一下就是平台额」去改本函数的海拔 —— 那个减法今天不
        # 存在于任何代码里，改它动不了钱，只会让两条道对不上。
        byok = payload.get("byok_cents")
        if isinstance(byok, (int, float)) and not isinstance(byok, bool):
            views["cost"]["by_child_byok"] = {
                **(views["cost"].get("by_child_byok") or {}),
                str(child_run_id): float(byok),
            }
        recompute_spent(views["cost"])
    return views
