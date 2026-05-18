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

from app.lifespan_helpers import BackgroundTaskRegistry


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
    from app.repositories.agent_repository import AgentRepository
    from app.repositories.skill_repository import SkillRepository
    from app.services.ai.runner.seed_loader import SeedLoader

    seed_loader = SeedLoader(
        agent_repo=AgentRepository(),
        skill_repo=SkillRepository(),
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
        from app.workflows.liveness_scanner import reconcile_stranded_runs

        await reconcile_stranded_runs()
    except Exception as exc:
        logger.warning(f"liveness reconcile on startup failed: {exc!r}")


async def _bg_reap_internal_queue() -> None:
    """Cancel stranded `_dbos_internal_queue` PENDING/ENQUEUED rows (path C).

    Every backend restart abandons whatever scheduled housekeeping
    workflows were enqueued at the time. They sit forever in
    dbos.workflow_status with status='PENDING' on _dbos_internal_queue,
    eventually polluting any code that introspects the queue. This sweep
    marks anything older than 5 minutes as CANCELLED so the table stops
    growing across restarts. User-queued workflows are left alone.
    """
    try:
        import os

        import psycopg

        dsn = os.environ.get("DBOS_DATABASE_URL")
        if not dsn:
            return
        with psycopg.connect(dsn, autocommit=True) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE dbos.workflow_status
                       SET status='CANCELLED'
                     WHERE queue_name='_dbos_internal_queue'
                       AND status IN ('PENDING','ENQUEUED')
                       AND created_at <
                           (EXTRACT(EPOCH FROM NOW() - INTERVAL '5 minutes') * 1000)::bigint
                    """
                )
                affected = cur.rowcount or 0
        if affected:
            logger.info(
                f"reap_internal_queue: cancelled {affected} stranded "
                f"_dbos_internal_queue PENDING/ENQUEUED rows"
            )
    except Exception as exc:
        logger.warning(f"reap_internal_queue startup sweep failed: {exc!r}")


def install_background_bootstrap(app: FastAPI) -> None:
    """Spawn all background bootstrap tasks into `app.state.bg_tasks`."""
    app.state.bg_tasks = BackgroundTaskRegistry()
    app.state.bg_tasks.spawn("schema_probe", _bg_schema_probe())
    app.state.bg_tasks.spawn("seed_loader", _bg_seed_loader())
    app.state.bg_tasks.spawn("deployment_log", _bg_deployment_log())
    app.state.bg_tasks.spawn("liveness_reconcile", _bg_liveness_reconcile())
    app.state.bg_tasks.spawn("reap_internal_queue", _bg_reap_internal_queue())
