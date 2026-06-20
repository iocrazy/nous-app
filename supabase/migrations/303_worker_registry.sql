-- 303 — worker_registry: per-process worker liveness + boot generation.
--
-- Worker Foundation P1 (observe-only). One row per DBOS executor process
-- (executor_id), refreshed on each health-sweeper tick (every 2 min, runs only
-- on the worker). Two future uses, neither active yet in P1:
--   * heartbeat_at  — "is this worker process alive?" (process-level liveness,
--     complements the per-task activity heartbeat planned for P2).
--   * boot_generation — a fencing token minted once per process boot. P3 will
--     stamp it on claimed task_tracking rows so a row carrying a generation no
--     longer in this table is a PROVABLE orphan of a dead boot (E2 evidence),
--     making --scale N multi-worker safe without timer guesswork.
--
-- P1 writes + observes this table only; NOTHING reads it for a decision yet.
-- Plan: docs/superpowers/plans/2026-06-20-worker-foundation.md
--
-- Created by: fix/worker-registry-observe (Worker Foundation P1).

CREATE TABLE IF NOT EXISTS public.worker_registry (
    executor_id     text PRIMARY KEY,
    boot_generation uuid        NOT NULL,
    app_version     text,
    pid             integer,
    started_at      timestamptz NOT NULL DEFAULT now(),
    heartbeat_at    timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now()
);

COMMENT ON TABLE public.worker_registry IS
    'Per-process worker liveness + boot-generation fencing token. Written by the '
    'health sweeper on the worker; not user data. See Worker Foundation plan.';
COMMENT ON COLUMN public.worker_registry.boot_generation IS
    'uuid minted once per process boot — fencing token for orphan detection (P3).';
COMMENT ON COLUMN public.worker_registry.heartbeat_at IS
    'Last refresh; stale ⇒ that worker process is presumed gone (observe-only in P1).';

-- Backend-only table (frontend never reads it). RLS on with a service_role-only
-- policy so PostgREST never exposes it to anon/authenticated — mirrors the
-- log-table RLS hardening lesson (#541/#542). The backend writes via the direct
-- DBOS_DATABASE_URL connection, which bypasses RLS, so writes are unaffected.
ALTER TABLE public.worker_registry ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS worker_registry_service_role_all ON public.worker_registry;
CREATE POLICY worker_registry_service_role_all ON public.worker_registry
    FOR ALL TO service_role USING (true) WITH CHECK (true);
