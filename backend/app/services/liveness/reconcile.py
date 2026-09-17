"""Startup reconcile for stranded agent_runs.

Originally lived in ``app.workflows.liveness_scanner`` next to the
``@DBOS.scheduled`` handler. Moved here (2026-05-27) because the
gateway process imports this helper at startup via
``app.startup.bootstrap``, and importing ``liveness_scanner`` also
fires the module-level ``@DBOS.scheduled("*/30 * * * * *")``
decorator — exactly the leak that the P1 role-split workflow change
was meant to prevent. This file has zero DBOS decorators, so it is
safe to import from any process role.

The scheduled handler in ``liveness_scanner`` still calls this
function, but the import direction is now one-way: scheduled handler
imports reconcile (worker only), bootstrap imports reconcile
(every role) — neither importer pulls the decorator-bearing module
unless they have to.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

from loguru import logger

from app.services.ai.runner.turn_end import TurnEndReason

# Keep the threshold default colocated with the helper that uses it.
# liveness_scanner.HEARTBEAT_DEAD_SECONDS still exists for the
# scheduled handler's own use; both read the same env var so they
# stay in sync without a cross-module import.
HEARTBEAT_DEAD_SECONDS = int(os.environ.get("LIVENESS_HEARTBEAT_DEAD_SECONDS", "120"))


async def reconcile_stranded_runs() -> dict[str, int]:
    """One-shot startup sweep. Safe to call at any time; idempotent.

    Marks every ``agent_runs`` row with ``status='running'`` and a
    ``heartbeat_at`` older than ``HEARTBEAT_DEAD_SECONDS`` as
    ``status='failed'`` / ``liveness_state='dead'`` /
    ``turn_end_reason='stranded'``. The migration-206 bridge trigger then
    surfaces these as chat messages in any associated issue thread.

    每一行还会补上检索投影与 ``ai_usage_hourly`` 的那一行样本 —— 这些 run 永远
    不会走到 ``RunRecorder._finish``，不在这里补就是两处都数不到它们（3c 终审
    I4 / K8）。
    """
    from app.db import engine as db_engine

    if not db_engine.is_configured():
        return {"reconciled": 0}

    cutoff = datetime.now(timezone.utc) - timedelta(seconds=HEARTBEAT_DEAD_SECONDS)

    from sqlalchemy import update

    from app.db.session import write_scope
    from app.models import AgentRuns

    # Single UPDATE (ORM, Phase B4): mark every stranded run dead; rowcount =
    # how many we touched. Replaces the supabase-py select-then-update (httpx
    # CLOSE_WAIT leak, Issue #199 Bug C).
    async with write_scope() as session:
        result = await session.execute(
            update(AgentRuns)
            .where(AgentRuns.status == "running", AgentRuns.heartbeat_at < cutoff)
            .values(
                liveness_state="dead",
                status="failed",
                ended_at=datetime.now(timezone.utc),
                error_code="stranded_on_restart",
                error_message=(
                    "Backend restarted while this run was in flight; no "
                    "heartbeat for >2 minutes."
                ),
                # 3c 终审 I4：这一列不写，这些 run 在 UsagePage 的 turn_end 分布
                # 条上根本不存在（那条查询带 ``turn_end_reason IS NOT NULL``）。
                turn_end_reason=TurnEndReason.STRANDED.value,
            )
            # 维度随行返回，供检索投影与小时表那一行使用 —— 这些 run 永远不会
            # 走到 ``RunRecorder._finish``，两件事都只能由这里跟上。
            .returning(
                AgentRuns.id,
                AgentRuns.team_id,
                AgentRuns.project_id,
                AgentRuns.agent_id,
                AgentRuns.model,
                AgentRuns.trigger,
                AgentRuns.attribution,
            )
        )
        rows = result.fetchall()
    n = len(rows)
    if n > 0:
        logger.warning(
            f"[liveness-reconcile] marked {n} stranded run(s) dead on startup"
        )
        # ``write_scope()`` 之外：投影要读回刚提交的那些行，而两者失败都不该
        # 连坐一次已经成立的终态写入（同 ``liveness_scanner._mark_dead``）。
        # 3c 终审 K8：此前这一处连检索投影都没接。
        from app.services.liveness.crash_rollup import record_crash_terminal_runs
        from app.services.search.projection import project_run_id_best_effort

        for row in rows:
            await project_run_id_best_effort(int(row.id))
        await record_crash_terminal_runs(rows)
    return {"reconciled": n}


__all__ = ["reconcile_stranded_runs", "HEARTBEAT_DEAD_SECONDS"]
