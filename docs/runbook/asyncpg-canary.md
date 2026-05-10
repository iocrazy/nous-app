# asyncpg + Supavisor canary runbook (Bug C)

Operational guide for rolling out the asyncpg + Supavisor data path
behind three per-repo feature flags:

- `USE_ASYNCPG_AGENT_RUNS` — Phase 2 (`AgentRunsRepository`)
- `USE_ASYNCPG_RESOURCES` — Phase 3a-3e (`ResourcesRepository`)
- `USE_ASYNCPG_MEDIA` — Phase 4a/4b/4c (`MediaRepository`)

All three flags are currently **off** in every environment — this doc
walks through how to flip them in dev, watch for regressions, and
promote to prod once stable. Flags are independent: flip one at a time
to keep the blast radius small.

## What this validates

[#194 Bug C](https://github.com/iocrazy/mediahub/issues/194) — after
~8h of backend uptime, `supabase-py` requests start timing out or
returning stale connections. Root cause traced to:

- Kong default `keepalive_timeout=75s` on the new Supabase prod stack
  (created during the 5/7 migration; no explicit override)
- `postgrest._async.client` forces `http2=True`
- `httpx`'s pool `keepalive_expiry` doesn't trigger under HTTP/2
  multiplexed traffic — the pool keeps a stale connection forever
- Result: requests hang or fail silently after server-side close

The fix bypasses the entire HTTP/Kong/PostgREST chain by going direct
PG via [Supavisor](https://supabase.com/docs/guides/database/connecting-to-postgres#supavisor)
(Supabase's official transaction-mode pooler) with `asyncpg`.

## What was migrated

### `ResourcesRepository` (Phase 3a-3e — PRs #213 / #219 / #220 / #221 / #222)

41 of 47 public methods route through `ResourcesRepositoryAsyncpg`
when `USE_ASYNCPG_RESOURCES=true`. The remaining 6 (`add_resource_tag`,
`remove_resource_tag`, `get_resource_tags`, `get_smart_folders`,
`create_smart_folder`, `execute_smart_rules`) inherit from the legacy
supabase-py implementation via MRO — lower-traffic, intentionally
deferred. If the canary surfaces them as Bug C contributors, see
Phase 3f (TBD).

### `MediaRepository` (Phase 4a/4b/4c — PRs #225 / #234)

12 public methods overridden in `MediaRepositoryAsyncpg` covering
the full `parsed_media` table surface:

- **Phase 4a CRUD**: `create`, `get_by_platform_id`, `get_by_id`,
  `update`, `delete`, `get_downloaded_by_platform_id`
- **Phase 4b lists**: `mark_stale_downloads_failed`,
  `get_pending_downloads`, `get_all`, `get_user_media_list`
- **Phase 4c search/stats**: `search`, `get_statistics`

The 9 wrapper methods (`check_*`, `mark_*`, `get_music_data`,
`mark_download_failed`) are NOT overridden — they call into
`self.get_by_platform_id` / `self.update` and Python MRO routes them
through the asyncpg overrides automatically. Flag: `USE_ASYNCPG_MEDIA`.

### `AgentRunsRepository` (Phase 2 — PR #212)

Already canary'd, behind `USE_ASYNCPG_AGENT_RUNS`. Telemetry path,
lower volume than the other two.

## Pre-flight: env config

The asyncpg path requires `SUPAVISOR_DATABASE_URL` set. **Without it,
the factory falls back to legacy supabase-py with a warning** —
flipping the flag alone won't crash anything, but it also won't
exercise asyncpg.

### DSN format

```
postgresql://postgres.{tenant_id}:{password}@{host}:{pooler_port}/postgres
```

Notes:

- **Tenant ID is encoded in the user**: `postgres.{tenant_id}`, not
  in a separate query param. This is the Supavisor convention.
- Use the **transaction-mode pooler port** (typically `6543` inside
  the container, mapped to a host port like `6545`). Do NOT use the
  session-mode port — `statement_cache_size=0` in `pg_pool.py`
  expects transaction mode.
- Password is the Postgres `postgres` user password from the
  Supabase dashboard.

### Where each env's DSN lives

| Env | Source |
|-----|--------|
| Local dev (Supabase CLI) | `postgresql://postgres:postgres@127.0.0.1:54322/postgres` (no Supavisor — single-process PG; transaction pooler not needed but the asyncpg path still works) |
| Self-hosted dev (NAS) | `mediahub-sb-dev` stack, host `192.168.50.9`, pooler exposed on port `6543` (verify in `docker-compose.yml`). Tenant: `heygo-dev` |
| Self-hosted prod (NAS) | `mediahub-sb-prod-pooler` container, host `192.168.50.9`, pooler exposed on port `6545`. Tenant: `heygo-prod` |

Pull the actual password from each Supabase admin console (`Settings → Database → Connection string`).

### Backend env vars to set

```bash
# In backend/.env (or whichever worktree env file is in use):

SUPAVISOR_DATABASE_URL=postgresql://postgres.heygo-dev:<PASSWORD>@192.168.50.9:6543/postgres
SUPAVISOR_POOL_MIN_SIZE=2     # default; tune if needed
SUPAVISOR_POOL_MAX_SIZE=10    # default; raise for high-concurrency env
USE_ASYNCPG_AGENT_RUNS=true   # already canary'd in Phase 2 — leave on
USE_ASYNCPG_RESOURCES=true    # Phase 3a-3e
USE_ASYNCPG_MEDIA=true        # Phase 4a/4b/4c
```

**Flip one at a time** if you're not sure the env is healthy. Each
flag is independent — start with `USE_ASYNCPG_RESOURCES`, soak 24h,
then add `USE_ASYNCPG_MEDIA`. If something breaks under one flag, the
narrow blast radius makes diagnosis trivial.

## Flip procedure (dev → staging → prod)

### 1. Dev (worktree or local)

```bash
# In the relevant backend dir:
echo 'SUPAVISOR_DATABASE_URL=...' >> .env       # Adjust per env
echo 'USE_ASYNCPG_RESOURCES=true' >> .env

# Restart the backend (uvicorn picks up the env on startup)
# If running under DBOS, restart the worker too.

# Verify the factory is routing through asyncpg:
curl -s http://localhost:$BACKEND_PORT/api/v1/health  # adjust path
# Then check logs — first call to ResourcesRepository should NOT
# log the "USE_ASYNCPG_RESOURCES=true but SUPAVISOR_DATABASE_URL
# is empty — falling back" warning. If it does, the URL didn't load.
```

### 2. Smoke test the hot paths

These exercise the migrated methods that matter most for Bug C.
**Group A** runs under `USE_ASYNCPG_RESOURCES=true`; **Group B** runs
under `USE_ASYNCPG_MEDIA=true`. Run group A first, then add group B.

#### Group A — `ResourcesRepository`

```
A1. Resource library list:
      GET /api/v1/resources?scope_type=user&scope_id=<your_user_uuid>
      Expect: 200 with embedded {resource: {...}} shape per row
      Hits get_resource_items (Phase 3e — most complex JOIN + filter)

A2. Resource lookup:
      GET /api/v1/resources/<id>
      Hits get_resource_by_id (Phase 3a — most-called read)

A3. Media fetch (cross-user dedup):
      POST /api/v1/media/fetch with a Douyin URL someone else has
      already downloaded
      Hits get_completed_resource_by_url_and_creator (the L2 dedup
      probe — most-called supabase-py path under Bug C)

A4. Folder ops:
      Create folder, nest subfolder, trash root, restore root
      Hits restore_folder_cascade / trash_folder_cascade
      (Phase 3d — recursive CTE replaces legacy BFS)
```

#### Group B — `MediaRepository`

```
B1. Media library list (per-user JOIN):
      GET /api/v1/media?limit=50
      Hits get_user_media_list (Phase 4b — JOIN through resources +
      resource_id overlay shape preservation)

B2. Media detail:
      GET /api/v1/media/<id>
      Hits get_by_id (Phase 4a — the BIGINT-coercion path; broken
      under naive str input, see PR #225's empirical fix)

B3. Media search:
      Use the search UI with a keyword that returns results
      Hits search (Phase 4c — dynamic parameterized WHERE assembly)

B4. Library statistics:
      GET /api/v1/media/statistics  (or whatever the dashboard uses)
      Hits get_statistics (Phase 4c — single SQL aggregation;
      compare numbers against pre-flip dashboard)

B5. Parse + download a fresh URL:
      Triggers create + update + mark_*_as_downloaded wrappers
      The wrappers call self.update — verifies MRO routing works
      end-to-end (no leak back to legacy supabase-py)
```

For each: response should be identical to pre-flip (shapes + contents
match). If anything diverges, that's a parity bug — file an issue and
rollback the offending flag (the others can stay on).

### 3. Soak

Leave the dev env on the asyncpg path for **at least 24 hours of
real traffic**. The whole point is to validate that the connection
issue from Bug C doesn't recur — Bug C took ~8h of uptime to surface
in prod, so anything shorter than 24h doesn't really probe.

Watch for:

- `application_logs` ERROR rows referencing `asyncpg`,
  `pg_pool`, `Supavisor`, `connection`, `pool exhausted`, `timeout`.
  Query (via Supabase SQL editor or `mcp__supabase`):

  ```sql
  SELECT logged_at, module, message
  FROM application_logs
  WHERE level = 'ERROR'
    AND logged_at >= NOW() - INTERVAL '24 hours'
    AND (message ILIKE '%asyncpg%'
         OR message ILIKE '%pg_pool%'
         OR message ILIKE '%pool exhausted%'
         OR message ILIKE '%Supavisor%')
  ORDER BY logged_at DESC LIMIT 50;
  ```

- Type-binding bugs specific to Phase 4 (BIGINT coercion + datetime).
  These would surface as `asyncpg.exceptions.DataError` with a
  signature like `'str' object cannot be interpreted as an integer`
  or `expected datetime.datetime instance, got 'str'`:

  ```sql
  SELECT logged_at, module, message
  FROM application_logs
  WHERE level = 'ERROR'
    AND logged_at >= NOW() - INTERVAL '24 hours'
    AND (message ILIKE '%DataError%'
         OR message ILIKE '%cannot be interpreted as an integer%'
         OR message ILIKE '%expected%datetime%')
  ORDER BY logged_at DESC LIMIT 50;
  ```

- Repository-method-level errors. The asyncpg classes log
  `Failed to <verb> parsed_media:` / `Failed to get user media list:`
  / `搜索视频失败:` / `获取统计信息失败:` etc. on every caught
  exception:

  ```sql
  SELECT logged_at, message
  FROM application_logs
  WHERE level = 'ERROR'
    AND logged_at >= NOW() - INTERVAL '24 hours'
    AND (message ILIKE '%Failed to%parsed_media%'
         OR message ILIKE '%Failed to get user media list%'
         OR message ILIKE '%获取待下载列表失败%'
         OR message ILIKE '%搜索视频失败%'
         OR message ILIKE '%获取统计信息失败%')
  ORDER BY logged_at DESC LIMIT 50;
  ```

- DBOS workflow failures with new error patterns — DBOS uses asyncpg
  directly to PG already (separate connection), so it's not affected
  by the flag, but a misconfigured Supavisor URL could exhaust the
  worker's pg connections via collision.

- Frontend errors via `frontend_error_logs` (CORS-looking errors are
  often 500s in disguise — see `feedback_cors_looking_errors_are_500s.md`).

### 4. Promote to prod

Only after the dev soak shows clean for 24+ hours:

```bash
# On the NAS (where prod backend runs):
ssh nas
cd /volume1/docker/mediahub
sudo docker compose exec backend bash -c '
  echo "SUPAVISOR_DATABASE_URL=postgresql://postgres.heygo-prod:<PWD>@192.168.50.9:6545/postgres" >> .env
  echo "USE_ASYNCPG_RESOURCES=true" >> .env
'
sudo docker compose restart backend
```

Or — preferred — bake the env vars into the docker-compose service
definition + `docker compose up -d` for an immutable rollout.

After restart, run the same 4 smoke tests against prod (with a real
account, in a dedicated browser session that you can throw away).

## Rollback

Single-line revert per flag. Each repo can be rolled back independently
without touching the others — that's the whole point of separating
them. Either:

```bash
# Edit .env, set back to false:
USE_ASYNCPG_RESOURCES=false   # rollback Resources path only
USE_ASYNCPG_MEDIA=false       # rollback Media path only

# Restart backend.
```

Or — if `.env` is baked into the image — flip the env var in the
docker-compose service and `restart`. The factory immediately routes
back to the supabase-py path on next call. **No data migration to
undo** (the asyncpg path reads/writes the same Postgres tables; it
just bypasses the HTTP/PostgREST chain).

If something is corrupting data (extremely unlikely — all the
migrated methods are read-mostly + same SQL semantics as legacy),
investigate before the rollback so we don't lose forensics:

1. Capture the offending request from `api_request_logs`
2. Reproduce against legacy path with the flag flipped back
3. Diff the responses — if asyncpg and legacy disagree, that's a
   parity bug, file an issue with the diff

## Success criteria

After 24h prod soak with the flag on, all of these should hold:

- [ ] No new ERROR rows in `application_logs` referencing asyncpg /
      Supavisor / pg_pool
- [ ] No new pattern of `httpx`/`postgrest` errors that match the
      Bug C signature (read timeout / connection reset after long
      idle period)
- [ ] Frontend response times for resources list / detail / fetch
      not regressed (baseline from Grafana / load tests)
- [ ] L2 dedup probe still correctly short-circuits duplicate fetches
      (verify via `application_logs` for the `[ResourcesRepo] L2
      dedup` log line — should keep firing at the same rate)

If all green, leave the flag on permanently and start planning
Phase 5 (drop supabase-py from this repository entirely + remove the
legacy classpath).

## Related

- [#194 Bug C tracking issue](https://github.com/iocrazy/mediahub/issues/194)
- Phase 1 foundation: PR #211 (`pg_pool.py` + `repository_base.py`)
- Phase 2 pilot: PR #212 (AgentRunsRepository)
- Phase 3a-3e: PRs #213 / #219 / #220 / #221 / #222 (ResourcesRepository)
- Phase 4a/4b/4c: PRs #225 / #234 (MediaRepository)
- CI lint scope fix: PR #217
- Master format sweep + pre-commit: PR #218
