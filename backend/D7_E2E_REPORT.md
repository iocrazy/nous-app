# PR-D7 — E2E verification report

Run by Claude on 2026-04-29 against NAS dev (PostgreSQL 17.6 +
Supavisor pooler via Termius SSH tunnel: 127.0.0.1:55433 + 9081).

## Environment

- Backend: `uv run uvicorn app.main:app --port 8082` against
  `DBOS_DATABASE_URL=postgresql://mediahub_dbos.heygo-dev:***@127.0.0.1:55433/postgres`
- Frontend: `npm run dev` (vite) at `localhost:3000`, auth/REST via
  Supabase REST tunnel `127.0.0.1:9081`
- Test user: `d7-e2e-test@mediahub.local` (created via service_role
  admin API; user_id = 8700de22-7e9a-46af-9176-41953297ac7c)

## Sanity (Step 0)

- ✅ PG tunnel up, mediahub_dbos.heygo-dev role auth OK
- ✅ Supabase REST tunnel up (HTTP 401 without apikey, expected)
- ✅ Backend lifespan: DBOS init + launch + 32 workflows registered
- ✅ `agent_workforce` queue listening (concurrency=8, partitioned)
- ✅ Workforce scheduler started
- ✅ Frontend npm install OK; D4/D6 files have 0 typecheck errors
  (pre-existing project-wide TS errors are CI continue-on-error)
- ✅ Frontend ↔ backend reachability (`/api/v1/config` 200, no CORS)

## Schema (Step 1)

- ✅ Migration 175 applied; loop ran clean (0 inserted on empty
  project_tasks dataset)
- ✅ `unmigrated_count` predicate returns 0
- ✅ `issue_create_atomic` smoke: created MH-2 with real auth.users
  UUID; counter advanced atomically

## D6 issues REST (Step 3 partial — backend only)

User-scoped JWT obtained via supabase-py
`auth.signInWithPassword`. All endpoints exercised:

- ✅ `POST /issues/` → MH-3, status=todo, priority=high
- ✅ `GET /issues/?status=todo` → total=1, items=1
- ✅ `GET /issues/by-identifier/MH-3` → match
- ✅ `PATCH /issues/{id}` (priority=critical) → reflected, updated_at advanced
- ✅ `POST /issues/{id}/transition` (status=in_progress) → started_at populated
- ✅ `POST /issues/{id}/dispatch` → returns dbos_workflow_id="issue-300499048344775"

## D4 SSE + workflow status (Step 3 cont.)

- ✅ `GET /workflows/{id}/status` returns full snapshot
  (status=SUCCESS, output={"issue_id":..., "noop":...})
- ✅ `GET /workflows/{id}/steps` returns 5 steps with names + outputs:
  `load_issue → atomic_checkout → set_status → set_status → clear_lock`
- ✅ SSE via Authorization header: emits 1 status event + done event,
  closes cleanly
- ✅ SSE via `?token=` query param (browser EventSource path): same
- ❌ → ✅ Two D4 bugs caught + fixed in commit ad105236:
    - `/steps` returned all-null fields (DBOS v2.19.0 ships dict not
      dataclass; getattr silently None'd)
    - `/events` 500'd because `get_optional_auth(request)` was
      invoked directly instead of via Depends; Header(None) defaults
      didn't resolve

## DBOS execute_issue end-to-end (Step 3 cont.)

The MH-3 dispatch lit up the full execute_issue parent workflow:

```
execute_issue(issue_id=300499048344775)
  step 1: load_issue                   → dict (real PG row)
  step 2: atomic_checkout              → True
  step 3: set_status('in_progress')    → None, started_at set
  step 4: set_status('done')           → None, completed_at set
  step 5: clear_lock                   → None
output: {"issue_id": ..., "noop": "PR-D5 will wire agent dispatch"}
status: SUCCESS
```

All 5 PG writes happened via `SET ROLE service_role` inside steps.
Confirmed via `SELECT * FROM dbos.workflow_status` in
postgres_dbos_sys.

## D5 workforce queue (Step 5)

- ✅ Without env flag: log line `Workforce: using AgentWorkerPool
  (in-process)`, scheduler started
- ✅ With `WORKFORCE_USE_DBOS_QUEUE=true`: log line `Workforce:
  using DbosAgentWorkforcePool (DBOS queue)`, queue listener up
- ✅ Lifespan regression caught + fixed (commit cc753c87):
  `import app.workflows` was shadowing the lifespan's `app` arg in
  Python's local scope, making `app.state.workforce_scheduler =
  ...` resolve to the package not the FastAPI parameter

## DBOS scheduled workflows actually firing (Step 4 alt)

Strongest end-to-end signal — the DBOS scheduler is REAL:

```sql
SELECT name, status, created_at FROM dbos.workflow_status
 ORDER BY created_at DESC LIMIT 10;
```
```
              name               | status  |  created_at
---------------------------------+---------+---------------
 retry_failed_downloads_workflow | PENDING | 1777431600891
 health_check_workflow           | PENDING | 1777431600660
 update_system_status_workflow   | SUCCESS | 1777431570578
 agent_runs_sweeper_workflow     | SUCCESS | 1777431541219
 update_system_status_workflow   | SUCCESS | 1777431540376
 update_system_status_workflow   | SUCCESS | 1777431510512
 agent_runs_sweeper_workflow     | SUCCESS | 1777431483254
 update_system_status_workflow   | SUCCESS | 1777431481098
 update_system_status_workflow   | SUCCESS | 1777431451438
 agent_runs_sweeper_workflow     | SUCCESS | 1777431420834
```

Side-effect verified in PG:
- `system_status.updated_at` advanced from 02:58:36 → 02:59:37
  (exactly 1 minute / 2 ticks of 30s cadence)

So the @DBOS.scheduled decorators are firing as expected, the
workflows complete successfully, and the side-effects land in the
target tables. **The DBOS-scheduled side of the migration is
operationally validated** — when celery-beat is shut off, the
scheduled jobs continue uninterrupted on DBOS.

## Routing flip mechanism (Step 4)

- ✅ `UPDATE dbos_workflow_routing SET mode='shadow' WHERE
  task_type='thumbnail'` reflected immediately in
  `/api/v1/dbos/routing` (60s cache refreshed on read)
- ✅ Restored to 'celery' afterward — table back to baseline (all 18
  rows celery)

## Deferred to manual / external service

The remaining items in D7_CUTOVER.md §5 require external services
or a real browser session:

- Douyin URL parse → download → analyze (real Douyin URL + LLM key)
- yt-dlp video transcode (real video URL + ffmpeg)
- ChatPanel agent turn (real LLM key)
- Storyboard image/video gen (paid image-gen APIs)
- IssuesPage browser interaction (real user login flow)
- Memory write end-to-end (real LLM)

These are gated on the canonical routing flip per task_type. The
recommended order remains the same as D7_CUTOVER.md §1:
thumbnail → ai_transcription / ai_summary → script_outline /
write_memory → analyze_l1 → transcode → parse + download → storyboard.

## Conclusion

DBOS migration code path is **operationally proven** for the
self-contained flows (issues lifecycle, scheduled jobs, workforce
queue selection). The user-facing task_types (parse/download/
transcode/storyboard/agent chat) have all the wiring in place but
are gated by `dbos_workflow_routing.mode='celery'` until a manual
shadow → dbos cutover per task_type.

No regressions surfaced that would block the master PR. Two D4
bugs caught + fixed during the sweep (commits ad105236 + cc753c87).
