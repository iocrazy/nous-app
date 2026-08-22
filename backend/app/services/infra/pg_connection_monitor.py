"""Postgres connection-slot pressure — sampling, classification, cache.

Why this exists: ``max_connections`` on the self-hosted Supabase cluster was
100, and the stack's own fixed floor is 60-70 connections before this app
opens a single one (realtime's subscription managers, supavisor_meta,
PostgREST, Supavisor→PG, DBOS's two pools…). A deploy window that briefly
runs old and new pools side by side then tips over the top — 106/100 was
observed on 2026-08-21, and at that point ``psql`` itself can no longer
connect, so the diagnosis tooling dies with the service.

The ceiling is now 200 (declared in
``deploy/gpu-server/supabase/docker-compose.yml``), but a bigger ceiling that
nobody watches just moves the cliff. This module is the watching part:

  * :func:`sample_connection_usage` — the cheap summary (used / max / percent
    / status) every consumer shares, so the threshold is defined ONCE.
  * :func:`fetch_connection_breakdown` — the expensive detail (per
    application_name + usename + state, oldest idle-in-transaction) that only
    the admin panel asks for, on demand.
  * :func:`cache_sample` / :func:`get_cached_sample` — a Redis hand-off so
    ``/readyz`` can report pressure WITHOUT running a query per probe hit.

⚠️ **Nothing here may gate readiness.** High connection pressure is a reason
to look, not a reason to take the service out of rotation — and ``/readyz``
failing is what triggers the deploy chain's automatic rollback. Rolling back
on connection pressure would restart containers, which opens *more* pools:
the response to the alarm would feed the fire. See ``app/api/lifespan_router``
for where this is wired as a non-gating field.

Raw ``text()`` SQL here is the documented structural exception for PG system
catalogs (CLAUDE.md, docs/decisions/2026-08-04-raw-sql-to-orm-full-migration).
``pg_stat_activity`` is a per-backend view, not a table the ORM can map. It
still goes through ``app.db.scoped_sql`` with ``system=True`` so the access
carries the same audit trail every other cross-tenant read does.
"""

from __future__ import annotations

import json
import time
from typing import Any, Optional

from loguru import logger

# ── Thresholds ────────────────────────────────────────────────────────────
# Percent of max_connections. WARNING at 80 is "you have a deploy window's
# worth of headroom left, go look"; ERROR at 95 is "the next pool that opens
# is the one that fails". Both are logged, neither gates anything.
WARN_PCT = 80.0
CRITICAL_PCT = 95.0

# Backend types that occupy a ``max_connections`` slot. Deliberately an
# ALLOWLIST, not "everything except the auxiliary processes": a future PG
# version adding a new background worker type would silently inflate a
# denylist's numbers and manufacture a false alarm, whereas an allowlist
# under-counts visibly instead.
#
# Excluded on purpose: checkpointer / background writer / walwriter /
# autovacuum launcher / logical replication launcher / pg_cron launcher /
# pg_net worker. Those are auxiliary or background-worker processes covered
# by ``max_worker_processes``; counting them against ``max_connections``
# would have reported 67/100 on a cluster actually using 60 slots.
# ``walsender`` IS counted — PG's own docs require max_wal_senders to be
# less than max_connections precisely because WAL senders consume slots.
SLOT_BACKEND_TYPES = (
    "client backend",
    "walsender",
    "parallel worker",
    "autovacuum worker",
)

# Redis hand-off: the scheduled sampler writes, /readyz reads. TTL is
# deliberately ~3x the sampling cadence so a dead sampler surfaces as a
# MISSING reading rather than a stale one that looks current (same reasoning
# as system_status_redis's 90s HASH TTL).
CACHE_KEY = "mediahub:db:connections"
CACHE_TTL_SECONDS = 900


def _slot_type_predicate(param_prefix: str = "bt") -> tuple[str, dict[str, str]]:
    """Build ``backend_type IN (:bt0, :bt1, ...)`` + its bound params.

    Named-placeholder expansion rather than ``= ANY(:list)`` — the same
    pattern ``app/db/schema_assertions.py`` uses for its table-name list, so
    it is already proven against this driver stack (asyncpg + Supavisor with
    ``statement_cache_size=0``).
    """
    names = [f"{param_prefix}{i}" for i in range(len(SLOT_BACKEND_TYPES))]
    predicate = "backend_type IN (" + ", ".join(f":{n}" for n in names) + ")"
    params = dict(zip(names, SLOT_BACKEND_TYPES))
    return predicate, params


def classify(percent: float) -> str:
    """Map a usage percentage onto ``ok`` / ``warning`` / ``critical``.

    Split out from the query so the thresholds are testable without a
    database — and so every consumer (scheduled log, /readyz, admin panel)
    provably shares one definition instead of re-deriving it.
    """
    if percent >= CRITICAL_PCT:
        return "critical"
    if percent >= WARN_PCT:
        return "warning"
    return "ok"


def unknown_sample(reason: str) -> dict[str, Any]:
    """The shape returned when the reading could not be taken.

    A separate ``unknown`` status, never a zeroed-out ``ok``: a probe that
    reports health when it actually failed is worse than no probe (the
    ``not_probed`` lesson from the model-health line, 2026-08-14). Callers
    render this as "no reading", not as "fine".
    """
    return {
        "status": "unknown",
        "reason": reason,
        "used": None,
        "max_connections": None,
        "percent": None,
        "idle_in_transaction": None,
        "oldest_idle_in_transaction_seconds": None,
        "sampled_at": time.time(),
    }


async def sample_connection_usage() -> dict[str, Any]:
    """One cheap aggregate row: slots used, the ceiling, and the verdict.

    Never raises. A DB that is unreachable/unconfigured yields
    :func:`unknown_sample` — this is a monitoring read, and it must not be
    able to break the thing it monitors.
    """
    from app.db import engine as db_engine

    if not db_engine.is_configured():
        return unknown_sample("SUPAVISOR_DATABASE_URL not configured")

    from app.db.scoped_sql import scoped_fetch_one

    predicate, params = _slot_type_predicate()
    sql = (
        "SELECT "
        f"  count(*) FILTER (WHERE {predicate})::int AS used, "
        "  current_setting('max_connections')::int AS max_connections, "
        "  count(*) FILTER (WHERE state = 'idle in transaction')::int "
        "    AS idle_in_transaction, "
        "  coalesce(max(extract(epoch FROM (now() - state_change))) "
        "    FILTER (WHERE state = 'idle in transaction'), 0)::int "
        "    AS oldest_idle_in_transaction_seconds "
        "FROM pg_stat_activity"
    )

    try:
        row = await scoped_fetch_one(
            sql,
            params,
            system=True,
            reason="pg connection-slot pressure sample (system catalog probe)",
        )
    except Exception as exc:  # noqa: BLE001 — monitoring must not break callers
        logger.warning(
            "[pg-conn] connection sample failed ({}) — reporting unknown",
            repr(exc),
        )
        return unknown_sample(f"{type(exc).__name__}: {exc}")

    if not row:
        return unknown_sample("pg_stat_activity returned no row")

    used = int(row["used"] or 0)
    max_conns = int(row["max_connections"] or 0)
    percent = round(used / max_conns * 100, 1) if max_conns else 0.0
    return {
        "status": classify(percent),
        "used": used,
        "max_connections": max_conns,
        "percent": percent,
        "idle_in_transaction": int(row["idle_in_transaction"] or 0),
        "oldest_idle_in_transaction_seconds": int(
            row["oldest_idle_in_transaction_seconds"] or 0
        ),
        "sampled_at": time.time(),
    }


async def fetch_connection_breakdown(limit: int = 50) -> list[dict[str, Any]]:
    """Per ``application_name`` + ``usename`` + ``state`` counts, busiest first.

    Only the admin panel calls this — it is a GROUP BY over every backend, so
    it does not belong on a probe path. Never raises; returns ``[]`` on
    failure and lets the caller's summary carry the ``unknown`` status.
    """
    from app.db import engine as db_engine

    if not db_engine.is_configured():
        return []

    from app.db.scoped_sql import scoped_fetch_all

    predicate, params = _slot_type_predicate()
    query_params: dict[str, Any] = dict(params)
    query_params["row_limit"] = limit
    sql = (
        "SELECT "
        "  coalesce(nullif(application_name, ''), '<none>') AS application_name, "
        "  coalesce(usename, '<system>') AS usename, "
        "  coalesce(state, '<none>') AS state, "
        "  count(*)::int AS count, "
        "  coalesce(max(extract(epoch FROM (now() - state_change))), 0)::int "
        "    AS oldest_state_seconds "
        "FROM pg_stat_activity "
        f"WHERE {predicate} "
        "GROUP BY 1, 2, 3 "
        "ORDER BY count DESC, application_name ASC "
        "LIMIT :row_limit"
    )

    try:
        rows = await scoped_fetch_all(
            sql,
            query_params,
            system=True,
            reason="admin connection panel breakdown (system catalog probe)",
        )
    except Exception as exc:  # noqa: BLE001 — monitoring must not break callers
        logger.warning(
            "[pg-conn] connection breakdown failed ({}) — returning empty",
            repr(exc),
        )
        return []

    return [dict(r) for r in rows]


# ── Redis hand-off (sampler writes → /readyz reads) ───────────────────────


async def cache_sample(sample: dict[str, Any]) -> None:
    """Publish the latest sample for probe consumers. Best-effort."""
    try:
        from app.core.redis import get_async_redis

        client = await get_async_redis()
        await client.set(
            CACHE_KEY,
            json.dumps(sample, ensure_ascii=False, default=str),
            ex=CACHE_TTL_SECONDS,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("[pg-conn] caching sample failed ({})", repr(exc))


async def get_cached_sample() -> Optional[dict[str, Any]]:
    """Last cached sample, or ``None`` when there is no fresh reading.

    ``None`` (key absent/expired/Redis down) is a real answer meaning "the
    sampler is not reporting" — callers must render it as absent, never
    substitute a healthy-looking default.
    """
    try:
        from app.core.redis import get_async_redis

        client = await get_async_redis()
        raw = await client.get(CACHE_KEY)
        if not raw:
            return None
        return json.loads(raw)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[pg-conn] reading cached sample failed ({})", repr(exc))
        return None


def log_pressure(sample: dict[str, Any]) -> None:
    """Emit the WARNING/ERROR line for a sample. Silent when ``ok``.

    Separate from sampling so the scheduled job's logging behaviour is
    testable without a database, and so ``/readyz`` and the admin panel can
    read the same metric WITHOUT re-logging it on every page refresh.
    """
    status = sample.get("status")
    if status == "critical":
        logger.error(
            "[pg-conn] connection slots critical: {}/{} ({}%) — the next pool "
            "that opens is the one that fails; idle_in_transaction={} "
            "(oldest {}s)",
            sample.get("used"),
            sample.get("max_connections"),
            sample.get("percent"),
            sample.get("idle_in_transaction"),
            sample.get("oldest_idle_in_transaction_seconds"),
        )
    elif status == "warning":
        logger.warning(
            "[pg-conn] connection slots elevated: {}/{} ({}%) — headroom for "
            "a deploy window is thin; idle_in_transaction={} (oldest {}s)",
            sample.get("used"),
            sample.get("max_connections"),
            sample.get("percent"),
            sample.get("idle_in_transaction"),
            sample.get("oldest_idle_in_transaction_seconds"),
        )
    elif status == "unknown":
        logger.warning(
            "[pg-conn] connection sample unavailable: {}",
            sample.get("reason") or "<no reason>",
        )


__all__ = [
    "CACHE_KEY",
    "CACHE_TTL_SECONDS",
    "CRITICAL_PCT",
    "SLOT_BACKEND_TYPES",
    "WARN_PCT",
    "cache_sample",
    "classify",
    "fetch_connection_breakdown",
    "get_cached_sample",
    "log_pressure",
    "sample_connection_usage",
    "unknown_sample",
]
