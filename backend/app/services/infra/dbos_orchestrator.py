"""DBOS Orchestrator — central place that owns DBOS instance + routing decisions.

Responsibilities:
    - Initialize the DBOS singleton from `DBOS_DATABASE_URL` env var
    - Read & cache the `dbos_workflow_routing` table; expose a
      `get_routing(task_type)` helper that returns the per-task mode
    - Provide `start_workflow_routed(task_type, ...)` — the only dispatcher.
      After Celery removal there is no fallback path: dispatch raises if
      the routing row says anything other than 'dbos', or if DBOS itself
      is not enabled (missing DBOS_DATABASE_URL).

The `dbos_workflow_routing.mode` column historically held 'celery' /
'shadow' / 'dbos' for the migration window; the dispatcher now only
honours 'dbos' and raises otherwise. The column itself is kept as a
kill-switch — set mode='off' (or any non-'dbos' value) to force dispatch
to fail without modifying code, useful for emergency disables.
"""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from typing import Any, Callable, Optional

from loguru import logger

from app.db import get_async_supabase_admin

# DBOS instance — set by `init_dbos`; None until lifespan startup runs.
_dbos = None
_routing_cache: dict[str, str] = {}
_routing_loaded_at: float = 0.0
_routing_refresh_interval_s: float = 60.0  # poll dbos_workflow_routing every 60s


@dataclass(frozen=True)
class RoutingDecision:
    task_type: str
    mode: str  # 'dbos' (or any other value to disable, see start_workflow_routed)
    notes: Optional[str] = None


async def _refresh_routing_cache() -> None:
    """Reload dbos_workflow_routing into the in-process cache."""
    global _routing_cache, _routing_loaded_at
    try:
        client = await get_async_supabase_admin()
        result = (
            await client.table("dbos_workflow_routing")
            .select("task_type, mode")
            .execute()
        )
        rows = result.data or []
        _routing_cache = {row["task_type"]: row["mode"] for row in rows}
        _routing_loaded_at = asyncio.get_event_loop().time()
        logger.info(f"[dbos] routing cache refreshed: {len(_routing_cache)} entries")
    except Exception as e:
        logger.warning(
            f"[dbos] routing cache refresh failed: {e!r} — keeping previous cache"
        )


async def get_routing(task_type: str) -> RoutingDecision:
    """Look up routing for a task_type. Unknown task_types default to 'dbos'
    (after Celery removal, that's the only working mode anyway — being
    permissive avoids breaking new task_types that haven't been added to
    the routing table yet)."""
    now = asyncio.get_event_loop().time()
    if not _routing_cache or (now - _routing_loaded_at) > _routing_refresh_interval_s:
        await _refresh_routing_cache()
    mode = _routing_cache.get(task_type, "dbos")
    return RoutingDecision(task_type=task_type, mode=mode)


_BUILD_INFO_PATH = "/app/build-info.json"


def _resolve_pinned_app_version() -> str | None:
    """Shared, per-deploy DBOS application_version from the baked build-info
    commit_sha. Both gateway and worker run the same image → same commit_sha →
    same version → the worker can claim workflows the gateway enqueues. Returns
    None (DBOS computes its own version) when build-info is absent — e.g. dev,
    which runs the single `combined` role where there's no cross-process
    enqueue/dequeue to mismatch.
    """
    import json
    from pathlib import Path

    p = Path(_BUILD_INFO_PATH)
    if not p.exists():
        return None
    try:
        sha = json.loads(p.read_text(encoding="utf-8")).get("commit_sha")
        return str(sha) if sha else None
    except Exception:
        return None


def init_dbos(executor_id: str | None = None) -> None:
    """Initialize the DBOS singleton (idempotent). Called from FastAPI lifespan
    BEFORE workflow modules are imported (decorators register against the
    singleton at import time).

    ``executor_id`` (when given) is a STABLE identity for this process, set on
    DBOSConfig. DBOS's startup recovery claims pending workflows by
    ``(executor_id, app_version)`` — a path that ignores ``listen_queues`` — so
    distinct ids per role keep a gateway restart from re-running the worker's
    in-flight workflows. Must be stable across restarts (we pass the role name,
    e.g. "gateway"/"worker", NOT a per-process uuid). Default leaves DBOS's own
    ``DBOS__VMID``-or-"local" behavior. See
    docs/superpowers/plans/2026-06-02-gateway-enqueue-only.md.
    """
    global _dbos
    if _dbos is not None:
        return

    db_url = os.environ.get("DBOS_DATABASE_URL")
    if not db_url:
        logger.warning("[dbos] DBOS_DATABASE_URL not set; DBOS orchestrator disabled")
        return

    from dbos import DBOS, DBOSConfig

    # Co-locate DBOS sys tables (workflow_status, operation_outputs,
    # workflow_events, etc.) in the SAME database as the app, in the
    # `dbos` schema. Default behaviour creates a separate
    # `<db>_dbos_sys` database, which would prevent Supabase Realtime
    # from broadcasting workflow_status changes (Realtime only
    # publishes from the configured app DB). Co-locating lets us add
    # `dbos.workflow_status` to supabase_realtime publication and
    # have the frontend Task Center subscribe natively.
    # Pool sizing: DBOS defaults to 20 conns per worker. The dev pooler
    # (Supavisor in Session mode on NAS, reached over SSH tunnel) has a
    # low pool_size cap — two reload cycles can blow past it and cause
    # the "MaxClientsInSessionMode: max clients reached" cascade that
    # locks up the dev session. Cap dev pool low; prod uses the env knob
    # to bump back up if needed.
    db_pool_size = int(os.environ.get("DBOS_DB_POOL_SIZE", "5"))

    cfg: DBOSConfig = {
        "name": "mediahub",
        "application_database_url": db_url,
        "system_database_url": db_url,
        "db_engine_kwargs": {
            "pool_size": db_pool_size,
            "max_overflow": 0,
            "pool_pre_ping": True,
            "pool_recycle": 300,  # 5 min — drop stale tunneled connections
        },
    }
    if executor_id:
        cfg["executor_id"] = executor_id
    # Pin a SHARED application_version across gateway + worker. DBOS dequeues
    # queued workflows filtered by application_version; gateway and worker
    # register different workflow sets (gateway skips _scheduled_bundle), so
    # DBOS would compute DIFFERENT versions per role — and a download the
    # gateway enqueues would never be claimed by the worker (orphaned →
    # marked 'lost' after 1h). Both roles run the same image, so the baked
    # build-info commit_sha is a stable, shared, per-deploy version.
    pinned = _resolve_pinned_app_version()
    if pinned:
        cfg["application_version"] = pinned
        logger.info(f"[dbos] pinned application_version={pinned}")
    _dbos = DBOS(config=cfg)
    logger.info(
        f"[dbos] singleton instantiated (sys + app share same DB, "
        f"pool_size={db_pool_size})"
    )


def launch_dbos(consume_queues: bool = True) -> None:
    """Start the DBOS worker pool + run pending-workflow recovery. Call AFTER
    all `@DBOS.workflow` modules have been imported.

    ``consume_queues=False`` (gateway role) launches DBOS so the process can
    still ENQUEUE workflows (dispatch needs ``_sys_db``), but restricts it to
    zero user queues via ``DBOS.listen_queues([])`` so it never dequeues /
    executes them. The worker container consumes the queues. See
    docs/superpowers/plans/2026-06-02-gateway-enqueue-only.md.

    Also runs a pre-launch sweep of stale internal scheduled workflows
    (see `_pre_launch_sweep_stale_scheduled`) to prevent recovery storms.
    DBOS's recovery on startup tries to re-execute every workflow that
    was RUNNING when the previous worker died; if the previous worker
    accumulated a large backlog of stuck PENDING workflows from earlier
    crashes, the recovery storm exhausts the executor's thread pool and
    every subsequent dispatch (including new user requests) gets stuck
    in PENDING forever — diagnosed on 2026-05-04 dev session.

    The pre-launch sweep cancels DBOS-internal `sched-*` workflows
    older than a safe cutoff so they don't pile into recovery. User-
    facing workflows are NEVER touched by this sweep — they go through
    the workflow_health_sweeper (PR #151) which respects
    do_not_auto_cancel.
    """
    if _dbos is None:
        return
    from dbos import DBOS

    _pre_launch_sweep_stale_scheduled()

    if not consume_queues:
        # Enqueue-only: service no user queues. Must precede launch(); the
        # SDK raises if called after launch or more than once.
        DBOS.listen_queues([])
        logger.info("[dbos] enqueue-only mode — listening to no user queues")

    DBOS.launch()
    logger.info("[dbos] launched (worker pool started, recovery complete)")


def _pre_launch_sweep_stale_scheduled() -> None:
    """Cancel internal scheduled workflows (`sched-*`) that have been
    PENDING/ENQUEUED for more than the safe-cutoff window.

    Why only `sched-*` (DBOS-internal)?
      * Each `@DBOS.scheduled` decorator generates a workflow per cron
        tick. If the previous worker died with N ticks queued, the new
        worker will recover all of them in a burst. None of them
        carries user state — they're meant to fire once per tick and
        succeeding ticks supersede them anyway.
      * User-facing workflows (parse, download, transcribe, agent_run)
        carry irreplaceable state and MUST NOT be auto-cancelled here.
        The PR #151 workflow_health_sweeper handles those with the
        do_not_auto_cancel safeguard the user requested.

    Cutoff: 3 minutes. Anything older is presumed dead — newer ticks
    have already replaced it functionally.

    Sync function on purpose so launch_dbos can call it before
    DBOS.launch (which is also sync).
    """
    # 3 min default: any sched-* workflow whose last update is older than
    # 3 minutes is presumed dead — the next cron tick has already fired
    # and a 3-minute-old PENDING/ENQUEUED row can only be a leftover
    # from a previous worker crash. 30-min default (pre-2026-05-27) let
    # cancelled scheduled workflows survive across container restarts and
    # be re-enqueued by DBOS recovery, blocking the event loop. Override
    # with DBOS_STALE_SCHED_CUTOFF_MINUTES env var if needed.
    cutoff_minutes = int(os.environ.get("DBOS_STALE_SCHED_CUTOFF_MINUTES", "3"))
    db_url = os.environ.get("DBOS_DATABASE_URL", "")
    if not db_url:
        return

    try:
        # Direct psycopg call so we don't depend on DBOS being initialised.
        import psycopg

        # Strip sqlalchemy-specific query params before psycopg connect.
        cleaned_url = db_url.replace("postgresql+psycopg2://", "postgresql://")

        with psycopg.connect(cleaned_url, connect_timeout=5) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE dbos.workflow_status
                    SET status = 'CANCELLED',
                        updated_at = EXTRACT(EPOCH FROM now()) * 1000
                    WHERE status IN ('PENDING', 'ENQUEUED')
                      AND (name LIKE 'sched-%%' OR workflow_uuid LIKE 'sched-%%')
                      AND updated_at / 1000.0
                          < EXTRACT(EPOCH FROM now() - make_interval(mins => %s))
                    RETURNING workflow_uuid, name;
                    """,
                    (cutoff_minutes,),
                )
                cancelled = cur.fetchall()
                # NOTE (2026-05-27 hotfix): the earlier version of this
                # sweep also issued `DELETE FROM dbos._dbos_internal_queue`
                # to evict stranded queue rows. That table does NOT exist
                # in DBOS 2.x — `_dbos_internal_queue` is a queue *name*
                # (string value in `dbos.workflow_status.queue_name`),
                # not a separate table. The DELETE raised UndefinedTable,
                # rolled back the whole transaction including the UPDATE
                # above, leaving the sweep ineffective and the cancel-
                # storm intact. The UPDATE alone is sufficient: DBOS
                # recovery skips rows with status='CANCELLED', so once
                # they're marked here, they won't be re-enqueued.
                conn.commit()
        if cancelled:
            logger.warning(
                f"[dbos] pre-launch sweep cancelled {len(cancelled)} stale "
                f"scheduled workflow(s) older than {cutoff_minutes} min — "
                "preventing recovery storm. Examples: "
                f"{[r[1] for r in cancelled[:3]]}"
            )
        else:
            logger.info("[dbos] pre-launch sweep: no stale scheduled workflows")
    except Exception as exc:
        # Sweep failure is not fatal — DBOS will still launch, just may
        # hit the recovery storm we tried to prevent. Logged loud so
        # operator notices when sweep can't run (e.g., PG unreachable).
        logger.warning(f"[dbos] pre-launch sweep failed: {exc!r}")


def shutdown_dbos(timeout_seconds: float = 5.0) -> None:
    """Drain the DBOS worker pool with a hard timeout.

    Called from FastAPI lifespan teardown. ``DBOS.destroy()`` blocks on
    the queue-listener thread + PG ``LISTEN`` connection drain — under
    ``uvicorn --reload`` that block stops the worker from exiting in
    time, leaving the admin port (3001) + main HTTP port (8082) held by
    a zombie that the next reload can't replace cleanly. Symptoms:
    ``Address already in use`` on the next start, /health timeouts, the
    whole dev session locks up after the second or third file edit.

    Strategy: run destroy() on a daemon thread; if it hasn't returned
    within ``timeout_seconds``, log + give up. The thread is daemon, so
    the worker process can still exit even with the destroy still
    pending — uvicorn's reload spawns a fresh worker that opens fresh
    sockets / pools, and the orphaned thread dies with the old PID."""
    global _dbos
    if _dbos is None:
        return
    import threading

    from dbos import DBOS

    done = threading.Event()
    err: list[BaseException] = []

    def _do() -> None:
        try:
            DBOS.destroy()
        except BaseException as e:  # noqa: BLE001
            err.append(e)
        finally:
            done.set()

    t = threading.Thread(target=_do, name="dbos-destroy", daemon=True)
    t.start()
    finished = done.wait(timeout=timeout_seconds)
    _dbos = None  # always release our reference so init_dbos can re-instantiate

    if not finished:
        logger.warning(
            f"[dbos] destroy did not return within {timeout_seconds}s — "
            "abandoning thread (daemon, dies with worker process). "
            "If you see admin-port or DB-pool errors on the next reload, "
            "this is the cause."
        )
        return
    if err:
        logger.warning(f"[dbos] destroy raised {err[0]!r}")
        return
    logger.info("[dbos] destroyed")


def is_enabled() -> bool:
    return _dbos is not None


# Sprint 5.5: optional bounds registry — gateway sets this on app.state
# at startup. When provided, dispatch fail-fasts if NO live worker
# advertises the workflow being dispatched.
_bounds_registry: Optional[Any] = None


def set_bounds_registry(registry: Optional[Any]) -> None:
    """Wire-up hook called from FastAPI lifespan. None = disable gating."""
    global _bounds_registry
    _bounds_registry = registry


async def start_workflow_routed(
    task_type: str,
    *,
    dbos_workflow_callable: Callable[..., Any],
    dbos_workflow_kwargs: Optional[dict[str, Any]] = None,
    workflow_id: Optional[str] = None,
) -> dict[str, Any]:
    """Dispatch a task as a DBOS workflow, gated by `dbos_workflow_routing`.

    The routing table is the only kill-switch left after Celery removal —
    if mode != 'dbos', dispatch raises (no fallback). DBOS itself must be
    enabled, otherwise we fail loudly so missing DBOS_DATABASE_URL surfaces
    immediately rather than silently masking with a Celery shadow.

    Sprint 5.5: if a BoundsRegistry is wired and contains live worker(s),
    we additionally check that at least one of them advertises the workflow
    name. If the registry is empty (typical single-process / combined
    deployment) the check is skipped — dispatch proceeds as before.

    Args:
        task_type: matched against routing table
        dbos_workflow_callable: the @DBOS.workflow function to invoke
        dbos_workflow_kwargs: kwargs passed to the workflow
        workflow_id: optional explicit DBOS workflow id (default: server-generated)

    Returns:
        dict with keys: mode, task_type, dbos_workflow_id.
    """
    decision = await get_routing(task_type)
    if decision.mode != "dbos":
        raise RuntimeError(
            f"task_type={task_type} routed to mode={decision.mode!r}; only 'dbos' "
            "is supported after Celery removal. Update dbos_workflow_routing."
        )
    if not is_enabled():
        raise RuntimeError(
            "DBOS orchestrator is not enabled (DBOS_DATABASE_URL missing or "
            "init failed). Cannot dispatch any workflow."
        )

    # Sprint 5.5 (R1 simplified): bounds dispatch gate is now always-on.
    # No env flag — the gate is self-disabling in two safe ways:
    #   1. Empty registry (combined-mode / pre-discovery) → skip
    #   2. Combined-mode self-bound includes all local workflows →
    #      can_dispatch_workflow() returns True naturally
    # Only fails fast in the genuine failure mode: gateway-mode + no
    # worker container advertises this workflow.
    if _bounds_registry is not None:
        live = _bounds_registry.live_bounds()
        if live:
            workflow_name = getattr(dbos_workflow_callable, "__name__", "")
            if workflow_name and not _bounds_registry.can_dispatch_workflow(
                workflow_name
            ):
                from app.agent_framework._metrics_helper import inc_metric

                inc_metric("dispatch_gate_blocked")
                raise RuntimeError(
                    f"no live worker advertises workflow '{workflow_name}' — "
                    f"refusing to enqueue (would sit indefinitely). "
                    f"Live workers: {len(live)}."
                )
            elif workflow_name:
                from app.agent_framework._metrics_helper import inc_metric

                inc_metric("dispatch_gate_passed")

    from contextlib import nullcontext

    from dbos import DBOS, DBOSContextSetAuth, SetWorkflowID

    kwargs = dbos_workflow_kwargs or {}
    # Set authenticated_user on the DBOS workflow_status row so
    # GET /api/v1/workflows can filter by user. Requires user_id
    # in workflow kwargs.
    user_id = kwargs.get("user_id")
    auth_ctx = DBOSContextSetAuth(user=user_id, roles=[]) if user_id else nullcontext()
    with auth_ctx:
        if workflow_id:
            with SetWorkflowID(workflow_id):
                handle = DBOS.start_workflow(dbos_workflow_callable, **kwargs)
        else:
            handle = DBOS.start_workflow(dbos_workflow_callable, **kwargs)
    return {
        "mode": "dbos",
        "task_type": task_type,
        "dbos_workflow_id": handle.workflow_id,
    }
