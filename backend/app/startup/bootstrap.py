"""Background bootstrap tasks — moved out of the critical startup path.

These tasks spawn into `app.state.bg_tasks` (`BackgroundTaskRegistry`) so
cold start is <2s. They cover:

- `schema_probe`: warn on migration drift (P0-1)
- `seed_loader`: AI Library agents + skills from `backend/seeds/`
- `deployment_log`: record CI-baked `build-info.json` (no-op in dev)
- `liveness_reconcile`: paperclip-style sweep of stranded agent_runs
- `reap_internal_queue`: cancel stale `_dbos_internal_queue` PENDING rows
  (path C, 2026-05-05 — see CLAUDE.md task system 第 5 条)
"""

from pathlib import Path

from fastapi import FastAPI
from loguru import logger

from app.agent_framework.role import role_from_env
from app.lifespan_helpers import BackgroundTaskRegistry
from app.startup.stall_detector import _bg_stall_detector


async def _bg_schema_probe() -> None:
    """P0-1: warn-only sanity probe for migration drift."""
    try:
        from app.db import get_async_supabase_admin

        sb = await get_async_supabase_admin()
        required_tables = [
            "agent_commitments",  # mig 186
            "ai_session_memory",  # mig 187
            "user_mcp_servers",  # mig 194
        ]  # extend on each migration that adds a hard-required table
        for table in required_tables:
            probe = await sb.table(table).select("*", count="exact").limit(0).execute()
            if not hasattr(probe, "data"):
                logger.warning(
                    f"Schema probe: table '{table}' is unreachable — "
                    f"check that the corresponding migration was applied"
                )
    except Exception as e:
        logger.warning(f"Schema probe failed (likely missing migration): {e}")


async def _bg_seed_loader() -> None:
    """Load AI Library seeds (agents + skills) from backend/seeds/.

    Idempotent via seed_hash columns (mig 200): on repeat starts the
    loader skips entities whose source files haven't changed.
    """
    seeds_root = Path(__file__).resolve().parent.parent.parent / "seeds"
    logger.info(
        f"seed_loader: entering (seeds_root={seeds_root}, "
        f"exists={seeds_root.exists()})"
    )
    from app.repositories.agent_repository import get_agent_repository
    from app.repositories.skill_repository import get_skill_repository
    from app.services.ai.runner.seed_loader import SeedLoader

    seed_loader = SeedLoader(
        agent_repo=get_agent_repository(),
        skill_repo=get_skill_repository(),
        seeds_root=seeds_root,
    )
    seed_results = await seed_loader.load_all()
    logger.info(f"seed_loader: exiting, results={seed_results}")


async def _bg_deployment_log() -> None:
    """Record deployment log from CI-baked build-info.json (no-op in dev)."""
    import json

    build_info_path = Path("/app/build-info.json")
    if not build_info_path.exists():
        logger.debug("build-info.json not found, skip deployment log")
        return
    info = json.loads(build_info_path.read_text(encoding="utf-8"))
    sha = info.get("commit_sha")
    if not sha:
        return
    from app.db import get_async_supabase_admin

    sb = await get_async_supabase_admin()
    exists = await (
        sb.table("deployment_logs")
        .select("id")
        .eq("service", "backend")
        .eq("commit_sha", sha)
        .limit(1)
        .execute()
    )
    if exists.data:
        logger.info(f"Deployment {sha} already logged, skip")
        return
    row = {
        "service": info.get("service", "backend"),
        "version": info.get("version") or "latest",
        "commit_sha": sha,
        "commit_count": int(info.get("commit_count") or 0),
        "commits": info.get("commits") or [],
        "summary": info.get("summary") or "",
        "deployed_by": info.get("deployed_by") or "ci",
        "status": "success",
        "metadata": {"run_id": info.get("run_id")},
    }
    await sb.table("deployment_logs").insert(row).execute()
    logger.success(f"Deployment logged: {sha} ({row['commit_count']} commits)")


async def _bg_liveness_reconcile() -> None:
    """Paperclip-style stranded-run reconcile (A8.5).

    Backend may have crashed while agent_runs were in flight; sweep them
    to status=failed + liveness=dead so the chat reflects reality and the
    user can retry.
    """
    try:
        # Import via app.services.liveness.reconcile so we do NOT pull
        # app.workflows.liveness_scanner (which fires a 30s
        # @DBOS.scheduled decorator at import time). bootstrap runs on
        # every role including gateway — see 2026-05-27 fix.
        from app.services.liveness.reconcile import reconcile_stranded_runs

        await reconcile_stranded_runs()
    except Exception as exc:
        logger.warning(f"liveness reconcile on startup failed: {exc!r}")


def build_reap_predicates() -> dict[str, str]:
    """Return the two WHERE-clause predicates the internal-queue reaper uses.

    Pure (no I/O) so it is unit-testable. Each value is a parenthesised SQL
    boolean expression intended to be OR-joined inside the reaper UPDATE.

    - **dead_gateway** (any age, provably-dead): the gateway is FORCED by DBOS
      to consume `_dbos_internal_queue` even with `listen_queues([])`. It
      dequeues worker-enqueued ticks, stamps them PENDING with
      `executor_id='gateway'`, but cannot execute them (it never imports
      `_scheduled_bundle`) and DBOS recovery is executor-scoped, so the worker
      never reclaims a 'gateway' row. Any such row is dead regardless of age.
      A real long-running task runs on the worker (`executor_id != 'gateway'`)
      or has `queue_name` NULL (start_workflow_routed), so it is never matched.
    - **stale_sched** (age-gated, sched-* only): stateless scheduled ticks
      superseded by newer ticks. Age is used ONLY for sched-* ticks, never for
      arbitrary user workflows. Uses the same created_at-epoch-millis shape the
      reaper has always used for the age gate. DBOS sets `name` to the function
      qualname (e.g. `agent_runs_sweeper_workflow`) — it is the `workflow_uuid`
      that carries the `sched-<fn>-<iso>` prefix (dbos/_scheduler.py), so the
      sched-* match must be on `workflow_uuid`, not `name` (matching the
      pre-launch sweep in dbos_orchestrator.py).
    """
    return {
        "dead_gateway": (
            "queue_name = '_dbos_internal_queue' "
            "AND status = 'PENDING' "
            "AND executor_id = 'gateway'"
        ),
        "stale_sched": (
            "queue_name = '_dbos_internal_queue' "
            "AND status = 'ENQUEUED' "
            "AND workflow_uuid LIKE 'sched-%' "
            "AND created_at < "
            "(EXTRACT(EPOCH FROM NOW() - INTERVAL '5 minutes') * 1000)::bigint"
        ),
    }


def _run_reap_sweep() -> int:
    """Run one blocking reaper sweep; return the count of cancelled rows.

    Sync (psycopg) so the caller can offload it to a worker thread via
    asyncio.to_thread. No-op (returns 0) when DBOS_DATABASE_URL is unset.
    """
    import os

    import psycopg

    dsn = os.environ.get("DBOS_DATABASE_URL")
    if not dsn:
        return 0
    preds = build_reap_predicates()
    where_clause = f"({preds['dead_gateway']}) OR ({preds['stale_sched']})"
    with psycopg.connect(dsn, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                UPDATE dbos.workflow_status
                   SET status = 'CANCELLED',
                       updated_at =
                           (EXTRACT(EPOCH FROM NOW()) * 1000)::bigint
                 WHERE {where_clause}
                RETURNING workflow_uuid
                """
            )
            return len(cur.fetchall())


async def _bg_reap_internal_queue() -> None:
    """Periodically cancel provably-dead `_dbos_internal_queue` rows (path C).

    Two predicates (see `build_reap_predicates`):
    - dead_gateway: gateway-stranded PENDING ticks (any age — the gateway can
      never execute a dequeued workflow, so these are provably dead).
    - stale_sched: sched-* ENQUEUED ticks older than 5 minutes (superseded by
      newer ticks).

    Neither predicate ever age-guesses an arbitrary user workflow: a live long
    task runs on the worker (executor_id != 'gateway') or has queue_name NULL.

    Runs once promptly on start, then every DBOS_REAP_INTERVAL_SECONDS (default
    120s). Each iteration is independently guarded so one failed sweep does not
    kill the loop. The blocking psycopg work runs off the event loop via
    asyncio.to_thread so a slow DB never stalls the async runtime.
    """
    import asyncio
    import os

    interval = int(os.environ.get("DBOS_REAP_INTERVAL_SECONDS", "120"))

    while True:
        try:
            affected = await asyncio.to_thread(_run_reap_sweep)
            if affected:
                logger.info(
                    f"reap_internal_queue: cancelled {affected} provably-dead "
                    f"_dbos_internal_queue rows (gateway-stranded + stale sched-*)"
                )
        except Exception as exc:
            logger.warning(f"reap_internal_queue sweep failed: {exc!r}")
        await asyncio.sleep(interval)


def install_background_bootstrap(app: FastAPI) -> None:
    """Spawn all background bootstrap tasks into `app.state.bg_tasks`."""
    app.state.bg_tasks = BackgroundTaskRegistry()
    app.state.bg_tasks.spawn("schema_probe", _bg_schema_probe())
    app.state.bg_tasks.spawn("seed_loader", _bg_seed_loader())
    app.state.bg_tasks.spawn("deployment_log", _bg_deployment_log())
    app.state.bg_tasks.spawn("liveness_reconcile", _bg_liveness_reconcile())
    app.state.bg_tasks.spawn("reap_internal_queue", _bg_reap_internal_queue())
    # Worker-stall detector: only on the HTTP-serving process (gateway /
    # combined), which stays healthy during a worker dequeue stall and can
    # observe the backlog + alert. Plain asyncio (not @DBOS.scheduled) so it
    # can't be stalled by the same queue it watches. Single observer avoids
    # double alerts in the gateway/worker split.
    if role_from_env().serves_http_api:
        app.state.bg_tasks.spawn("stall_detector", _bg_stall_detector())
