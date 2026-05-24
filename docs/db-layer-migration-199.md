# Backend Data-Layer Migration (Issue #199) — Full Plan & Status

> Living plan for moving backend DB access off supabase-py/REST onto direct
> PG (SQLAlchemy Core over asyncpg). Written for review. Last updated after
> PR #332.

## 1. Background / why

The 2026-05-22 prod incident ("Engine Offline") was a **connection leak**:
every periodic supabase-py REST call (Kong → PostgREST → httpx) left a
`CLOSE_WAIT` socket that httpcore never reaped (known httpcore bug,
unfixed on latest), exhausting the ~28k ephemeral port range over ~2 days.

- **Immediate global fix (shipped, #328):** disable httpx keep-alive on the
  Supabase client (`max_keepalive_connections=0`) so no idle connection is
  retained → CLOSE_WAIT can't form. Verified on prod (CLOSE_WAIT=0).
- **Root direction (this plan, Issue #199):** the trusted backend should not
  go through Kong/PostgREST/httpx at all. Move backend DB access to **direct
  PG** so the whole httpx-leak class is gone and queries are faster.

## 2. Decided target architecture (by trust boundary, not by table)

```
frontend → supabase-py / REST          (RLS / Auth / Realtime / Storage — untrusted client; stays forever)
backend  → SQLAlchemy Core / asyncpg   (trusted, service_role; THIS migration)
DBOS     → its own SQLAlchemy engine    (workflow_status; app must not touch)
```

- REST's value (RLS, Auth, Realtime) only matters for the **untrusted
  frontend**. Backend uses `service_role` (bypasses RLS anyway).
- **SQLAlchemy is the query layer ON TOP of the asyncpg driver**
  (`postgresql+asyncpg://`), not a competitor to it. Chose **Core** (not full
  ORM) so the existing `supabase/migrations/*.sql` workflow stays; gain
  injection-safe query building.
- Do NOT introduce SQLAlchemy as a *third* ad-hoc pattern. One backend layer.

## 3. DB-layer infra files (`backend/app/db/`)

| File | Role | Status / fate |
|------|------|---------------|
| `engine.py` | **SQLAlchemy 2.0 async engine** to Supavisor + query helpers (`fetch_all` / `fetch_val` / `execute`). The #199 **target** layer. | NEW (#329, #332). Read path verified #329, write path verified #332. |
| `pg_pool.py` | Raw **asyncpg** pool to Supavisor (`get_pool` / `close_pool` / `is_configured` / `health_check`). Incident-era stopgap. | INTERIM — retire once all callers move to `engine.py`. |
| `repository_base.py` | `AsyncpgRepository` base class (raw asyncpg): `fetch_one/fetch_all/fetch_value/execute/insert/update_by_id/get_by_id/delete_by_id/acquire/transaction`. Shared by repos. | TO MIGRATE to engine — **high blast radius** (all repos). Separate tested PR. |
| `supabase_client.py` | supabase-py REST client (per-loop cache). Carries the #328 keep-alive-disable workaround. | KEEP for frontend-facing + dev fallbacks. Drop the #328 workaround once supabase-py is gone from backend. |
| `__init__.py` | Re-exports (`get_async_supabase_admin`, etc.). | Update as layers move. |

### Validated engine config (`engine.py`, proven on prod)

```python
create_async_engine(
    "postgresql+asyncpg://...@<supavisor>:6543/postgres",
    poolclass=NullPool,                       # Supavisor IS the pool; a 2nd pool over a txn pooler → "prepared statement does not exist"
    connect_args={"statement_cache_size": 0}, # txn pooling: no client-side prepared-statement cache
)
```
Helpers: `fetch_all`/`fetch_val` on `engine.connect()` (read); `execute` on
`engine.begin()` (auto-commit), returns affected rowcount. **`:name`** bind
params (NOT asyncpg `$1`).

## 4. Migration inventory & status

### 4a. Migrated to `engine.py` (DONE)
| File | Functionality | What moved |
|------|---------------|-----------|
| `services/infra/system_monitor_service.py` → `get_queue_status` | UI queue counters (active/pending) from `task_tracking`; runs every 30s | supabase-py → engine `text()` COUNT (#326 pg_pool → #329 engine). + `get_connection_stats` leak metric (#321). |
| `workflows/liveness_scanner.py` | every-30s agent_runs liveness state machine (running→silent→stuck→dead) + startup `reconcile_stranded_runs` | pg_pool → engine helpers (#332). `_transition`/`_mark_dead` = `execute()`; scan = `fetch_all`; reconcile = `execute` rowcount. |
| `startup/teardown.py` | shutdown drains | added `dispose_engine()` alongside `close_pool()`. |

### 4b. Sweepers — MIGRATED to engine (PR #2, #333)
| File | Functionality | Was | Now |
|------|---------------|-----|-----|
| `workflows/scheduled_master.py` | every-1min user_schedules dispatcher (`fire_due_schedules_step` + `_dispatch_one`) | pg_pool | ✅ engine helpers |
| `workflows/workflow_health_sweeper.py` | every-2min task_tracking health classify + LOST/timeout/orphan actions (fetch + `_refresh_policy` + 4 write helpers) | pg_pool | ✅ engine helpers (phase bug fixed #331; phase-discipline exception documented) |
| `workflows/agent_runs_sweeper.py` | monthly budget recompute → pause/unpause agents (`recompute_monthly_budgets_step`) | **supabase-py** (not pg_pool — earlier note was wrong) | ✅ engine helpers (the `monthly_usage_by_agent` repo call still goes through repository_base/pg_pool until §4c) |

### 4c. Repository layer — separate tested PR (HIGH blast radius)
| File | Functionality | Notes |
|------|---------------|-------|
| `db/repository_base.py` | shared CRUD helpers for all asyncpg repos | Migrate helper internals to engine, **keep signatures**. Gotchas: `$N`→`:name` shim; `execute()->str` asyncpg tag vs SQLAlchemy `rowcount` (affects `delete_by_id`); `connect()` vs `begin()`; **8 `acquire()/transaction()` raw-conn sites** break. Needs unit tests + prod end-to-end verify. |
| `repositories/resources_repository.py` + `resources_repository_asyncpg.py` | resources CRUD | Two asyncpg variants coexist — consolidate. |
| `repositories/media_repository.py` + `media_repository_asyncpg.py` | parsed_media CRUD | same |
| `repositories/agent_runs_repository.py` + `agent_runs_repository_asyncpg.py` | agent_runs CRUD | same |

### 4d. Long tail — supabase-py (121 files)
121 files still import `get_async_supabase*`. These split into:
- **Frontend-facing API routers / services** → KEEP REST (RLS/Auth/Realtime). NOT migrated.
- **Backend trusted data access** → migrate to engine over time (the bulk of #199).
A file-by-file triage (frontend-keep vs backend-migrate) is required before
mass migration. Out of scope for the current sweeper PRs.

## 5. Phased execution plan
- **Phase 0 (done):** engine.py + helpers; validate read (#329) + write (#332) on Supavisor.
- **Phase 0.5 (in progress):** migrate isolated sweepers. liveness_scanner ✓ (#332). Next: agent_runs_sweeper, scheduled_master, workflow_health_sweeper (PR #2).
- **Phase 1:** `repository_base` → engine (separate tested PR) + consolidate the duplicate `*_repository(_asyncpg).py` files. Then retire `pg_pool.py`.
- **Phase 2:** triage + migrate backend supabase-py call sites (121-file long tail); drop the #328 keep-alive workaround once supabase-py is backend-free.
- **Tooling:** ruff/grep rule banning f-string SQL; prefer queries in repository classes over inline-in-workflow.

## 6. Risks / decisions / history
- **keepalive=0 tradeoff:** fresh TCP per supabase-py call; fine (TIME_WAIT auto-reaps, hot paths moved to engine). Removed once supabase-py is backend-free.
- **No supabase fallback in sweepers:** they assume Supavisor configured; `is_configured()` guards added (#331) so they skip gracefully in dev/CI instead of crash-looping.
- **phase value:** active phase is `'processing'` (DBOS trigger `RUNNING→processing`, migration 209/219; verified prod), NOT `'in_progress'`. CLAUDE.md doc is stale on this. (Bug fixed #331.)
- **DBOS phase-discipline:** sweepers writing `phase` directly is a deliberate "writer of last resort" exception (trigger can't fire for dead/lost workflows).

## 7. Review checklist (for the reviewer)
- [ ] Engine config (NullPool + statement_cache_size=0) correct for Supavisor txn pooling.
- [ ] `:name` params everywhere (no leftover `$N` once a file is on the engine).
- [ ] Reads use `connect()`, writes use `begin()` (commit).
- [ ] Sweeper logic preserved 1:1 (CAS guards, state machines, filter semantics).
- [ ] `is_configured()` guards on each migrated sweeper.
- [ ] repository_base: `delete_by_id` truthiness via rowcount; 8 acquire sites adapted; signatures unchanged.
- [ ] No supabase-py removed from frontend-facing paths.

## 8. Related PRs / issues
#318 (run_async drain) · #321/#324 (conn metric) · #322/#323 (healthcheck guard) · #325 (httpx limits, superseded) · #326 (get_queue_status pg_pool) · #327 (sweepers pg_pool) · **#328 (keep-alive disable — the leak fix)** · #329 (engine read spike) · #330 (this migration tracking issue) · #331 (phase bug + guards) · **#332 (engine helpers + liveness_scanner)**.
