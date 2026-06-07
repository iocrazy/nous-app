# Backend ORM 2.0 Migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **Status:** plan-eng-review applied 2026-06-04 — see §16 Review Resolutions. Read that section first; it carries the binding decisions (flat models, server_default, unit-of-work, sqlacodegen, drift guard).

**Goal:** Migrate the backend data-access layer from hand-written SQL (asyncpg + `text()`) and supabase-py REST to idiomatic **SQLAlchemy 2.0 ORM** (`DeclarativeBase` + `Mapped`/`mapped_column` models + `AsyncSession` + `select()/insert()/update()/delete()`), so commit boundaries are owned by the session (eliminating the silent-rollback class of bug) and async support is first-class.

**Architecture:** Keep every `*Repository` class's **public async method signatures identical** — migrate only the internals. The 106 call sites that import repo classes directly stay untouched. Roll out per domain behind `USE_ORM_*` env flags (mirroring the existing `USE_ASYNCPG_MEDIA` cutover), with shadow-read parity checks before flipping each flag. Front-end stays on REST (anon key + RLS); DBOS keeps its private engine; Supabase **Auth (GoTrue)** and **Storage** stay REST permanently (non-SQL services).

**Tech Stack:** Python 3.13, SQLAlchemy 2.0 (asyncio ext). **The ORM reuses the EXISTING `app/db/engine.py::get_engine()`** — same asyncpg driver, same Supavisor pooled URL, same `NullPool` + `statement_cache_size=0`. SQLAlchemy is not a driver; `postgresql+asyncpg://` means "SQLAlchemy Core/ORM on top of the asyncpg driver we already use." We are NOT introducing a new transport. Models are **generated from the live DB via `sqlacodegen`** (then flattened), not hand-written. Alembic is NOT adopted for applying migrations (schema stays in `supabase/migrations/*.sql`); model↔schema drift is guarded by a reflection-diff test. `Base.metadata` is reference-only, never `create_all` against prod.

---

## 0. Current-State Inventory (measured 2026-06-04, master `a9a97371`)

| Metric | Value |
|---|---|
| Total repo files | 93 |
| Use `.table(` (supabase REST) | 51 |
| Use asyncpg (`fetch_*`/`execute`/`AsyncpgRepository`) | 5 |
| Use `.rpc(` (PostgREST RPC → PG function) | 6 |
| Distinct tables referenced (≈ model count) | ~57 |
| `get_*_repository` factories | 6 |
| Files importing repo classes **directly** (not via factory) | 106 |
| Backend Supabase client | **service-role key → RLS bypassed** (no repo depends on RLS) |
| ORM/session layer today | **none** (`grep` DeclarativeBase/AsyncSession/sessionmaker → 0) |

**Two paths to the same Postgres (the mental model that drives this migration):**
```
A) supabase-py (REST):  code → HTTPS → PostgREST gateway → Postgres   [51 repos today]
B) asyncpg direct (wire): code → asyncpg driver → Supavisor → Postgres [5 repos + DBOS + the new ORM]
   SQLAlchemy ORM/Core sits ON TOP of (B): AsyncSession + select() → asyncpg → Postgres
```
Migration = converge everything onto (B). The 5 asyncpg/`text()` repos keep the same transport (just `text()` → `select()`); the 51 REST repos switch transport (HTTP/PostgREST → asyncpg direct) AND API.

---

## 1. Scope

### 1.1 Permanently EXCLUDED (stay REST / non-SQL — never become ORM)
- **Auth (GoTrue)**: `supabase_auth_service.py`, `supabase_auth_router.py`, `api/admin/users_router.py`, `utils/admin_helpers.py`, and the user-create/list portions of `projects_repository.py` & `points_router.py`. Supabase **Auth API**, not SQL tables.
- **Storage**: `services/library/chat_upload.py` (and any `.storage`/bucket upload). Supabase **Storage API**.
- **Front-end**: unchanged (browser → Supabase REST, anon key + RLS).
- **DBOS engine**: `dbos.*` tables are engine-private; never mapped.

### 1.2 IN SCOPE — migrate to ORM 2.0 (≈ 51 REST + 5 asyncpg repos)
Grouped into phases §5–§10. RPC repos (§8) need per-function SQL rewrite. The 5 asyncpg repos (§5) go first — they already hand-write SQL AND carry the silent-rollback commit bug.

---

## 2. Core Strategy & Invariants

### 2.1 Interface-stable internal swap (non-negotiable)
Each `*Repository` keeps its class name and every public async method's name + signature + return shape (`dict` / `list[dict]` / `None`). Only the body changes. ORM rows are converted to plain `dict` at the repo boundary — no `Mapped` objects leak to the 106 callers. A repo migration is "green" only if its existing tests pass unchanged (plus new ones).

### 2.2 Per-domain cutover flag + shadow parity
Add `USE_ORM_<DOMAIN>` env flags (default **false**), factory/`__init__` selects impl. Before flipping in prod: **shadow-read parity** — read via both impls on a sampled %, diff to a log sink, watch per-flag latency/error metrics. Flip → monitor → delete legacy after one clean release.

### 2.3 Hard invariants checklist (MUST hold in every migrated repo)
- [ ] **Commit boundary** — writes run inside `write_scope()` (`async with session.begin()`), NEVER on a non-committing `connect()`. This is the entire reason for the migration (the old `fetch_one("UPDATE…RETURNING")` silently rolled back).
- [ ] **[P0#1] Flat models, NO `relationship()`** — async lazy-load on an `AsyncSession` RAISES `MissingGreenletError` (it is NOT a lazy query). Models map columns only, to match current dict access and sidestep the landmine. If a join is genuinely needed, write an explicit `select(...).join(...)` or mandate `selectinload`/`joinedload` — never rely on attribute lazy-load. (Notion §9.1 mentions eager loading; this is the async caveat the note omits.)
- [ ] **[P0#2] `server_default` / DB-owned columns** — snowflake-bigint PKs have DB-side defaults; `updated_at` is trigger-owned. Models use `server_default=FetchedValue()` (or omit the column on INSERT) for `id`/`created_at`/`updated_at`. NEVER set a Python-side `default=`/`onupdate=` for these — it fights the DB (sends NULL/wrong id, or stomps the trigger). (Notion §3.1 shows `default=datetime.utcnow` — explicitly WRONG for this project; sqlacodegen emits the correct `server_default` automatically.)
- [ ] **Snowflake BIGINT bind** — IDs are `Mapped[int]` (BIGINT). Coerce str snowflake ids via the existing `_bigint()` helper before bind; asyncpg int8 codec is strict (never bind str to BIGINT). (`feedback_asyncpg_bigint_str_strict`)
- [ ] **JSONB merge, not replace** — shared `jsonb` columns (e.g. `user_settings.settings_json`) are read-modify-merge, never overwritten. (`bug_user_settings_json_clobber`)
- [ ] **tz-aware datetimes** — bind `datetime` objects (not isoformat strings) for `timestamptz`; keep UTC tz-aware.
- [ ] **[P1#7] DBOS-step idempotency** — a `write_scope` commit inside a `@DBOS.step` that DBOS may RETRY must be idempotent (upsert / guard), OR the write belongs in the workflow body, not the step. (Same-family lesson as #495: never start a child workflow from inside a step.) Validate in Phase 0 pilot.
- [ ] **dict at boundary** — convert ORM rows to `dict` so callers + frontend `bigIntSafeFetch` assumptions are unaffected.
- [ ] **Supavisor footgun (do not "fix")** — `NullPool` + `statement_cache_size=0` over Supavisor transaction-mode is CORRECT (pooler does the pooling; prepared-statement cache MUST be off or you get "prepared statement already exists"). Reuse `get_engine()` as-is; do not swap in a real client-side pool.
- [ ] **No `create_all` against prod** — schema owned by `supabase/migrations/`. `Base.metadata` is for reflection/tests only.

### 2.4b [Q3] Fully async — no sync DB path, eliminate `run_async` DB bridges
The DB layer is already async-only (async supabase client; async engine; no sync `create_engine`/`psycopg2`/`sessionmaker`). The residue is **9 `run_async()` bridges** where SYNC functions call async DB: `tasks/download_strategies.py`, `tasks/download_helpers.py`, `tasks/utils.py`, `services/media/parsers/parse_helpers.py`, `services/media/transcode/transcode_service.py`, `workflows/scheduled_cleanup.py`, `workflows/storyboard.py`, `services/ai/providers/ai_provider_helpers.py`.

Rule for the migration:
- **DB access is async-native end to end.** Convert each `run_async(repo.X(...))` site so the caller is `async def` and `await`s the repo directly. No `run_async` around DB calls.
- **Genuine sync-library boundaries stay sync, isolated.** yt-dlp downloaders, ffmpeg subprocess, and other blocking 3rd-party libs are real CPU/blocking work — wrap them in `await asyncio.to_thread(sync_call)` from an async caller, and keep DB calls in the async caller (never `run_async(repo...)` from inside the sync lib code).
- **Net:** after migration, `run_async` survives ONLY (if at all) at a true sync→async entry boundary, never as a DB-access shim. Add a CI grep guard: `run_async(` adjacent to `repo`/`session` is a lint failure.

### 2.4 [P0#3] Unit-of-Work — request-scoped session (so multi-repo writes stay atomic)
Per-method `write_scope()` alone gives each repo call its own transaction → a service calling 3 repo writes gets 3 separate commits, no cross-repo rollback. To preserve atomicity (the core ORM 2.0 unit-of-work benefit), repos JOIN an ambient session if one exists:

```python
# backend/app/db/session.py
from contextvars import ContextVar
_request_session: ContextVar["AsyncSession | None"] = ContextVar("_request_session", default=None)

@asynccontextmanager
async def unit_of_work():
    """Open a request/service-scoped session+transaction. Repo writes called
    inside this block join it (one commit for all). Set by FastAPI dependency
    or service entry; repos opt in via _current_or_new()."""
    async with get_sessionmaker()() as s:
        async with s.begin():
            token = _request_session.set(s)
            try:
                yield s
            finally:
                _request_session.reset(token)

@asynccontextmanager
async def write_scope():
    """Repo write boundary: join the ambient unit_of_work if present (no nested
    commit), else open+commit a standalone transaction."""
    existing = _request_session.get()
    if existing is not None:
        yield existing            # caller's unit_of_work owns the commit
        return
    async with get_sessionmaker()() as s:
        async with s.begin():
            yield s
```
Repos call `write_scope()` for writes, `read_scope()` for reads. Services that need atomicity wrap their calls in `unit_of_work()`. Default (no ambient session) = per-method commit, identical to today's behavior → safe for the 106 untouched callers.

---

## 3. Phase 0 — Foundation

**Files:**
- Create: `backend/app/db/orm_base.py` (`Base(DeclarativeBase)`)
- Create: `backend/app/db/session.py` (`get_sessionmaker`, `read_scope`, `write_scope`, `unit_of_work` — §2.4)
- Create: `backend/app/models/__init__.py` + per-domain model modules (generated, §11)
- Create: `backend/tests/db/test_orm_session.py`, `backend/tests/db/test_schema_drift.py`
- Reuse: `backend/app/db/engine.py::get_engine()` (NO new engine — §Tech Stack / Q2)

- [ ] **Task 0.1** — `Base(DeclarativeBase)` in `orm_base.py`. Docstring: maps existing tables, never `create_all` against a real DB.
- [ ] **Task 0.2** — `session.py`: `async_sessionmaker(bind=get_engine(), expire_on_commit=False)` + `read_scope`/`write_scope`/`unit_of_work` exactly as §2.4. (Binds the EXISTING asyncpg engine — confirms Q2: no new transport.)
- [ ] **Task 0.3 — sqlacodegen bootstrap** — `sqlacodegen --generator declarative <SUPAVISOR_URL>` against a snapshot DB → generates 2.0 `Mapped` models for the ~57 mapped tables with correct `server_default`/types/nullability. Split into `app/models/<domain>.py`. **Flatten**: delete generated `relationship()` lines (P0#1). Spot-check against `information_schema`. Commit the generated+flattened models.
- [ ] **Task 0.4 — TDD session** — `test_orm_session.py`: `write_scope` commits (write→read-back in a fresh session); rolls back on raise; `unit_of_work` makes two repo writes atomic (one fails → both rolled back); `read_scope` returns dicts. RED→GREEN. **Run against a real Postgres** (see §12), not mocks.
- [~] **Task 0.5 — DBOS-step pilot — DEFERRED to Phase 1 §5.1 (decided 2026-06-04)**. Rationale: existing DBOS tests all MOCK the runtime (no in-repo precedent for launching a real worker), and a throwaway local-DBOS spike against dev would only synthetically exercise what §5.1 exercises for real — §5.1 (`media_repository`) IS the first real DBOS-touched write path (it carries the silent-rollback retry-counter P0). So validate `write_scope`-inside-`@DBOS.step` commit + simulated-retry idempotency THERE, against the real workflow, instead of a synthetic pilot now. The §2.3 P1#7 invariant (DBOS-step writes must be idempotent) stays documented and binding; only its empirical validation moves to §5.1.
- [ ] **Task 0.6 — schema drift guard** — `test_schema_drift.py`: reflect the mapped tables (`MetaData().reflect(only=[...])`) and assert every model's columns/types match the live table; fail on drift. Wire into CI. (Replaces Alembic autogenerate for the "models stay synced" guarantee.)
- [ ] **Task 0.7 — Commit** (`feat(db): SQLAlchemy 2.0 ORM foundation — Base + session scopes + generated models + drift guard`). No behavior change yet → safe PR.

---

## 4. Migration task TEMPLATE (per repo, Phases 1–7)
- [ ] **1** Confirm the table's generated model exists + is flattened (Task 0.3 output); add if missing.
- [ ] **2** Characterization tests for the repo's existing public methods (capture current contract).
- [ ] **3** ORM impl behind `USE_ORM_<DOMAIN>`, signatures identical; reads `read_scope`+`select()`, writes `write_scope`; coerce ids via `_bigint()`; merge jsonb; return dicts.
- [ ] **4** Run repo tests under BOTH flag states → identical. Integration tests hit real PG.
- [ ] **5** Shadow-parity (where a prod read path exists): read via legacy + ORM, diff to log sink, sample %.
- [ ] **6** Commit; later flip flag in prod, monitor metrics, then delete legacy impl + `_to_named` usage.

---

## 5. Phase 1 — asyncpg repos (5) — also fixes the silent-rollback P0
These already hand-write SQL and carry the commit bug. **[P1#5] The ORM impl REPLACES the asyncpg impl — retire `USE_ASYNCPG_MEDIA` and the asyncpg subclass, do NOT stack a 3rd switch.** End state per repo: REST (legacy, behind flag, deleted after cutover) → ORM. No REST/asyncpg/ORM tri-state.

- [ ] **5.1** `media_repository*` → model `parsed_media`. Replace `self.fetch_one("UPDATE…RETURNING")` with `write_scope`+`update()`. Collapse `USE_ASYNCPG_MEDIA`→`USE_ORM_MEDIA`. **⟵ carries the DEFERRED Task 0.5 pilot**: this is the first real `@DBOS.step` write path, so here we must empirically validate `write_scope` commits inside the step in the live worker AND is idempotent on a simulated DBOS step retry (P1#7) — the retry-counter write (#498) is the exact case. Make the UPDATE idempotent (guard/upsert) and document the verified pattern back in §2.3 once proven.
- [ ] **5.2** `resources_repository*` → `resources`, `resource_items`, `resource_versions`, `folders`. `USE_ORM_RESOURCES`.
- [ ] **5.3** `agent_runs_repository*` → `agent_runs`. `USE_ORM_AGENT_RUNS`.
- [ ] **5.4** `user_settings_repository` (shared `settings_json` jsonb) → `user_settings`. **JSONB merge invariant critical.** `USE_ORM_USER_SETTINGS`.
- [ ] **5.5** Retire the `fetch_one(...RETURNING)`-as-write antipattern in `repository_base.py` (`insert`/`update_by_id`) — route through a committing helper or deprecate once unused.
- [ ] **5.6** Close out the `self.fetch_one(...RETURNING)` write sites flagged in PR #498. (#498's `db_engine.execute` workaround for the retry counter is superseded by the media ORM impl.)

## 6. Phase 2 — Library / Media (REST)
Repos: `videos_repository`, `video_collection_repository`, `collections_repository`, `libraries_repository`, `style_template_repository`, `script_repository`, `search_repository`, `transcode_repository`, `media_repository` (REST remnants). Models: `videos`, `collections`, `video_collections`, `libraries`, `style_templates`, `scripts`. Flag `USE_ORM_LIBRARY`.

## 7. Phase 3 — Teams / Projects / Permissions / Tags (REST)
Repos: `team_repository`, `teams_repository`(admin), `projects_repository`(**SQL parts only**), `permission_repository`, `invite_repository`, `tag_preferences_repository`, `table_preferences_repository`. Models: `teams`, `team_members`, `projects`, `project_files`, `invites`, `tags`, `resource_tags`, `tag_preferences`, `table_preferences`. Flag `USE_ORM_TEAMS`.

## 8. Phase 4 — RPC repos (6) — careful per-function rewrite
Each `supabase.rpc("fn")` → call the PG function through the session (`await session.execute(select(func.fn(...)))` or `text()` inside read/write_scope). **Prefer keeping `SECURITY DEFINER` functions in PG** and just calling them via session (don't port their logic to Python). Confirmed fns so far: `get_analysis_stats`, `increment_api_key_usage` (enumerate the rest at task time, verify each via `pg_proc`/`information_schema` — `reference_postgrest_rpc`). Repos: `analysis_repository`, `issue_repository`, `tags_repository`, `api_key_repository`, `points_repository`, `storyboard_repository`. Flag per-repo.

## 9. Phase 5 — AI / Agents (REST)
Repos: `agent_repository`, `agent_workforce_repository`, `ai_repository`, `skill_repository`, `session_memory_repository`, `review_repository`, `nous_repository`, `commitment_repository`, `approval_requests_repository`, `notification_repository`. Models: `ai_agents`, `skills`, `skill_files`, `agent_skills`, `ai_sessions`, `agent_workforce`, `session_memories`, `reviews`, `nous_*`, `commitments`, `approval_requests`, `notifications`. Flag `USE_ORM_AI`.

## 10. Phase 6 — Users / Billing / Logs / Admin (REST)
Repos: `users_repository`(admin, **SQL only**), `user_logs_repository`, `user_mcp_servers_repository`, `credits_repository`, `payment_repository`, `points_repository`(SQL parts), `cookies_repository`, `logs_repository`, `request_logs_repository`, `audit_logs_repository`, `monitoring_repository`, `stats_repository`, `alert_rules_repository`, `system_settings_repository`, `tasks_repository`, `task_tracking` access. Flag `USE_ORM_OPS`.
> ⚠️ `task_tracking` is the UI single source (route-C). Its model maps the table, but repo methods keep the SAME write discipline — phase/status/etc. stay trigger-owned, business code never PATCHes them. **[P2#10] bulk paths**: `application_logs`/`api_request_logs`/analysis high-volume inserts use `insert().values([...])` bulk, not per-row unit-of-work.

---

## 11. Models — generated, not hand-written
**Source: `sqlacodegen --generator declarative` against the live DB** → ~57 typed `Mapped` models with correct `server_default`/types (solves P0#2 mechanically). Then flatten (drop `relationship()`, P0#1) + spot-check. Split into `app/models/<domain>.py`. Tables (finalize from `information_schema.tables` in Task 0.3):
`parsed_media, videos, resources, resource_items, resource_versions, folders, agent_runs, user_settings, teams, team_members, projects, project_files, libraries, tags, resource_tags, tag_preferences, table_preferences, invites, ai_agents, skills, skill_files, agent_skills, ai_sessions, agent_workforce, session_memories, reviews, commitments, approval_requests, notifications, nous_models, user_logs, user_mcp_servers, credits, payments, daily_point_gifts, cookies, application_logs, api_request_logs, audit_logs, alert_rules, system_settings, task_tracking, collections, video_collections, style_templates, scripts, storyboards, issues, api_keys, points, display_codes, ...`
**Excluded from models**: `dbos.*`, `auth.*`, `storage.*`, RLS policies, RPC functions, triggers (DB owns these; drift test only checks mapped tables).

---

## 12. Testing & Verification (per phase)
- **Integration tests vs REAL Postgres (required)** — server_default, triggers, jsonb, bigint codec, commit/rollback only surface against real PG. Mirror `tests/integration/test_asyncpg_repos.py`. Unit mocks are NOT sufficient for write paths.
- **Unit** — each repo's existing tests pass unchanged under `USE_ORM_*` true AND false.
- **New** — commit-boundary (write→read-back fresh session; rollback-on-raise) + unit-of-work atomicity for every migrated write.
- **Drift test** (Task 0.6) — green in CI.
- **Shadow parity** — sampled dual-read diff to a log sink for high-risk repos before flag flip.
- **Full suite** `uv run pytest` green (~2779 baseline). **Lint** black+isort+flake8 clean.
- **Prod smoke (post-flip)** — targeted read/write per domain; watch `application_logs` for `42703`/`violates`/type errors.

## 13. Rollback
Per-domain `USE_ORM_*` default-false → rollback = flip flag false (instant via `/app/.env`, no redeploy). Legacy impl kept one release post-flip. No destructive schema changes → DB rollback N/A.

## 14. PR / merge cadence
Phase 0 = 1 PR (foundation, no behavior change). Phase 1 = 1 PR (5 asyncpg repos; fixes commit P0). Phases 2–6 = one PR per phase (sub-batch if large), ≤ a few days each, daily `scripts/sync-worktree.sh` rebase. Each PR interface-stable, flag default-false; flip in a follow-up ops step.

## 15. Open decisions (resolved 2026-06-04 unless noted)
1. ✅ **Connection pool** — reuse `get_engine()` (NullPool + asyncpg over Supavisor). Correct for Supavisor txn-mode; do not change (§2.3 footgun).
2. ✅ **`task_tracking`** — ORM model maps it; repo methods keep trigger-owned columns read/decoration-only (route-C).
3. ✅ **RPC functions** — keep PG-side `SECURITY DEFINER` functions, call via session (don't port to Python) unless a function is trivial.
4. ✅ **Alembic** — NOT adopted as migration engine (schema stays in supabase/migrations; avoids two-owner conflict with RLS/DBOS/Supabase system objects). Drift guarded by reflection-diff test (Task 0.6).
5. ✅ **Models** — sqlacodegen-generated + flattened, not hand-written.

---

## 16. Review Resolutions (plan-eng-review, 2026-06-04)
Folded into the plan above. Decisions (all accepted):

| # | Finding | Resolution | Where |
|---|---|---|---|
| P0#1 | async lazy-load on AsyncSession raises | flat models, no `relationship()`; explicit joins / selectinload only | §2.3, §3 Task 0.3, §11 |
| P0#2 | Python `default=` fights snowflake/updated_at DB triggers | `server_default`/omit-on-insert; sqlacodegen emits it | §2.3, §11 |
| P0#3 | session-per-method = no cross-repo atomicity | contextvar `unit_of_work()` + join-ambient `write_scope()` | §2.4, §3 Task 0.2 |
| P1#4 | no model↔schema drift guard (no Alembic) | reflection-diff CI test | §3 Task 0.6, §12 |
| P1#5 | 3-way REST/asyncpg/ORM flag matrix | Phase 1 retires asyncpg impl+flag; ORM replaces | §5 |
| P1#6 | unit mocks miss real DB behavior | integration tests vs real PG required | §12 |
| P1#7 | DBOS-step commit + retry → double write | idempotency invariant + pilot validation | §2.3, §3 Task 0.5 |
| P2#8 | NullPool/statement_cache rationale unstated | stated as "do not fix" footgun | §2.3, §15.1 |
| P2#9 | shadow parity too vague | sampled dual-read + diff sink + per-flag metrics | §2.2, §12 |
| P2#10 | bulk insert paths | `insert().values([...])` for high-volume tables | §10 |
| Q1 | hand-writing 57 models is slow/error-prone | sqlacodegen reverse-generate + flatten | §3 Task 0.3, §11 |
| Q2 | "do we lose our own stack?" | ORM reuses `get_engine()`; driver stays asyncpg; only transport-convergence for REST repos | Tech Stack, §0, §3 Task 0.2 |
| Q3 | fully async — kill sync DB paths | DB already async-only; convert 9 `run_async` DB bridges to async-native; sync libs via `asyncio.to_thread`; CI guard against `run_async(repo…)` | §2.4b |

**Notion note cross-check** (《SQLAlchemy 2.0 完整启用指南》): DeclarativeBase/Mapped ✅, async_sessionmaker/expire_on_commit=False ✅, session.begin/commit ✅, select/update ✅, eager-loading → adopted as the async-lazy CAVEAT (P0#1), Python defaults → deliberately inverted for this project (P0#2), Alembic → deliberately not the migration engine (drift-test instead), bulk ops → P2#10.

---

## Self-Review (plan-write + review-applied)
- Scope: all 93 repos phased; Auth/Storage/frontend/DBOS excluded. ✅
- No placeholders: foundation code concrete (§2.4, §3); models generated not hand-waved; drift + integration tests specified. ✅
- Invariants: commit boundary, flat-model/async-lazy, server_default, bigint, jsonb-merge, tz, DBOS-step idempotency, Supavisor footgun, no-create_all. ✅
- Call-site safety: 106 importers protected by interface-stable swap + default-off unit_of_work. ✅
- Reversibility: per-domain flags + one-release legacy retention. ✅
