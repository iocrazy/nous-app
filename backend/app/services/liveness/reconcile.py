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

# Keep the threshold default colocated with the helper that uses it.
# liveness_scanner.HEARTBEAT_DEAD_SECONDS still exists for the
# scheduled handler's own use; both read the same env var so they
# stay in sync without a cross-module import.
HEARTBEAT_DEAD_SECONDS = int(os.environ.get("LIVENESS_HEARTBEAT_DEAD_SECONDS", "120"))


async def reconcile_stranded_runs() -> dict[str, int]:
    """One-shot startup sweep. Safe to call at any time; idempotent.

    Marks every ``agent_runs`` row with ``status='running'`` and a
    ``heartbeat_at`` older than ``HEARTBEAT_DEAD_SECONDS`` as
    ``status='failed'`` / ``liveness_state='dead'``. The migration-206
    bridge trigger then surfaces these as chat messages in any
    associated issue thread.
    """
    from app.db import engine as db_engine

    if not db_engine.is_configured():
        return {"reconciled": 0}

    cutoff = datetime.now(timezone.utc) - timedelta(seconds=HEARTBEAT_DEAD_SECONDS)

    # Single UPDATE (direct PG via SQLAlchemy): mark every stranded run dead;
    # rowcount = how many we touched. Replaces the supabase-py
    # select-then-update (httpx CLOSE_WAIT leak, Issue #199 Bug C).
    n = await db_engine.execute(
        "UPDATE public.agent_runs SET liveness_state = 'dead', "
        "status = 'failed', ended_at = :ended, error_code = 'stranded_on_restart', "
        "error_message = :msg "
        "WHERE status = 'running' AND heartbeat_at < :cutoff",
        {
            "ended": datetime.now(timezone.utc),
            "msg": "Backend restarted while this run was in flight; no heartbeat for >2 minutes.",
            "cutoff": cutoff,
        },
    )
    if n > 0:
        logger.warning(
            f"[liveness-reconcile] marked {n} stranded run(s) dead on startup"
        )
    return {"reconciled": n}


__all__ = ["reconcile_stranded_runs", "HEARTBEAT_DEAD_SECONDS"]
