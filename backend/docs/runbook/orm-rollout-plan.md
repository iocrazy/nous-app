# ORM 2.0 Rollout Plan — Shadow-Compare → Tiered Flip Runbook

> The master plan for flipping the `USE_ORM_<DOMAIN>` flags from REST to the
> SQLAlchemy 2.0 ORM in production, **one domain at a time**, with parity
> *proven on real prod traffic* before each flip.
>
> Strategy-C built every `<Repo>Orm` to be **byte-identical** to its legacy
> supabase-py `<Repo>` (uuid→str, timestamptz→ISO, bigint→int, …). This runbook
> is how we *verify* that claim against live traffic instead of trusting it.

## TL;DR — the loop for each domain

1. Add the domain to `SHADOW_ORM_DOMAINS` on NAS → deploy.
2. Let real traffic flow for a representative window (≥1h, ideally a full daily
   cycle covering admin usage / cron / peak).
3. Query `application_logs` for `module='orm_shadow'` diffs. **Zero diffs over
   representative traffic = parity proven.** Any diff → investigate + fix the
   ORM repo, re-shadow.
4. Flip `USE_ORM_<DOMAIN>=true` → deploy → monitor the error funnel.
5. Stable for ~a week → remove the domain from `SHADOW_ORM_DOMAINS` → move to the
   next domain.

Rollback at any point: `USE_ORM_<DOMAIN>=false` + restart → instant revert to
REST (the factory choke point short-circuits). No data migration to undo.

---

## How shadow-compare works

`backend/app/db/shadow_compare.py` provides `ShadowRepo`, gated by the
`SHADOW_ORM_DOMAINS` env (a comma-list on `settings`, **independent** of the
`USE_ORM_*` flags). Empty by default → fully inert.

When a domain is in the shadow set (and the SQLAlchemy engine is configured),
its factory returns `ShadowRepo(Rest(), Orm(), '<domain>')` **instead of**
`Rest()`. `ShadowRepo` dispatches by method-name prefix via `__getattr__`:

- **Read methods** (`get_` / `list_` / `find_` / `count_` / `search_` /
  `fetch_` / `exists_`): the REST result is returned to the caller **always**
  (REST stays the live source of truth); then a fire-and-forget
  `asyncio.create_task` runs the ORM method, deep-diffs it against the REST
  result, and on mismatch logs a WARNING to `application_logs` with
  `module='orm_shadow'`, `{domain, method, args_summary, diff}` (diff capped at
  ~2KB).
- **All other methods** (writes — `create_` / `update_` / `delete_` / `save_` /
  `upsert_` / …): plain passthrough to the **REST repo only**. Writes are
  **never** dual-run — that would double-mutate the database.

Guarantees:

- **Read-only on the caller's path** — the caller always receives the REST
  value; the ORM call + diff happen on a detached background task.
- **No propagation** — any exception in the ORM call or the diff is caught and
  logged as an `orm_shadow failure`; it never reaches the request.
- **No double-mutation** — only read-prefixed methods are dual-run.

### The read-method prefix heuristic — known gaps

Dispatch is purely by name prefix, so two failure modes exist; audit each domain
before shadowing it:

- **A read that does NOT match a prefix** → it is treated as a write
  (passthrough, never shadowed). Such a method is silently *not parity-checked*.
  Audit each repo's read methods for non-conforming names (e.g. a method named
  `stats_since`, `app_logs_between`, `request_logs_between` — these are reads but
  start with `stats_` / `app_` / `request_`, **not** a read prefix). **Tier-0
  note:** `MonitoringRepository` (`request_logs_between` / `app_logs_between` /
  `frontend_error_count`), `AuditLogsRepository.list_since`, and
  `RequestLogsRepository.stats_since` do **not** match the prefixes and will pass
  through un-shadowed. These are still safe (passthrough = REST, the live truth)
  — they're just not parity-validated by the harness. For full coverage on those,
  either rename to a read prefix, or spot-check them manually with a one-off ORM
  vs REST script during the shadow window. `count_`/`list_with_filters`/`get_*`
  methods on those repos ARE covered.
- **A write that DOES match a read prefix** → it would be wrongly shadowed
  (dual-run). No such method exists in the repos today (writes are
  `create_/update_/delete_/insert/upsert/save/set_/...`), but any future
  `get_or_create_*` style method is the trap to watch — it starts with `get_`
  but mutates. If one is added, exclude it explicitly before shadowing its
  domain.

The diff is **order-sensitive** for lists: an ORM repo that returns the same
rows in a different order IS a finding, because callers depend on REST's
ordering contract.

---

## Wiring status

**Tier-0 factories are wired** with the shadow branch as the working example
(the `if shadow_enabled('<d>') and is_configured(): return ShadowRepo(...)`
branch sits between the `USE_ORM` branch and the final `return Rest()`):

- `admin/audit_logs_repository.py` → `admin_audit_logs`
- `admin/monitoring_repository.py` → `admin_monitoring`
- `admin/stats_repository.py` → `admin_stats`
- `admin/system_settings_repository.py` → `admin_system_settings`
- `admin/table_preferences_repository.py` → `admin_table_preferences`
- `admin/search_repository.py` → `admin_search`
- `admin/request_logs_repository.py` → `admin_request_logs` (3 factories:
  request / frontend-error / app logs)
- `admin/tasks_repository.py` → `admin_tasks`
- `admin/videos_repository.py` → `admin_videos`
- `admin/alert_rules_repository.py` → `admin_alert_rules`

**The remaining tiers are wired per-tier as they're rolled out** — the same
three-line shadow branch is added to each domain's factory when that tier comes
up. Until wired, a domain's factory simply has no shadow branch (it behaves
exactly as today regardless of `SHADOW_ORM_DOMAINS`).

---

## Flip-order tiers (low-stakes / low-traffic → high-stakes)

Roll out **strictly in this order**. Within a tier, shadow + flip one domain at
a time; don't batch-flip a whole tier in one deploy.

### Tier 0 — admin read-only (lowest risk: admin-only, mostly read, low traffic)
`ADMIN_AUDIT_LOGS`, `ADMIN_MONITORING`, `ADMIN_STATS`, `ADMIN_SYSTEM_SETTINGS`,
`ADMIN_TABLE_PREFERENCES`, `ADMIN_SEARCH`, `ADMIN_ALERT_RULES`,
`ADMIN_REQUEST_LOGS`, `ADMIN_TASKS`, `ADMIN_VIDEOS`
*Rationale:* admin console only, no end-user blast radius, dominated by reads
over log tables. Best place to exercise the harness itself.

### Tier 1 — user logs + simple lookups (low write complexity, value-type-simple)
`LOGS`, `USER_LOGS`, `STYLE_TEMPLATES`, `TAG_PREFERENCES`, `NOTIFICATIONS`,
`SCRIPTS`, `NOUS`, `SESSION_MEMORY`, `PERMISSION`, `USER_SETTINGS`, `COLLECTIONS`
*Rationale:* per-user CRUD with mostly flat shapes; failures are user-scoped and
non-destructive. `USER_SETTINGS` writes a shared jsonb column — verify merge-not-
replace parity (see `bug_user_settings_json_clobber`). `COLLECTIONS`
(`smart_collections`) is plain per-user CRUD; its model pre-existed in the
reflected set, so no drift-verify gate beyond the normal shadow window.

### Tier 2 — mid-complexity lookups / moderate writes
`LIBRARIES`, `PROJECTS`, `STORYBOARD`, `AI`, `INVITE`, `ISSUE`, `AGENT`,
`AGENT_RUNS`
*Rationale:* richer shapes + relations, but still feature-scoped. `AGENT_RUNS`
is the original silent-rollback P0 driver — flip with extra write monitoring.

### Tier 3 — central tables with many call-sites
`TAGS`, `ADMIN_TAGS`, `SKILL`, `MEDIA`, `WORKFORCE`, `ADMIN_TRANSCODE`, `ANALYSIS`
*Rationale:* high fan-out — a parity bug here touches many surfaces. Shadow for
a longer window to cover all call paths. `MEDIA` reads JOIN `resources` (relevant
to the resources epic — see Tier 7). `ANALYSIS` (`resource_analysis`) is
cross-table + pgvector + the `match_videos_by_embedding` RPC. The
`resource_analysis` table is created in final post-076 form by **mig 262** (it
was absent on the self-hosted prod); the model + repo are drift- and
integration-validated against the live dev DB (== prod). Still verify the
embedding/RPC read shapes during the shadow window.

### Tier 4 — authorization (uuid `==`/`!=` risk)
`REVIEW`, `TEAM`, `ADMIN_USERS`, `ADMIN_TEAMS`
*Rationale:* these gate access. Strategy-C uuid→str coercion is most consequential
here: an `==`/`!=` on a uuid that drifts type silently denies/grants. Diff every
membership / ownership read carefully before flipping.

### Tier 5 — secrets
`COOKIES`, `API_KEY`, `USER_MCP_SERVERS`
*Rationale:* a parity bug can leak or drop credentials. Low traffic, but verify
that masked/decrypted value shapes match exactly. `USER_MCP_SERVERS`
(`user_mcp_servers`) stores an **encrypted** `bearer_token` (encrypt-at-write /
decrypt-at-read) and returns a frozen `UserMCPServer` dataclass with `uuid.UUID`
ids — verify the decrypted token round-trips and the M3 `WHERE user_id == owner`
ownership filter holds on update/delete. The `user_mcp_servers` table is
(re)created by **mig 263** (original mig 194 was absent on the self-hosted prod);
the model + repo are drift- and integration-validated against the live dev DB.

### Tier 6 — money (highest stakes)
`POINTS`, `PAYMENT`, `ADMIN_CREDITS`, `COMMITMENT`, `APPROVAL`
*Rationale:* balance / ledger / order correctness. Any diff is a release blocker.
Shadow over a full billing cycle's worth of representative traffic; flip last,
one domain at a time, with the error funnel watched live through the deploy.

### Tier 7 — resources (the Epic-A prerequisite)
`RESOURCES` → **then** `SCOPE_ENFORCE_RESOURCES`
*Rationale:* `USE_ORM_RESOURCES` must be flipped (and shadow-proven) **before**
scope enforcement is meaningful — the scope choke point only sees ORM statements;
the REST path bypasses it entirely. See
[`resources-scope-flip-readiness.md`](./resources-scope-flip-readiness.md) for
the full scope flip/rollback procedure. Order: shadow `resources` → flip
`USE_ORM_RESOURCES=true` → verify ORM-path parity → flip
`SCOPE_ENFORCE_RESOURCES=true` (its own runbook).

> **Deferred-repo finish wave (now in scope):** `collections` (Tier 1),
> `analysis` (Tier 3), `user_mcp_servers` (Tier 5) were the last three REST repos
> without an ORM sibling. They are now code-complete behind `USE_ORM_COLLECTIONS`
> / `USE_ORM_ANALYSIS` / `USE_ORM_USER_MCP_SERVERS` (all default false, inert).
> ✅ **Validated against the live dev DB (== prod schema):** the
> `resource_analysis` and `user_mcp_servers` tables were ABSENT on the self-hosted
> prod (their original migrations 014→076 / 194 never ran there), so **mig 262 /
> 263** (re)create them; the schema-drift guard + both repos'
> `tests/integration/test_*_repository_orm.py` now pass against the live DB. Once
> mig 262/263 land on prod the tables exist and these domains can shadow + flip
> like any other. `collections` reused a pre-existing model + table.
>
> **Genuinely out of scope:** none remain — every public-table repo now has an
> ORM path (or is intentionally REST-only infra like `base_repository`).

---

## Per-step monitoring SQL

Run against the self-hosted Supabase (`application_logs` / `task_tracking`).

### 1. Shadow diff check (during the shadow window — expect **0 rows**)
```sql
SELECT message, COUNT(*)
FROM application_logs
WHERE module = 'orm_shadow'
  AND logged_at >= NOW() - INTERVAL '1 hour'
GROUP BY message
ORDER BY count DESC;
```
Any row = a real parity finding (mismatch) or an `orm_shadow failure` (the ORM
path raised). Investigate before flipping. Widen the interval to match the actual
shadow window (e.g. `INTERVAL '24 hours'`).

To split mismatches from failures:
```sql
SELECT
  CASE WHEN message LIKE 'orm_shadow mismatch%' THEN 'mismatch'
       WHEN message LIKE 'orm_shadow failure%'  THEN 'failure'
       ELSE 'other' END AS kind,
  COUNT(*)
FROM application_logs
WHERE module = 'orm_shadow' AND logged_at >= NOW() - INTERVAL '1 hour'
GROUP BY kind;
```

### 2. Post-flip error funnel (right after `USE_ORM_<D>=true` — watch ~30 min)
```sql
SELECT module, message, COUNT(*)
FROM application_logs
WHERE level = 'ERROR'
  AND logged_at >= NOW() - INTERVAL '30 minutes'
GROUP BY module, message
ORDER BY count DESC;
```
A spike in the flipped domain's module = roll back immediately.

### 3. Schema-drift / `does not exist` / `violates` funnel (from CLAUDE.md)
```sql
SELECT module, message, COUNT(*)
FROM application_logs
WHERE level = 'ERROR'
  AND logged_at >= NOW() - INTERVAL '7 days'
  AND (message ILIKE '%does not exist%' OR message ILIKE '%violates%not-null%')
GROUP BY module, message
ORDER BY count DESC;
```
Catches column/type drift the ORM models might trip that REST tolerated.

### 4. Task failure cross-check (for domains that drive workflows, e.g. MEDIA/RESOURCES)
```sql
SELECT error_msg, COUNT(*)
FROM task_tracking
WHERE phase = 'failed'
  AND completed_at >= NOW() - INTERVAL '30 minutes'
GROUP BY error_msg
ORDER BY count DESC;
```

---

## Rollback

**For a `USE_ORM_<DOMAIN>` flip:** set `USE_ORM_<DOMAIN>=false` and restart.
Instant and complete — the factory choke point short-circuits back to the REST
repo with the flag off. **No data migration to undo** (the ORM repo writes to the
same tables in the same shapes; there is nothing to back out).

**For `SCOPE_ENFORCE_RESOURCES`:** set it `=false` + restart — see
[`resources-scope-flip-readiness.md`](./resources-scope-flip-readiness.md).

Shadow itself never needs rollback — removing a domain from `SHADOW_ORM_DOMAINS`
(or leaving it) has zero effect on the live response. But to stop the background
ORM load, drop the domain from the set + restart.

---

## NAS ops note — how the flags actually get set

`USE_ORM_*` and `SHADOW_ORM_DOMAINS` are **env vars**, read at process start.
On prod they live in `/volume1/docker/mediahub/docker/.env` (bind-mounted into
the container as `/app/.env`). To change one:

```bash
ssh user@nas -p 1122
cd /volume1/docker/mediahub/docker
# edit .env: add the domain to SHADOW_ORM_DOMAINS, or set USE_ORM_<D>=true
sudo docker compose stop -t0 mediahub-app-backend
sudo docker compose start mediahub-app-backend
# repeat for mediahub-worker if the domain runs in workflows (Tier 3/7)
```

⚠️ **Watchtower does NOT apply compose / env changes** — it only pulls a new
image and restarts with the container's *existing* env. Editing `.env` + a
`stop`/`start` cycle is what actually picks up the new value (it persists across
Watchtower image pulls because `.env` is a host bind-mount). See
`reference_nas_backend_env` for the full mechanics, and CLAUDE.md →
"WATCHTOWER 不会应用 docker-compose 配置变更".

> Both gateway and worker read the same `.env`. For Tier 0–2 (HTTP-only admin /
> user reads) restarting the backend is enough. For Tier 3 (`MEDIA`,
> `WORKFORCE`, `ADMIN_TRANSCODE`) and Tier 7 (`RESOURCES`), the worker executes
> the workflows — restart it too.

---

## Cleanup (≈2 weeks after a domain is stably flipped)

- Remove the domain from `SHADOW_ORM_DOMAINS`.
- Once **all** domains in a tier are stable, the default of `USE_ORM_<D>` can be
  flipped to `true` in `config.py` and the legacy REST `<Repo>` deleted (the
  factory + shadow branch collapse). Do the REST-repo deletion only after the
  shadow set no longer references it.
