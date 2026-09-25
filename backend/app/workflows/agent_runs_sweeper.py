"""agent_runs_sweeper — DBOS @scheduled port of the legacy
`tasks.agent_runs_sweeper.sweep` Celery beat job.

The legacy task ran every 60s under celery-beat, fenced by a Postgres
advisory lock so multiple beat workers wouldn't double-execute. DBOS
handles that natively: the scheduler generates a deterministic
workflow_id (`sched-<name>-<iso>`) per cron tick, and the system DB
rejects duplicate workflow_ids — so only one DBOS worker actually
runs each tick. The advisory_lock indirection is dropped.

Two jobs (unchanged from the Celery version):
  1. mark zombie `status='running'` rows (heartbeat_at < now-2min)
     as `heartbeat_lost`
  2. recompute monthly token/cost spend per agent → flip
     ai_agents.paused_reason='budget' on overrun, clear it on
     undershoot (without clobbering manual pauses)

Why steps + workflow are ALL ``async def``
------------------------------------------
Originally written as ``def + asyncio.run(_do())`` because supabase-py
worked fine across short-lived event loops — every call recreated its
httpx client. asyncpg's pool is the opposite: it BINDS to the loop
where it was first awaited. Each ``asyncio.run()`` opens a new loop,
runs the coroutine, and closes the loop — but a cached asyncpg
connection still points at the (now-dead) first-loop pool. Second
tick onwards crashes with ``Event loop is closed`` and
``cannot perform operation: another operation is in progress``.

DBOS supports ``async def`` workflows + steps natively. Awaiting from
the same loop the executor owns means the asyncpg pool stays bound to
a long-lived loop — the symptom disappears at the source.

Same fix shape as PR #227 (workflow_health_sweeper) — see that file's
header for the longer cascade explanation.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from dbos import DBOS
from loguru import logger

HEARTBEAT_STALENESS_SECONDS = 120


def _agents_budget_scan_stmt(agent_ids):
    """Column-level select of the budget fields for a batch of agent ids.

    ``select(*AiAgents.__table__.c)`` — NOT ``select(AiAgents)``. The
    entity-level form maps each result row to a single 'AiAgents' key (the
    ORM instance), not one key per column, so every ``agent.get('paused_
    reason')``/``agent['id']`` consumer below would silently return None /
    raise KeyError instead of the legacy column-keyed dict shape (see
    tests/test_orm_b5_task2_row_shape_e2e.py for the real-engine proof)."""
    from sqlalchemy import select

    from app.models import AiAgents

    return select(*AiAgents.__table__.c).where(AiAgents.id.in_(agent_ids))


def _agent_pause_stmt(agent_id, paused_reason):
    """UPDATE ai_agents.paused_reason for one agent (budget pause/unpause)."""
    from sqlalchemy import update

    from app.models import AiAgents

    return (
        update(AiAgents)
        .where(AiAgents.id == agent_id)
        .values(paused_reason=paused_reason)
    )


@DBOS.step()
async def mark_heartbeat_lost_step() -> int:
    """Flip running rows whose heartbeat is older than 2 minutes, then close
    each flipped run's transcript with ``turn_end{reason:interrupted}`` so
    the event log stays replay-complete (spec §2 primitive ①). A run whose
    transcript already carries a turn_end is left alone."""
    from app.repositories.agent_runs_repository import get_agent_runs_repository
    from app.services.ai.runner.interrupted_turn import close_interrupted_runs

    runs_repo = get_agent_runs_repository()
    stale_before = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(
        seconds=HEARTBEAT_STALENESS_SECONDS
    )
    run_ids = await runs_repo.mark_heartbeat_lost_ids(stale_before=stale_before)
    await close_interrupted_runs(run_ids)

    # 这条 run 永远不会走到 ``RunRecorder._finish``，所以树收口也只能由这里
    # 跟上 —— 漏掉这一处，含一条崩溃 run 的树永远收不了口，整棵树一分不扣
    # （见 ``tree_charge`` 模块 docstring 的 Stated Limitations）。
    from app.services.ai.billing.tree_charge import settle_tree_if_closed

    for run_id in run_ids:
        try:
            await settle_tree_if_closed(run_id=str(run_id))
        except Exception as exc:  # noqa: BLE001 — 计费绝不连坐终态写入
            logger.warning(f"[agent-runs-sweeper] tree settle {run_id} failed: {exc}")
    return len(run_ids)


INBOX_ORPHAN_SECONDS = 24 * 3600
#: Slack past the needs_input gate's TTL before a parked issue's items become
#: expirable (FH3 T1): the gate's own timeout, the turn that follows it and a
#: sweeper tick all land after ``since + TTL``, and an item must not be thrown
#: away in the minute the answer turn was about to claim it.
PARKED_EXPIRY_GRACE_SECONDS = 3600
RECONCILE_GRACE_SECONDS = 10 * 60


@DBOS.step()
async def reconcile_issue_execution_state_step() -> int:
    """MH-1: an ``in_progress`` issue whose last run ended (any terminal
    status) ≥10 min ago while ``execution_state.turn`` still says a turn is
    on → merge ``agent_outcome="interrupted"`` (+ reason, reconciled_at) so
    the decoration stops claiming a run that is gone. issue.rollup already
    derives phase from the runs; this keeps the column honest for the
    readers that still look at it. Returns how many issues were stamped."""
    from app.repositories.agent_runs_repository import get_agent_runs_repository
    from app.repositories.issue_repository import issue_repository
    from app.services.issues.execution_state import merge_execution_state

    cutoff = datetime.now(timezone.utc) - timedelta(seconds=RECONCILE_GRACE_SECONDS)
    stamped = 0
    for issue in await issue_repository.list_in_progress_without_live_run():
        session_id = issue.get("ai_session_id")
        runs = await get_agent_runs_repository().list_for_issue(
            issue_id=int(issue["id"]),
            conversation_id=int(session_id) if session_id else None,
            limit=1,
        )
        latest = runs[0] if runs else None
        ended_at = (latest or {}).get("ended_at")
        if latest is None or ended_at is None or ended_at > cutoff:
            continue
        await merge_execution_state(
            int(issue["id"]),
            {
                "agent_outcome": "interrupted",
                "outcome_reason": (
                    f"run {latest['id']} ended ({latest.get('status')}) "
                    "without a status transition"
                ),
                "reconciled_at": datetime.now(timezone.utc).isoformat(),
            },
        )
        stamped += 1
    return stamped


@DBOS.step()
async def reap_preempted_input_waits_step() -> int:
    """Release workflows still parked on a question whose issue is already
    cancelled/done/closed (hotfix-2 defect H). The cancel hook normally does
    this at once in the API process; when its cancel fails it leaves the
    marker, and this tick — on the worker, where the singleton is launched —
    finishes it within a minute. Returns how many were released."""
    from app.agent_framework.input_gate import reap_preempted_input_waits

    return await reap_preempted_input_waits()


@DBOS.step()
async def reap_stale_workforce_tasks_step() -> dict[str, int]:
    """Close workforce tasks whose worker died and whose DBOS workflow will
    not come back (framework-hardening T5). Task-centric, 10 minutes, at most
    20 per tick — see ``services/workforce/stale_tasks.py`` for the three
    rules. Never raises: the reaper counts per-row failures instead.

    ⚠️ Steps are only ever APPENDED to the tick: a step inserted mid-sequence
    shifts the step ids of in-flight scheduled workflows across a deploy. This
    one was last until FH3 T6 appended ``reap_zombie_locks_step`` after it."""
    from app.services.workforce.stale_tasks import reap_stale_workforce_tasks

    return await reap_stale_workforce_tasks()


@DBOS.step()
async def reap_zombie_locks_step() -> int:
    """Release issue execution locks whose workflow is already terminal or
    gone (FH3 T6): a cancelled ``execute_issue`` never reaches its
    ``finally: clear_lock``. CAS per row, ``status`` untouched, nothing
    cancelled — see ``input_gate.reap_zombie_locks``. Never raises.

    ⚠️ Must stay the LAST step of the tick: a step inserted mid-sequence
    shifts the step ids of in-flight scheduled workflows across a deploy."""
    from app.agent_framework.input_gate import reap_zombie_locks

    return await reap_zombie_locks()


@DBOS.step()
async def expire_orphan_inbox_step() -> int:
    """Mark unclaimed agent_run_inbox items older than a day as expired
    (spec §1-③: an item whose run ended before the next step boundary is an
    orphan; it is never deleted, so the thread still shows it was sent).

    Not orphans: items on a paused issue (phase 2a) and items on an issue
    parked on the needs_input gate while the gate still waits (FH3 T1)."""
    from app.core.config import settings
    from app.repositories.agent_run_inbox_repository import (
        get_agent_run_inbox_repository,
    )

    now = datetime.now(timezone.utc)
    older_than = now - timedelta(seconds=INBOX_ORPHAN_SECONDS)
    # FH3 T1: an issue parked on the needs_input gate keeps its items until the
    # gate itself gives up; a marker older than that is a dead workflow's.
    parked_floor = now - timedelta(
        hours=settings.NEEDS_INPUT_RECV_TTL_HOURS,
        seconds=PARKED_EXPIRY_GRACE_SECONDS,
    )
    # Phase 2a: items queued on a PAUSED issue wait for resume — never orphans.
    return await get_agent_run_inbox_repository().expire_stale(
        older_than=older_than,
        skip_paused_issues=True,
        skip_parked_issues=True,
        parked_floor=parked_floor,
    )


#: Issues drained per tick. The sweep runs every minute, so what does not fit
#: is picked up next minute — the cap bounds one tick, not the backlog.
INBOX_DRAIN_LIMIT = 20


@DBOS.step()
async def scan_idle_inbox_step() -> list[dict[str, Any]]:
    """Never dispatches. Returns the drain orders the WORKFLOW body must dispatch.

    Items are claimed at STEP boundaries only (``InboxClaimHook``). Between a
    run's last boundary and its row going terminal there is a window with no
    boundary left, and anything that lands there is stranded: the 2026-09-10
    acceptance watched a ``subagent_result`` and a scheduled ``steer`` sit
    unclaimed for 15 and 8.8 minutes while the schedule row reported a clean
    ``fire_count=1``. ``issue_lifecycle`` now drains before it ends, bounded;
    this is the backstop for everything that misses that window — a run that
    crashed, a dispatch that never started, a stream past the bound.

    Being the BACKSTOP, it declines to race the primary: ``pending_issue_targets``
    excludes issues holding ``execution_locked_at``. That lock is up for the
    whole lifetime of ``execute_issue``, including the seconds between the
    in-turn drain deciding to run one more turn and that turn's ``agent_runs``
    row existing — a gap in which NONE of the three busy signals is raised. On
    2026-09-10 a tick landed inside an 11 s one and bought a second billed turn
    on an item the drain had already taken, which reached the user as an
    unexplained "Continue working on this issue" (Task 7b defect C).

    ⚠️ **This function must never dispatch.** DBOS forbids ``start_workflow``
    from inside a step and asserts it with an EMPTY message, which the delivery
    path catches and returns as ``skipped/dispatch_failed:`` — the first cut of
    this drain did exactly that and could not fire even once in production
    while logging "left in place" at INFO (route C; ``scheduled_master.py:105``
    records the same rule, and Task 5's wake-up dispatch is split for it). The
    body half is ``_drain_one_issue``.

    It does WRITE in one case (FH3 T1), which is why this says "never
    dispatches" rather than "select only" — the rule was always about
    ``start_workflow``. An issue waiting on a person (``in_review`` /
    ``needs_followup``, not parked on the gate) has the wake-ups the AGENT
    scheduled for itself expired here, with a WARN: ``deliver_or_dispatch``
    reads such an issue as idle and would buy a billed turn on them — the turn
    ``_fire_issue_wakeup``'s ``issue_not_active`` guard exists to refuse, which
    a wake-up queued while the issue was parked went around. What a person or
    a sub-agent sent still dispatches. If nothing else is pending the target
    yields a marker order ``{issue_id, inactive_expired}`` the body counts.

    An order carries only what the dispatch needs. The step's return value is
    checkpointed into ``dbos.operation_outputs`` every minute, forever.
    """
    from app.repositories.agent_run_inbox_repository import (
        get_agent_run_inbox_repository,
    )

    repo = get_agent_run_inbox_repository()
    try:
        targets = await repo.pending_issue_targets(limit=INBOX_DRAIN_LIMIT)
    except Exception as err:  # noqa: BLE001 — a probe that cannot read has
        # not proved there is nothing to drain; say so and try again next tick.
        logger.error(f"[sweeper] idle-drain could not list pending targets: {err}")
        return []

    orders: list[dict[str, Any]] = []
    for target in targets:
        issue_id = int(target["target_id"])
        try:
            inactive_expired = await _expire_agent_items_if_inactive(repo, issue_id)
            item = await repo.oldest_pending_for_target(
                target_kind="issue", target_id=issue_id
            )
        except Exception as err:  # noqa: BLE001 — one issue must not sink the scan
            # Also the answer to a failed activity read or expiry: neither has
            # proved the issue active, and a wrong guess costs a billed turn.
            logger.exception(f"[sweeper] idle-drain scan failed for {issue_id}: {err}")
            continue
        if item is None:
            if inactive_expired:
                orders.append(
                    {"issue_id": issue_id, "inactive_expired": inactive_expired}
                )
            # else: claimed between the two reads — nothing stranded after all
            continue
        orders.append(
            {
                "issue_id": issue_id,
                "user_id": str(item["user_id"]),
                "kind": str(item.get("kind") or "steer"),
                "pending_count": int(target.get("count") or 0),
            }
        )
    return orders


async def _expire_agent_items_if_inactive(repo: Any, issue_id: int) -> int:
    """FH3 T1 (E2): on an issue waiting on a person and not parked on the gate,
    expire the pending items the agent scheduled for itself; return how many.

    Called from inside ``scan_idle_inbox_step`` — a write, never a dispatch.
    Errors propagate: the scan skips the target for this tick rather than
    dispatch on a guess. A parked issue is left alone (its answer turn claims
    the items); a missing issue is left to ``deliver_or_dispatch``, which
    already refuses it.
    """
    from app.repositories.user_schedules_repository import (
        AGENT_WAKEUP_INACTIVE_STATUSES,
        ISSUE_NOT_ACTIVE,
    )
    from app.services.issues.execution_state import is_parked_on_input

    issue = await repo.issue_activity(issue_id)
    if (
        not issue
        or issue.get("status") not in AGENT_WAKEUP_INACTIVE_STATUSES
        or is_parked_on_input(issue)
    ):
        return 0
    return int(
        await repo.expire_agent_items_for_target(
            target_kind="issue", target_id=issue_id, reason=ISSUE_NOT_ACTIVE
        )
    )


async def _drain_one_issue(order: dict[str, Any], counters: dict[str, int]) -> None:
    """Dispatch one stranded issue. Runs in the workflow BODY — see the step's
    docstring for why it cannot live inside one.

    The busy question is NOT re-implemented here. ``deliver_or_dispatch`` owns
    all three signals (a running root run, ``paused_at``, the in-flight
    ``dispatching`` marker) plus the terminal/hidden check, so this calls it
    with ``already_enqueued=True``: on busy it returns without writing a second
    row, on idle it starts the turn on the continuation nudge.

    THREE outcomes, reported apart. "The issue is busy" is routine; "the
    dispatch itself failed" is a fault, and the first cut logged both at INFO
    as "left in place" — which is how a backstop that never worked read as
    working.
    """
    from app.services.issues import inbox_or_dispatch as deliver_mod

    issue_id = int(order["issue_id"])
    count = int(order.get("pending_count") or 0)
    try:
        result = await deliver_mod.deliver_or_dispatch(
            issue_id,
            kind=str(order.get("kind") or "steer"),
            content={},
            user_id=str(order["user_id"]),
            already_enqueued=True,
        )
    except Exception as err:  # noqa: BLE001 — one issue must not sink the tick
        counters["failed"] += 1
        logger.exception(f"[sweeper] idle-drain failed for issue {issue_id}: {err}")
        return

    if result.mode == "dispatched":
        counters["dispatched"] += 1
        logger.info(
            f"[sweeper] issue {issue_id}: {count} stranded inbox item(s) — "
            f"dispatched {result.workflow_id}"
        )
    elif result.mode == "skipped" and str(result.reason or "").startswith(
        "dispatch_failed"
    ):
        counters["failed"] += 1
        logger.error(
            f"[sweeper] idle-drain could not dispatch issue {issue_id} holding "
            f"{count} stranded inbox item(s): {result.reason}"
        )
    else:
        counters["busy"] += 1
        logger.info(
            f"[sweeper] issue {issue_id}: {count} pending inbox item(s) left "
            f"in place ({result.mode}/{result.reason})"
        )


@DBOS.step()
async def recompute_monthly_budgets_step() -> int:
    """Sum this month's spend per agent, flip paused_reason='budget' on
    overrun. Returns count of agents whose paused_reason transitioned."""
    from uuid import UUID

    from app.db import engine as db_engine
    from app.db.session import read_scope, write_scope
    from app.repositories.agent_runs_repository import get_agent_runs_repository

    if not db_engine.is_configured():
        return 0

    now = datetime.now(timezone.utc)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    runs_repo = get_agent_runs_repository()
    rows = await runs_repo.monthly_usage_by_agent(
        month_start=month_start,
        month_end=now.replace(microsecond=0),
    )

    totals: dict[str, dict[str, float]] = {}
    for r in rows:
        aid = r["agent_id"]
        bucket = totals.setdefault(aid, {"tokens": 0, "cost_cents": 0.0})
        bucket["tokens"] += int(r.get("total_tokens") or 0)
        if r.get("cost_cents") is not None:
            bucket["cost_cents"] += float(r["cost_cents"])

    if not totals:
        return 0

    # agent_runs.agent_id is a uuid column round-tripped as str (see the
    # VALUE-TYPE PARITY note at the top of agent_runs_repository.py) —
    # AiAgents.id is a native Uuid column, so bind real uuid.UUID objects.
    agent_ids = [UUID(aid) for aid in totals.keys()]
    async with read_scope() as session:
        agents = (
            (await session.execute(_agents_budget_scan_stmt(agent_ids)))
            .mappings()
            .all()
        )

    transitions = 0
    for agent in agents:
        aid = str(agent["id"])
        t = totals.get(aid, {"tokens": 0, "cost_cents": 0.0})
        token_budget = agent.get("monthly_token_budget")
        cost_budget = agent.get("monthly_cost_cents_budget")
        paused_reason = agent.get("paused_reason")

        over_tokens = token_budget is not None and t["tokens"] > int(token_budget)
        over_cost = cost_budget is not None and t["cost_cents"] > float(cost_budget)
        should_pause = over_tokens or over_cost

        if should_pause and paused_reason != "budget":
            # Don't clobber a manual pause.
            if paused_reason is None:
                async with write_scope() as session:
                    await session.execute(_agent_pause_stmt(agent["id"], "budget"))
                transitions += 1
                logger.info(
                    f"[sweeper] agent {aid} paused_by_budget "
                    f"(tokens={t['tokens']}, cost={t['cost_cents']})"
                )
        elif not should_pause and paused_reason == "budget":
            async with write_scope() as session:
                await session.execute(_agent_pause_stmt(agent["id"], None))
            transitions += 1
            logger.info(f"[sweeper] agent {aid} unpaused (budget cleared)")

    return transitions


@DBOS.step()
async def force_settle_stale_pending_trees_step() -> int:
    """兜底：把过了宽限期仍没收口的 run 树捞出来再试一次。

    正常情况下收口由树里最后一个可观测事件触发（每条 run 的 ``_finish``、
    ``agent_worker`` 写完 ``subagent_done``、三个崩溃类终态写方）。捞不回来的有两类，
    都没有任何**其他**探针会说：

    * **派了但永远不会跑的异步任务** —— 收口要求全树 ``async_pending == 0``
      （workforce 异步派发不建子 run 行，「行全终态」不蕴含「树跑完了」），那个计数
      就永远减不回 0，钱永久不进账；
    * **盖了戳、扣费却抛异常** —— ``settle`` 会把戳撤回去，可那之后全树已经没有 run
      会再结束，崩溃写方也不会来。撤戳本身是对的，但**得有人重试**。

    所以提名条件**不看** ``async_pending`` —— 只要「是 root 行 + 终态 + 还没盖戳 +
    结束超过宽限期 + 在窗口内」就值得看一眼。判定全在
    :func:`~app.services.ai.billing.tree_charge.settle_tree_if_closed` 里（它重查全树
    状态、``async_pending``、宽限期、防回溯，并靠 root 行 CAS 保证只扣一次），所以提名
    宽一点只是多几次读，不会多扣一分钱。

    ⚠️ **提名下界是切换点，不是「7 天前」**（2026-09-17 事故）：上线后头两轮这一步把 81 棵
    **上线前**的历史树（``ended_at`` 在 09-10~09-15）提名了进来。它们结束于积分链修好
    之前，``point_transactions`` 里零行，于是 ``settle`` 那道「扣过钱没有」的正查放行，
    整棵扣掉 125 分 —— 违反用户裁定「只向前不追扣」。现在下界取
    ``max(now - FORCED_SETTLE_MAX_AGE, cutover)``，``settle`` 里还有一道按 root
    ``started_at`` 的同源判据（``pre_cutover``）。**两道都要在**：这里是提名侧的省事，
    那里是唯一的权威 —— 正常收口路径根本不经过这个函数。

    ⚠️ **`ORDER BY ended_at DESC`**：升序 + LIMIT 会让窗口里攒下的老树把新树饿死
    （它们每轮都被重提名、每轮都不动）。降序保证新结束的树永远排在前面。积压只可能
    来自「子 run 长期 running」那一类（树没终态，收口不成立而提名仍然命中），而它同样
    受 7 天上界约束 —— 所以积压有界，不会无限增长到把 LIMIT 长期占满。

    📌 **记票（3d）**：``agent_runs`` 上没有 ``ended_at`` 索引（现有 14 个索引全是
    ``heartbeat_at`` / ``started_at`` / ``created_at`` / 坐标列，且多数带
    ``status = 'running'`` 谓词，与这里的 ``!= 'running'`` 正相反），所以稳态下这是每
    60 秒一次全表扫 + top-N。本计划禁迁移，索引另立票：
    ``agent_runs(ended_at DESC) WHERE parent_run_id IS NULL``。
    """
    from app.db.session import read_scope
    from app.services.ai.billing.tree_charge import (
        FORCED_SETTLE_MAX_AGE,
        PENDING_CHILDREN_GRACE,
        cutover_at,
        settle_tree_if_closed,
    )

    now = datetime.now(timezone.utc)
    cutover = cutover_at()
    if cutover is None:
        # 切换点读不出来 = 不知道哪些树算历史树。什么都不提名（``cutover_at``
        # 已经刷过 ERROR），别凭一个坏配置去扣钱。
        return 0
    try:
        async with read_scope() as session:
            rows = (
                await session.execute(
                    _stale_tree_candidates_stmt(
                        older_than=now - PENDING_CHILDREN_GRACE,
                        # 两个下界取晚的那个：7 天窗口是给积压兜底的，切换点是
                        # 裁定的硬边界，谁更靠后听谁的。
                        newer_than=max(now - FORCED_SETTLE_MAX_AGE, cutover),
                    )
                )
            ).all()
    except Exception as exc:  # noqa: BLE001 — 兜底失败不该把这一轮清扫弄挂
        logger.warning(f"[sweeper] stale-tree scan failed: {exc}")
        return 0

    settled = 0
    #: 按 ``SettleOutcome.reason`` 分桶（终审 M7 / T12）。此前这一步只返回一个
    #: ``settled`` 计数，而它把 ``forced``（异步子 run 从没物化，靠强制收口捞回来）
    #: 和 ``charged``（撤戳之后重试成功）合成了同一个数 —— 两者意味着完全不同的
    #: 两件事，混在一起就谁也报不了警。2026-09-17 那天「一分钟内 43 棵」这个形状
    #: 是唯一能被自动抓住的信号，而当时只能靠人去数日志。
    buckets: dict[str, int] = {}
    for row in rows:
        try:
            out = await settle_tree_if_closed(
                run_id=str(row.id), force_stale_pending=True
            )
        except Exception as exc:  # noqa: BLE001
            # 既记明细（哪一棵）也进桶（这一轮炸了几棵）。只留明细的话，一轮里
            # 炸 1 棵和炸 20 棵在汇总上长得一模一样。
            logger.warning(f"[sweeper] forced settle {row.id} failed: {exc}")
            buckets["error"] = buckets.get("error", 0) + 1
            continue
        buckets[out.reason] = buckets.get(out.reason, 0) + 1
        if out.reason in ("forced", "charged"):
            settled += 1

    if buckets:
        summary = " ".join(f"{k}={v}" for k, v in sorted(buckets.items()))
        # 三级，按**这一轮到底发生了什么**分（评审 M-4）：
        #
        # * WARNING —— ``forced`` 稳态下应当恒为 0，非 0 就是「有异步任务在丢」；
        #   ``error`` 同理。这两样要能从噪声里跳出来，那是本段遥测存在的理由。
        # * INFO —— 这一轮真的收了一棵树（``charged``）。发生了事，值得留痕。
        # * DEBUG —— 其余全是「提名到了、但什么都没做」。
        #
        # ⚠️ 安静的那一档**不能只列 ``deferred``**。提名谓词是
        # ``charged_at IS NULL``，所以 ``legacy_charged`` / ``partially_charged`` /
        # ``pending_children`` 的树同样没有戳，同样会被每分钟重新提名一次，直到掉
        # 出 7 天窗口 —— 只放过 ``deferred`` 等于只堵了其中一条。判据因此是
        # 「有没有发生事」而不是「reason 叫什么」：一棵卡住的树会连打 7 天 × 1440
        # 行，那时这条遥测已经从信号退化成要过滤的噪声。
        if buckets.get("forced", 0) or buckets.get("error", 0):
            log = logger.warning
        elif buckets.get("charged", 0):
            log = logger.info
        else:
            log = logger.debug
        log(f"[sweeper] stale-tree settle: candidates={len(rows)} {summary}")
    # 提名到零棵树则一行都不打 —— 连 DEBUG 都不必，没有任何可说的。
    return settled


def _stale_tree_candidates_stmt(*, older_than: datetime, newer_than: datetime):
    """提名语句。抽成纯 builder，好让测试断言它带着那几个谓词 —— 少一个都不会报错，
    只会让兜底安静地退化（``charged_at IS NULL`` 少了就是重提名已收口的树并饿死新树，
    时间窗少了就是回溯扣历史 —— 后者 2026-09-17 真的发生过，125 分）。

    ``newer_than`` 由调用方算成 ``max(now - FORCED_SETTLE_MAX_AGE, cutover)``，所以
    这条语句本身不认识切换点；它只负责忠实地带上那个下界。"""
    from sqlalchemy import select

    from app.models import AgentRuns

    return (
        select(AgentRuns.id)
        # 只提名 root 行：收口自己会沿 ``root_run_id`` 解析整棵树，子行提名等于
        # 把同一棵树重复喂进来。
        .where(AgentRuns.parent_run_id.is_(None))
        .where(AgentRuns.status != "running")
        .where(AgentRuns.metadata_json["billing"]["charged_at"].astext.is_(None))
        .where(AgentRuns.ended_at < older_than)
        .where(AgentRuns.ended_at > newer_than)
        .order_by(AgentRuns.ended_at.desc())
        .limit(50)
    )


@DBOS.scheduled("* * * * *")  # every minute
@DBOS.workflow()
async def agent_runs_sweeper_workflow(
    scheduled_time: datetime, actual_time: datetime
) -> None:
    """Run a single sweeper tick. DBOS dedup via workflow_id =
    `sched-agent_runs_sweeper_workflow-<iso>` ensures only one worker
    fires per cron tick across the cluster."""
    heartbeat_lost = await mark_heartbeat_lost_step()
    transitions = await recompute_monthly_budgets_step()
    # Scan BEFORE expiring: an item that is both stranded and a day old should
    # get its turn rather than be thrown away by the step running beside it.
    # The dispatch itself happens HERE, in the body — never inside a step.
    drain = {"dispatched": 0, "busy": 0, "failed": 0, "inactive": 0}
    for order in await scan_idle_inbox_step():
        if order.get("inactive_expired"):
            # FH3 T1: only the agent's own wake-ups were pending on an issue
            # waiting on a person; the scan expired them. Nothing to dispatch.
            drain["inactive"] += 1
            continue
        await _drain_one_issue(order, drain)
    expired_inbox = await expire_orphan_inbox_step()
    reconciled = await reconcile_issue_execution_state_step()
    forced_settles = await force_settle_stale_pending_trees_step()
    preempted_waits = await reap_preempted_input_waits_step()
    stale_tasks = await reap_stale_workforce_tasks_step()
    # LAST, always — new steps go below this line, never above it.
    zombie_locks = await reap_zombie_locks_step()
    # Only outcomes that CHANGED something (or failed) make the tick worth a
    # line; a long healthy run is ``skipped_pending`` every minute and must not
    # turn a quiet tick into an INFO line forever.
    stale_acted = {
        k: v
        for k, v in stale_tasks.items()
        if k in ("done", "failed", "requeued", "raced", "errors") and v
    }
    if (
        heartbeat_lost
        or transitions
        or any(drain.values())
        or expired_inbox
        or reconciled
        or forced_settles
        or preempted_waits
        or stale_acted
        or zombie_locks
    ):
        logger.info(
            f"[sweeper] heartbeat_lost={heartbeat_lost} "
            f"budget_transitions={transitions} "
            f"inbox_drained={drain['dispatched']} inbox_busy={drain['busy']} "
            f"inbox_drain_failed={drain['failed']} "
            f"inbox_inactive={drain['inactive']} "
            f"expired_inbox={expired_inbox} reconciled_issues={reconciled} "
            f"forced_tree_settles={forced_settles} "
            f"preempted_waits_released={preempted_waits} "
            f"stale_workforce_tasks="
            f"{' '.join(f'{k}={v}' for k, v in sorted(stale_acted.items())) or 0}"
            + (f" zombie_locks_released={zombie_locks}" if zombie_locks else "")
        )
