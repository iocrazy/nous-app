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


async def _bg_work_dir_probe() -> None:
    """The media work dir is a real, writable, mounted volume — or readiness
    degrades. Raising is the point: see startup/work_dir_probe.py."""
    from app.core.config import settings
    from app.startup.work_dir_probe import probe_work_dir

    probe_work_dir(
        settings.DOWNLOAD_PATH, require_marker=settings.MEDIA_WORK_DIR_REQUIRE_MARKER
    )


async def _bg_schema_probe() -> None:
    """P0-1: warn-only sanity probe for migration drift."""
    try:
        from sqlalchemy import literal, select

        from app.db.session import read_scope
        from app.models import AgentCommitments, AiSessionMemory, UserMcpServers

        # extend on each migration that adds a hard-required table
        required = [
            AgentCommitments,  # mig 186
            AiSessionMemory,  # mig 187
            UserMcpServers,  # mig 194
        ]
        # ``SELECT 1 FROM <table> LIMIT 0`` — table-existence probe only (no
        # column assertion), matching the legacy select("*").limit(0) reach.
        async with read_scope() as session:
            for model in required:
                await session.execute(select(literal(1)).select_from(model).limit(0))
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
    from sqlalchemy import insert, select

    from app.db.session import read_scope, write_scope
    from app.models import DeploymentLogs

    async with read_scope() as session:
        exists = (
            await session.execute(
                select(DeploymentLogs.id)
                .where(DeploymentLogs.service == "backend")
                .where(DeploymentLogs.commit_sha == sha)
                .limit(1)
            )
        ).first()
    if exists is not None:
        logger.info(f"Deployment {sha} already logged, skip")
        return
    commit_count = int(info.get("commit_count") or 0)
    async with write_scope() as session:
        await session.execute(
            insert(DeploymentLogs).values(
                service=info.get("service", "backend"),
                version=info.get("version") or "latest",
                commit_sha=sha,
                commit_count=commit_count,
                commits=info.get("commits") or [],
                summary=info.get("summary") or "",
                deployed_by=info.get("deployed_by") or "ci",
                status="success",
                metadata_={"run_id": info.get("run_id")},
            )
        )
    logger.success(f"Deployment logged: {sha} ({commit_count} commits)")


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

    from app.startup.env_utils import env_int

    interval = env_int("DBOS_REAP_INTERVAL_SECONDS", 120)

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


async def _bg_reap_stale_input_waits() -> None:
    """One-shot startup sweep: clear cross-version fake-alive needs_input waits.

    A deploy bumps the DBOS app version; workflows suspended on the input gate
    under the OLD version are never recovered by the new workers — the
    awaiting_input marker stays on, so replies keep getting sent into the void
    instead of falling back to the legacy respond_to_issue_reply path. The
    sweep (``input_gate.reap_stale_input_waits``) clears the marker, cancels
    the workflow, and releases the issue's execution lock. Delayed ~30s so
    DBOS launch (needed for cancel) has settled — same pattern as the
    internal-queue reaper's prompt-first-run.
    """
    import asyncio

    await asyncio.sleep(30)
    try:
        from app.agent_framework.input_gate import reap_stale_input_waits
        from app.services.infra.dbos_orchestrator import _resolve_pinned_app_version

        current = _resolve_pinned_app_version()
        if not current:
            # Prod reality (2026-08-03 首次发版实测): /app/build-info.json is not
            # baked into the image, so the pinned resolver returns None on every
            # boot and the sweep would NEVER run — leaving post-deploy stale
            # waits eating replies. Fall back to the version DBOS itself
            # computed at launch (GlobalParams.app_version, set by the time
            # this 30s-delayed task runs); it's exactly the value stamped onto
            # this process's workflow_status rows, which is the comparison the
            # reaper needs.
            try:
                from dbos._utils import GlobalParams

                current = GlobalParams.app_version or None
            except Exception:  # noqa: BLE001 — private-ish import, keep soft
                current = None
        if not current:
            logger.info(
                "reap_stale_input_waits: no app version resolvable "
                "(build-info absent and DBOS not launched); skipping"
            )
            return
        n = await reap_stale_input_waits(current_version=current)
        if n:
            logger.info(f"reap_stale_input_waits: reaped {n} cross-version wait(s)")
    except Exception as exc:  # noqa: BLE001 — best-effort, never break startup
        logger.warning(f"reap_stale_input_waits failed: {exc!r}")


async def _bg_memory_warmup() -> None:
    """Pre-warm the memory-recall connection pools so the FIRST chat turn after
    a restart doesn't pay cold-start latency on the synchronous hot path.

    Memory recall (graph + Honcho) is gathered before the prompt on every chat
    turn. Its inner timeouts (graph search 3s) and the call-site budget (4s)
    bound the *common* cold case, but the deepest-cold moment — a freshly
    recreated backend container AND a cold qwen embedder box at the same time —
    once produced a ~12s spike that slipped past those async timeouts (the cold
    embed/connection setup blocked uncancellably). Firing one throwaway recall
    here moves that one-time cold cost off a user's turn and into background
    startup. Fully best-effort: any failure is swallowed."""
    try:
        from uuid import uuid4

        from app.services.ai.chat.ai_library_chat_wiring import (
            _safe_recall_graph_facts,
            _safe_recall_honcho_context,
        )

        uid = uuid4()
        await _safe_recall_graph_facts(
            user_id=uid, user_query="warmup", session_id=None
        )
        await _safe_recall_honcho_context(user_id=str(uid), session_id=None)
        logger.info("[bootstrap] memory recall pools warmed")
    except Exception as exc:  # noqa: BLE001 — warmup must never affect startup
        logger.warning(f"memory warmup failed (non-fatal): {exc!r}")


async def _bg_secrets_selfheal() -> None:
    """Secret-at-rest self-heal (see app.services.infra.secrets_selfheal).

    When NO real MEDIAHUB_TOKEN_ENCRYPTION_KEY is configured this is where
    the long-promised boot warning fires (secret_box's docstring promised it
    since P7 but it was never wired — is_configured() had zero non-test
    callers): MCP bearer tokens fall back to the PUBLIC committed dev key
    and settings-secret writes fail closed. When a real key IS configured,
    run one idempotent sweep that re-encrypts plaintext / dev-keyed secrets
    under it (marker check ⇒ a second pass rewrites 0 rows).
    """
    try:
        from app.core import secret_box

        if not secret_box.is_configured():
            logger.warning(
                "MEDIAHUB_TOKEN_ENCRYPTION_KEY is NOT set — MCP bearer tokens "
                "fall back to the PUBLIC dev key committed in this repo and "
                "settings secrets are NOT protected at rest. Set "
                "MEDIAHUB_TOKEN_ENCRYPTION_KEY (keep the old key as "
                "MEDIAHUB_TOKEN_ENCRYPTION_KEY_OLD during rotation)."
            )
            return
        from app.services.infra.secrets_selfheal import run_secrets_selfheal

        await run_secrets_selfheal()
    except Exception as exc:  # noqa: BLE001 — healer must never break startup
        logger.warning(f"secrets selfheal on startup failed: {exc!r}")


def install_background_bootstrap(app: FastAPI) -> None:
    """Spawn all background bootstrap tasks into `app.state.bg_tasks`."""
    app.state.bg_tasks = BackgroundTaskRegistry()
    app.state.bg_tasks.spawn("schema_probe", _bg_schema_probe())
    # fatal: a shadow-dir / unwritable work dir is refused, not run on.
    app.state.bg_tasks.spawn("work_dir_probe", _bg_work_dir_probe(), fatal=True)
    app.state.bg_tasks.spawn("secrets_selfheal", _bg_secrets_selfheal())
    app.state.bg_tasks.spawn("seed_loader", _bg_seed_loader())
    app.state.bg_tasks.spawn("deployment_log", _bg_deployment_log())
    app.state.bg_tasks.spawn("liveness_reconcile", _bg_liveness_reconcile())
    # long_running: while-True sweep loop — exempt from the /readyz gate or
    # readiness would report 503 "starting" for the whole process lifetime.
    app.state.bg_tasks.spawn(
        "reap_internal_queue", _bg_reap_internal_queue(), long_running=True
    )
    # Detached housekeeping: sleeps 30s before its single sweep, so readiness
    # must not wait for it — but it IS finite. Registering it as a daemon
    # (long_running=True, the original #1662 wiring) made `dead_daemons()`
    # read its normal return as a crash: /readyz went permanently
    # "degraded"/503, both containers sat (unhealthy), and the deploy smoke
    # gate only passed by racing the 30s sleep. gates_readiness=False is the
    # flag that actually expresses "don't gate on me".
    app.state.bg_tasks.spawn(
        "reap_stale_input_waits",
        _bg_reap_stale_input_waits(),
        gates_readiness=False,
    )
    # Worker-stall detector: only on the HTTP-serving process (gateway /
    # combined), which stays healthy during a worker dequeue stall and can
    # observe the backlog + alert. Plain asyncio (not @DBOS.scheduled) so it
    # can't be stalled by the same queue it watches. Single observer avoids
    # double alerts in the gateway/worker split.
    if role_from_env().serves_http_api:
        app.state.bg_tasks.spawn(
            "stall_detector", _bg_stall_detector(), long_running=True
        )
        # Memory recall runs on the chat hot path, served by this process —
        # warm its pools here so the first turn after a restart isn't cold.
        app.state.bg_tasks.spawn("memory_warmup", _bg_memory_warmup())
