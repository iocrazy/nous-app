"""Worker identity + registry helpers (Worker Foundation P1, observe-only).

A worker process has a STABLE `executor_id` (the role name, e.g. "worker") and
a per-boot `boot_generation` uuid minted ONCE per process. The generation is the
fencing token: P3 will stamp it on claimed task_tracking rows so a row carrying
a generation no longer registered here is a provable orphan of a dead boot.

P1 only WRITES and OBSERVES `worker_registry` — nothing reads it for a decision.
All writes piggyback on the health-sweeper tick (no dedicated thread, no extra
Supavisor-pool pressure), so a busy worker refreshes itself as a side effect of
work it already does.
"""

from __future__ import annotations

import os
import uuid
from typing import Any

from loguru import logger

# Minted lazily, ONCE per process. Module-global so every caller in this process
# (registry write at tick N, again at tick N+1) reports the same generation.
_BOOT_GENERATION: str | None = None


def boot_generation() -> str:
    """This process's boot-generation fencing token (stable for its lifetime)."""
    global _BOOT_GENERATION
    if _BOOT_GENERATION is None:
        _BOOT_GENERATION = str(uuid.uuid4())
    return _BOOT_GENERATION


def _multi_worker_id_enabled() -> bool:
    """HA enablement gate. OFF by default → single-worker behavior is byte-for-
    byte unchanged. ON → workers get a stable PER-REPLICA executor_id so DBOS
    recovery (keyed on executor_id) isolates each worker: a worker only recovers
    its OWN in-flight workflows, never a sibling's → `--scale`/multi-service is
    safe (no double-execution). Flip only after the 2-worker validation."""
    return os.environ.get("FEATURE_MULTI_WORKER_ID", "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def resolve_executor_id(role: Any) -> str:
    """The DBOS executor_id for a process role.

    gateway / combined → the role name (unchanged). worker → the role name
    ('worker') by default; with HA enabled, a STABLE per-replica id
    `worker-<WORKER_REPLICA_INDEX>` (index defaults to 0 for a lone worker).

    Stable-per-replica is the key property: replica N restarts as 'worker-N' and
    reclaims ONLY its own orphans (DBOS recovery filters by executor_id), while
    replica M never touches them. A per-process uuid would break cross-restart
    recovery; the shared bare 'worker' breaks multi-worker. This is the middle
    that satisfies both."""
    from app.agent_framework.role import ProcessRole

    if role == ProcessRole.WORKER and _multi_worker_id_enabled():
        idx = os.environ.get("WORKER_REPLICA_INDEX", "0").strip() or "0"
        return f"worker-{idx}"
    return role.value if hasattr(role, "value") else str(role)


def current_executor_id() -> str:
    """The DBOS executor_id this process runs under — must match what
    `init_dbos(executor_id=resolve_executor_id(role))` set so the
    worker_registry row keys on the same id DBOS uses."""
    from app.agent_framework.role import role_from_env

    return resolve_executor_id(role_from_env())


async def upsert_registry(db_engine: Any) -> bool:
    """Insert-or-refresh this process's `worker_registry` row. Returns True on a
    successful write. Best-effort: a failure (table missing pre-migration, pool
    saturated) is logged and swallowed — P1 must never affect the sweeper tick.

    `started_at` is set on INSERT only (a refresh keeps the original boot time);
    `heartbeat_at`/`updated_at` advance every call.
    """
    from app.workflows.workflow_health_sweeper import _resolve_pinned_app_version

    try:
        await db_engine.execute(
            "INSERT INTO public.worker_registry "
            "(executor_id, boot_generation, app_version, pid, "
            " started_at, heartbeat_at, updated_at) "
            "VALUES (:eid, :gen, :ver, :pid, now(), now(), now()) "
            "ON CONFLICT (executor_id) DO UPDATE SET "
            "  boot_generation = EXCLUDED.boot_generation, "
            "  app_version = EXCLUDED.app_version, "
            "  pid = EXCLUDED.pid, "
            "  heartbeat_at = now(), "
            "  updated_at = now()",
            {
                "eid": current_executor_id(),
                "gen": boot_generation(),
                "ver": _resolve_pinned_app_version(),
                "pid": os.getpid(),
            },
        )
        return True
    except Exception as exc:  # noqa: BLE001
        logger.opt(exception=True).debug(
            f"[worker_identity] registry upsert failed (non-fatal): {exc!r}"
        )
        return False


async def stale_executor_ids(db_engine: Any, threshold_seconds: float) -> list[str]:
    """executor_ids whose heartbeat is older than `threshold_seconds` — i.e. a
    worker process presumed gone. P1 only LOGS these (observe); no action. The
    current process's own row, just refreshed, is never stale."""
    try:
        rows = await db_engine.fetch_all(
            "SELECT executor_id FROM public.worker_registry "
            "WHERE heartbeat_at < now() - make_interval(secs => :secs)",
            {"secs": float(threshold_seconds)},
        )
        return [r["executor_id"] for r in (rows or [])]
    except Exception as exc:  # noqa: BLE001
        logger.opt(exception=True).debug(
            f"[worker_identity] stale query failed (non-fatal): {exc!r}"
        )
        return []
