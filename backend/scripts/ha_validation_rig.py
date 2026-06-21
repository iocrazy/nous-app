#!/usr/bin/env python
"""HA validation rig — Worker Foundation P2/P3 live confirmation.

Proves, against a THROWAWAY isolated Postgres database (created + dropped by
this script — NEVER the shared dev schema with its 155k DBOS rows), the two
safety properties multi-worker HA depends on:

  A. RECOVERY ISOLATION — DBOS's REAL ``get_pending_workflows(executor_id, ...)``
     (dbos/_sys_db.py) returns ONLY a worker's own PENDING rows, so worker-1
     never re-runs worker-0's in-flight workflows on restart (no double download
     / no double AI billing). This is the property `resolve_executor_id`'s
     stable-per-replica id buys us.

  B. OWNER-DEAD REAP — our REAL ``reap_owner_dead_orphans_step`` (the code that
     ships), run as worker-1 with FEATURE_MULTI_WORKER_ID on, cancels a dead
     sibling's orphan (worker-0, whose worker_registry heartbeat is stale),
     reconciles its task_tracking row to lost(retryable) — and NEVER touches a
     live worker's running task or a too-young freshly-claimed row.

Both checks run against a real Postgres with the real DBOS schema (created via
DBOS's own ``run_migrations``) and the real shipping reaper code. Nothing is
mocked.

Usage:
    HA_RIG_BASE_DSN='postgresql://user:pw@192.168.50.9:55434/postgres' \\
        uv run python scripts/ha_validation_rig.py

HA_RIG_BASE_DSN must point at the ``postgres`` maintenance database; the rig
CREATEs/DROPs its own ``ha_validation_rig`` database next to it.
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
import uuid

RIG_DB = "ha_validation_rig"
APP_VERSION = "ha-rig-v1"


def _fail(msg: str) -> None:
    print(f"  ✗ FAIL: {msg}")
    raise SystemExit(1)


def _ok(msg: str) -> None:
    print(f"  ✓ {msg}")


def _build_urls(base_dsn: str) -> tuple[str, str]:
    """(admin_url → /postgres, rig_url → /ha_validation_rig). Plain
    ``postgresql://`` DSNs; drivers are appended by each consumer."""
    import sqlalchemy as sa

    base = sa.make_url(base_dsn)
    admin = base.set(database="postgres")
    rig = base.set(database=RIG_DB)
    return (
        admin.render_as_string(hide_password=False),
        rig.render_as_string(hide_password=False),
    )


def _recreate_database(admin_url: str) -> None:
    import sqlalchemy as sa

    eng = sa.create_engine(
        sa.make_url(admin_url).set(drivername="postgresql+psycopg"),
        isolation_level="AUTOCOMMIT",
    )
    with eng.connect() as c:
        # Drop any leftover from a previous run, then create fresh.
        c.execute(
            sa.text(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = :d AND pid <> pg_backend_pid()"
            ),
            {"d": RIG_DB},
        )
        c.execute(sa.text(f'DROP DATABASE IF EXISTS "{RIG_DB}"'))
        c.execute(sa.text(f'CREATE DATABASE "{RIG_DB}"'))
    eng.dispose()
    _ok(f"throwaway database {RIG_DB!r} created (isolated; dropped at the end)")


def _drop_database(admin_url: str) -> None:
    import sqlalchemy as sa

    eng = sa.create_engine(
        sa.make_url(admin_url).set(drivername="postgresql+psycopg"),
        isolation_level="AUTOCOMMIT",
    )
    with eng.connect() as c:
        c.execute(
            sa.text(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = :d AND pid <> pg_backend_pid()"
            ),
            {"d": RIG_DB},
        )
        c.execute(sa.text(f'DROP DATABASE IF EXISTS "{RIG_DB}"'))
    eng.dispose()
    _ok(f"throwaway database {RIG_DB!r} dropped")


def _make_sysdb(rig_url: str):
    """Build a REAL DBOS SystemDatabase + run its real migrations → authoritative
    dbos.workflow_status schema. No DBOS launch (no scheduler/recovery thread)."""
    from dbos._serialization import DBOSDefaultSerializer
    from dbos._sys_db import SystemDatabase

    sysdb = SystemDatabase.create(
        system_database_url=rig_url,
        engine_kwargs={"pool_size": 2, "max_overflow": 0},
        engine=None,
        schema="dbos",
        serializer=DBOSDefaultSerializer,
        executor_id="worker-1",
        use_listen_notify=False,
    )
    sysdb.run_migrations()
    return sysdb


def _insert_wf(
    engine,
    *,
    wid,
    executor_id,
    status,
    updated_at_ms,
    name="download_workflow",
    queue="download_user",
    app_version=APP_VERSION,
) -> None:
    import sqlalchemy as sa

    with engine.begin() as c:
        c.execute(
            sa.text(
                "INSERT INTO dbos.workflow_status "
                "(workflow_uuid, status, name, executor_id, application_version, "
                " queue_name, created_at, updated_at, priority) "
                "VALUES (:wid, :st, :nm, :eid, :ver, :q, :ca, :ua, 0)"
            ),
            {
                "wid": wid,
                "st": status,
                "nm": name,
                "eid": executor_id,
                "ver": app_version,
                "q": queue,
                "ca": updated_at_ms,
                "ua": updated_at_ms,
            },
        )


def _wf_status(engine, wid) -> str | None:
    import sqlalchemy as sa

    with engine.connect() as c:
        return c.execute(
            sa.text(
                "SELECT status FROM dbos.workflow_status " "WHERE workflow_uuid = :w"
            ),
            {"w": wid},
        ).scalar()


def _tt_phase(engine, wid) -> tuple[str | None, str | None]:
    import sqlalchemy as sa

    with engine.connect() as c:
        row = c.execute(
            sa.text(
                "SELECT phase, status FROM public.task_tracking "
                "WHERE dbos_workflow_id = :w"
            ),
            {"w": wid},
        ).first()
        return (row[0], row[1]) if row else (None, None)


def _seed_support_tables(engine, now_ms: int) -> None:
    """worker_registry (mig 303 shape) + minimal task_tracking, in the rig db."""
    import sqlalchemy as sa

    with engine.begin() as c:
        c.execute(
            sa.text(
                "CREATE TABLE public.worker_registry ("
                " executor_id text PRIMARY KEY, boot_generation uuid NOT NULL,"
                " app_version text, pid integer,"
                " started_at timestamptz NOT NULL DEFAULT now(),"
                " heartbeat_at timestamptz NOT NULL DEFAULT now(),"
                " updated_at timestamptz NOT NULL DEFAULT now())"
            )
        )
        c.execute(
            sa.text(
                "CREATE TABLE public.task_tracking ("
                " dbos_workflow_id text PRIMARY KEY, phase text, status text,"
                " error_code text, error_msg text, completed_at timestamptz)"
            )
        )
        # worker-0 = DEAD (heartbeat 10 min old); worker-1 = ALIVE (now).
        c.execute(
            sa.text(
                "INSERT INTO public.worker_registry "
                "(executor_id, boot_generation, heartbeat_at) VALUES "
                "('worker-0', :g0, now() - interval '10 minutes'),"
                "('worker-1', :g1, now())"
            ),
            {"g0": str(uuid.uuid4()), "g1": str(uuid.uuid4())},
        )


def check_a_recovery_isolation(sysdb, engine) -> None:
    print("\n[A] DBOS recovery isolation (real get_pending_workflows)")
    own = "wf-" + uuid.uuid4().hex[:12]
    sibling = "wf-" + uuid.uuid4().hex[:12]
    now_ms = int(time.time() * 1000)
    _insert_wf(
        engine,
        wid=sibling,
        executor_id="worker-0",
        status="PENDING",
        updated_at_ms=now_ms,
    )
    _insert_wf(
        engine, wid=own, executor_id="worker-1", status="PENDING", updated_at_ms=now_ms
    )

    w1 = {r.workflow_id for r in sysdb.get_pending_workflows("worker-1", APP_VERSION)}
    w0 = {r.workflow_id for r in sysdb.get_pending_workflows("worker-0", APP_VERSION)}

    if sibling in w1:
        _fail(
            f"worker-1 recovery picked up worker-0's pending row {sibling} "
            "→ DOUBLE EXECUTION risk"
        )
    if own not in w1:
        _fail("worker-1 recovery did not see its OWN pending row")
    if own in w0 or sibling not in w0:
        _fail("worker-0 recovery set is wrong")
    _ok("worker-1 recovers ONLY its own pending workflow, never worker-0's")
    _ok("worker-0 recovers ONLY its own — isolation holds both directions")


async def check_b_owner_dead_reap(rig_async_url: str, engine) -> None:
    print("\n[B] owner-dead reaper (real reap_owner_dead_orphans_step as worker-1)")
    from app.workflows.workflow_health_sweeper import reap_owner_dead_orphans_step

    old_ms = int((time.time() - 600) * 1000)  # 10 min old → past 180s floor
    young_ms = int((time.time() - 10) * 1000)  # 10 s old → under the floor
    orphan = "wf-" + uuid.uuid4().hex[:12]
    young = "wf-" + uuid.uuid4().hex[:12]
    healthy = "wf-" + uuid.uuid4().hex[:12]

    # Orphan: RUNNING, owned by DEAD worker-0, old → must be reaped.
    _insert_wf(
        engine,
        wid=orphan,
        executor_id="worker-0",
        status="RUNNING",
        updated_at_ms=old_ms,
    )
    # Young orphan: owned by dead worker-0 but fresh → age floor must spare it.
    _insert_wf(
        engine,
        wid=young,
        executor_id="worker-0",
        status="PENDING",
        updated_at_ms=young_ms,
    )
    # Healthy: RUNNING, owned by ALIVE worker-1 → must be untouched.
    _insert_wf(
        engine,
        wid=healthy,
        executor_id="worker-1",
        status="RUNNING",
        updated_at_ms=old_ms,
    )
    import sqlalchemy as sa

    with engine.begin() as c:
        for wid in (orphan, young, healthy):
            c.execute(
                sa.text(
                    "INSERT INTO public.task_tracking "
                    "(dbos_workflow_id, phase, status) "
                    "VALUES (:w, 'processing', 'processing')"
                ),
                {"w": wid},
            )

    result = await reap_owner_dead_orphans_step()
    print(f"  step result: {result}")

    if result.get("owner_dead_cancelled") != 1:
        _fail(f"expected exactly 1 cancellation, got {result}")
    if _wf_status(engine, orphan) != "CANCELLED":
        _fail("dead-owner orphan was NOT cancelled in dbos.workflow_status")
    if _tt_phase(engine, orphan) != ("lost", "lost"):
        _fail(
            f"orphan task_tracking not reconciled to lost: {_tt_phase(engine, orphan)}"
        )
    _ok("dead worker-0's orphan → CANCELLED + task_tracking lost(retryable)")

    if _wf_status(engine, young) != "PENDING":
        _fail("age floor failed — a too-young row was cancelled")
    _ok("too-young row owned by dead worker spared (180s age floor)")

    if _wf_status(engine, healthy) != "RUNNING":
        _fail("FALSE POSITIVE — a LIVE worker's running task was cancelled")
    if _tt_phase(engine, healthy) != ("processing", "processing"):
        _fail("live worker's task_tracking row was wrongly mutated")
    _ok("live worker-1's running task left untouched (no false positive)")


def main() -> None:
    base_dsn = os.environ.get("HA_RIG_BASE_DSN", "").strip()
    if not base_dsn:
        print(
            "HA_RIG_BASE_DSN not set. Point it at the dev pg 'postgres' db, e.g.\n"
            "  HA_RIG_BASE_DSN='postgresql://supabase_admin:<pw>@192.168.50.9:55434/postgres'"
        )
        raise SystemExit(2)

    admin_url, rig_url = _build_urls(base_dsn)

    # The reaper reads SUPAVISOR_DATABASE_URL and resolves identity from env —
    # set BEFORE importing any app module (pydantic settings loads at import).
    os.environ["SUPAVISOR_DATABASE_URL"] = rig_url
    os.environ["MEDIAHUB_ROLE"] = "worker"
    os.environ["FEATURE_MULTI_WORKER_ID"] = "true"
    os.environ["WORKER_REPLICA_INDEX"] = "1"  # this rig process = worker-1
    os.environ["DBOS_SWEEP_BOOT_GRACE_SECONDS"] = "0"  # don't skip on boot grace

    print("=" * 68)
    print("HA validation rig — Worker Foundation P2/P3 (FEATURE_MULTI_WORKER_ID)")
    print("=" * 68)

    _recreate_database(admin_url)
    sysdb = None
    try:
        sysdb = _make_sysdb(rig_url)
        engine = sysdb.engine  # sync sqlalchemy engine on the rig db
        _seed_support_tables(engine, int(time.time() * 1000))

        check_a_recovery_isolation(sysdb, engine)
        asyncio.run(check_b_owner_dead_reap(rig_url, engine))

        print("\n" + "=" * 68)
        print(
            "✅ ALL CHECKS PASSED — multi-worker isolation + owner-dead reap "
            "verified live"
        )
        print("=" * 68)
    finally:
        if sysdb is not None:
            try:
                sysdb.destroy()
            except Exception:
                pass
        # Dispose the app's async engine if it was created.
        try:
            from app.db import engine as _e

            asyncio.run(_e.dispose_engine())
        except Exception:
            pass
        _drop_database(admin_url)


if __name__ == "__main__":
    sys.exit(main())
