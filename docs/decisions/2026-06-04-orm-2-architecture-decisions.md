# ORM 2.0 Migration — Architecture Decisions (cross-session sync)

> **Source:** design discussion in the `master` session on 2026-06-04, synced here
> for the `feature/orm-2-migration` session. Extends
> [`db-layer-migration-199.md`](../db-layer-migration-199.md) (the living plan).
> This worktree has already **generated 107 ORM 2.0 models from the DB**
> (`5b6e9dfa`) — that's the right database-first move; this doc adds the
> guardrails + the auth/scale decisions around it.

---

## 0. Standing directive — design for **100k users + stress testing**

Target is **100,000 users** with stress testing planned. **Do NOT tune for the
current ~1-active-team / ~0.3-tasks-sec load.** Several "simpler is fine at
current scale" conclusions FLIP at 100k — they're called out below.

---

## 1. Framing: you keep asyncpg, you leave PostgREST

`SQLAlchemy ORM 2.0` is **not** an alternative to `asyncpg` — it sits **on top
of** it (`postgresql+asyncpg://`). The migration does **not** get rid of
asyncpg (it stays as the driver). What's being left is **supabase-py /
PostgREST** (the `client.table().insert()` HTTP-over-REST path). Frame every
"is this escaping X" question as **REST → direct SQL**, not "asyncpg → ORM".

---

## 2. Supavisor pooling — reuse it, pick the **mode**

SQLAlchemy-over-asyncpg already connects **through Supavisor** (you are not
self-managing a raw pool). The real decision is the **mode/port**:

| | **6543 transaction** | **6545 session** |
|---|---|---|
| Multiplexing | high (1000s of clients → few PG backends) | low (1 client pins 1 backend for the session) |
| LISTEN/NOTIFY | ❌ dropped (conn returned per-txn) | ✅ works (conn pinned) |
| prepared statements | ❌ need `NullPool` + `statement_cache_size=0` | ✅ |
| session-persistent `SET` | ❌ | ✅ |
| `SET LOCAL` (txn-scoped) | ✅ | ✅ |

**At 100k:** `6543 transaction` is the **mandatory primary** path (session mode
pins a backend per session → exhausts PG's ~few-hundred connection ceiling).
`6545 session` is reserved for a **small FIXED set of LISTEN connections**
(worker-side, NOT per-user). The dual-engine split is **required at this scale**
(it was "premature" only at today's tiny load).

**LISTEN blocker resolved:** the old `delegate_tool.py` note "Supabase pgbouncer
transaction pooling drops LISTEN" = it was on **txn mode**. Use a **6545 session**
connection for LISTEN → works **without bypassing Supavisor**. Don't naive-pool
session mode for general queries; keep session connections few.

### Connection / pool discipline (the #318 lesson)

The 2026-05-22 "Engine Offline" was a `supabase-py/httpx` per-loop-client
`CLOSE_WAIT` leak (ephemeral source-port exhaustion). That HTTP path is **gone**
with direct SQL. New risk class = pool discipline:

- **Singleton engine only.** Never `create_async_engine` per loop/request
  (`get_engine()` global `_engine`). Repeating the per-loop-create pattern IS
  #318 reborn — especially in DBOS/worker `asyncio.run` contexts.
- Keep **6545 session connections few** (LISTEN only). Many long-lived session
  conns → exhaust Supavisor's session budget = "too many connections" (different
  failure than port exhaustion).
- `NullPool` + `statement_cache_size=0` over the txn pooler (already proven).

---

## 3. Migrations: keep `supabase/migrations/*.sql`, do **NOT** adopt Alembic

Even under full ORM 2.0. SQL files stay the **single source of schema truth**
(tables + RLS + triggers like `mirror_dbos_lifecycle_to_tracking` + SECURITY
DEFINER fns + pgvector + GRANTs — Alembic autogenerate sees **none** of these,
only ORM-mapped tables). 248+ migrations, CI auto-apply, PR review, rollback
files already work — switching = pure churn.

**Database-first**: ORM models are a **typed mirror** of the SQL schema, not the
source. (You already did this — generated 107 models from the DB.)

### model/DB drift solution (impossible-by-construction + CI-caught)

1. **Generate models from the DB** (`sqlacodegen` against a migration-built DB)
   — models become a derived artifact, can't hand-drift. Generate Table core,
   hand-layer relationships, CI-diff table structure only. ✅ already started.
2. **CI drift gate (the real guard):** build ephemeral PG from
   `supabase/migrations/*.sql` → reflect → diff vs committed models → **fail on
   non-empty diff.** Use **Alembic `--autogenerate` ONLY as a checker** (assert
   empty diff; NOT as the migration runner — no conflict with keeping SQL files)
   OR `migra` (PG-specific, sees more). Scope to columns/types/nullable/FK/index;
   ignore RLS/triggers/functions.
3. **Startup fail-fast:** on boot, reflect + assert critical tables/columns exist
   → crash loudly on mismatch (catches code-deployed-ahead-of-migration).

At 100k, silent drift under load = mass 500s → these are **non-negotiable**.
asyncpg type-strictness (bigint/uuid/pgvector/timestamptz) → **type-level drift
must be checked too** (wrong `Mapped[...]` = runtime bind error).

---

## 4. Authorization / RLS — app-layer scope choke point (primary), RLS backstop (crown jewels)

**The big risk of going direct:** PostgREST enforced RLS via the user JWT;
direct SQL runs as `service_role` (BYPASSRLS), so **per-user authz moves into
app code**. A query that forgets `WHERE user_id` = data leak (RLS would've
caught it). Frontend **stays on REST** (RLS-protected); backend (trusted) goes
ORM with the model below.

**Decision (at 100k, perf-aware):** do NOT make RLS the backend's primary
mechanism — per-query policy-predicate overhead × high QPS is real, and 95% of
backend queries are legit cross-user/system ops that RLS would only tax. Instead:

### Primary defense — application-layer scope-injection choke point (fail-closed by construction)

Make "no-scope user-facing query" **impossible to issue**, enforced in the ORM,
not by per-query vigilance:

**Layer 1 — no raw session; two typed entry points:**
```python
async with user_session(scope) as s:   # scope is a REQUIRED arg — can't build without it
    ...                                 # auto-injects WHERE user_id = scope
async with system_session(reason="nightly sweeper") as s:  # explicit cross-user + audited/greppable
    ...                                 # no injection
```

**Layer 2 — `do_orm_execute` event injects the tenant filter (catches even raw `select()`):**
```python
_scope: ContextVar[Scope | None] = ContextVar("db_scope", default=None)

class TenantScoped:                 # tenant tables inherit this (have user_id)
    user_id: Mapped[int]

@event.listens_for(Session, "do_orm_execute")
def _enforce_scope(state):
    if not state.is_select:
        return
    if not any(issubclass(m.class_, TenantScoped) for m in state.all_mappers):
        return
    scope = _scope.get()
    if scope is SYSTEM:             # system_session sentinel → explicit cross-user
        return
    if scope is None:              # ★ unset scope + touches tenant table
        raise UnscopedQueryError(state.statement)        # fail-closed: raise, never silent full-scan
    state.statement = state.statement.options(
        with_loader_criteria(
            TenantScoped,
            lambda cls: cls.user_id == scope.user_id,
            include_aliases=True,  # cover joins/aliases too
        )
    )
```
Unset scope + tenant table = **exception** → "forgot to scope" blows up in
dev/test immediately, can't ship. Fail-closed by construction.

**Layer 3 — writes:** `before_insert` mapper event stamps `user_id` from scope
if unset / **asserts == scope** if set (can't insert for another user). Bulk
UPDATE/DELETE on tenant tables in user mode: inject the same `where`, or forbid
raw bulk DML (force load-then-modify).

**Layer 4 — crown-jewel RLS backstop:** the few highest-blast-radius tables keep
DB-level RLS as the last line if Layers 1–3 have a bug. Pay the RLS tax **only**
where the blast radius justifies it. Candidate crown jewels (to finalize):
`user_cookies` (cookies), `user_settings` (AI provider API keys), points/billing
accounts, `api_keys`.

### Must-pin details

- **asyncio scope propagation:** `ContextVar` copies per task across `await` —
  per-request/per-task scope, pooling-safe (scope binds to task, not connection).
  Set scope in **two places**: ① HTTP middleware (from JWT) ② **every DBOS
  workflow/task entry** (from the task payload's `user_id`) — workers run
  workflows in their own context; forgetting → fail-closed raise (good).
- **Raw SQL is the escape hatch:** `text()`/Core bypasses the ORM event. Rule:
  tenant access goes through the ORM; raw SQL on tenant tables is a **reviewed
  exception** + manually scoped + ideally covered by crown-jewel RLS. At 100k
  you WILL have perf raw SQL — govern it (a `scoped_sql(scope, ...)` helper that
  forces a scope param, or a review checklist).
- **Perf:** `with_loader_criteria` injects `WHERE user_id=?` — rides the
  `user_id` index, **zero RLS role-switch tax**. As fast as hand-written
  filtering, just enforced.
- **Default DB role = `service_role`** (perf) since the app layer is already
  fail-closed; RLS only on crown jewels. (`execute_as_service_role` already does
  `SET LOCAL ROLE` — the role-switch mechanism exists if a crown-jewel path needs
  `authenticated` + `SET LOCAL request.jwt.claims`.)

---

## 5. Relevance to the outbox / no-poll dispatch thread

Once `enqueue_outbox` (currently PostgREST `client.table().insert()`) moves to
SQLAlchemy, the **DBOS delivery enqueue can run in the SAME transaction** as the
write → atomic → the "pure app-hook, zero safety cron" transactional-outbox
design becomes safe (no NOTIFY plumbing, no relay cron). That was the "bigger
change" blocking it. The 5s/10s `outbox_dispatch`/`inbox_dispatch` polling crons
(today: ~5000 CANCELLED housekeeping workflows/day) can then collapse. **Floor
remains:** DBOS's own queue dequeue polls (SKIP LOCKED) — irreducible, that's
execution not relay.

---

## 6. Open decisions (to finalize across both sessions)

1. **`TenantScoped` boundary** — which of the 107 tables carry `user_id` (tenant)
   vs shared/system tables (no injection)? This defines what the choke-point
   event applies to.
2. **Raw-SQL governance** — how to guarantee perf raw SQL on tenant tables is
   also scoped (forced-scope helper vs review checklist).
3. **Crown-jewel table list** — finalize which tables get RLS backstop.
4. **RLS sub-axes** (if any crown-jewel path uses RLS): identity injection
   (replicate `SET LOCAL request.jwt.claims` vs custom GUC `app.user_id`);
   enforce via session-factory/event hook not per-query.
5. **Single → N workers** (100k scale) + DBOS SKIP-LOCKED contention under high
   queue volume (stress-test target; amplifies the #478 stall class).
6. **Hot-table partitioning/retention** at 100k (`task_tracking`,
   `dbos.workflow_status`, `application_logs`, `agent_runs` — day-rotate/partition
   + TTL or vacuum/query degrades).

---

_Master-session memory mirror: `feedback_design_for_100k_scale`,
`project_db_layer_sqlalchemy`. Keep both in sync as decisions land._
