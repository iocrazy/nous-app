# DBOS Task Tracking — Architecture (post-D7)

This document captures how mediahub tracks task / workflow state after
the D7 cutover. It's the canonical reference for D8 (full unified_tasks
removal) and any future workflow work.

## TL;DR

```
┌─ FastAPI router (e.g. /media/fetch) ──────────────────────────┐
│  1. uuid.uuid4() → wf_id                                      │
│  2. mgr.create(unified_tasks, celery_task_id=wf_id)           │
│  3. start_workflow_routed(workflow_id=wf_id,                  │
│                          DBOSContextSetAuth(user=auth.uid))   │
│     └─→ DBOS dispatches workflow                              │
└───────────────────────────────────────────────────────────────┘
                       ↓
┌─ DBOS Workflow body (parse_workflow / download_workflow / …) ─┐
│  @DBOS.workflow()                                             │
│  @tracked_workflow(task_type=..., title_fn=...)               │
│  def workflow_fn(...):                                        │
│      mark_started_step()    ── unified_tasks.status=running   │
│      ... business steps ...                                   │
│      mark_completed_step()  ── unified_tasks.status=completed │
│  (on exception:)                                              │
│      mark_failed_step()     ── unified_tasks.status=failed    │
└───────────────────────────────────────────────────────────────┘
                       ↓
┌─ Frontend Task Center (TaskManagerContext) ───────────────────┐
│  Two parallel Realtime subscriptions:                         │
│   • public.unified_tasks (legacy, still authoritative)        │
│   • dbos.workflow_status (new, post-D7)                       │
│  DBOS_PATCH reducer matches by celery_task_id == workflow_uuid│
└───────────────────────────────────────────────────────────────┘
```

## Table topology

Both DBOS sys-DB and the app DB live in the **same Postgres database**
(`postgres`) on the `mediahub-sb-dev` instance:

| Table | Purpose | Owner |
|---|---|---|
| `public.unified_tasks` | Legacy task tracker | service_role |
| `dbos.workflow_status` | DBOS workflow lifecycle | mediahub_dbos |
| `dbos.operation_outputs` | DBOS step results | mediahub_dbos |
| `dbos.workflow_events` | DBOS pub/sub events | mediahub_dbos |
| `dbos.workflow_schedules` | DBOS @scheduled cron | mediahub_dbos |
| `public.dbos_workflow_routing` | Per-task_type mode (always `dbos` post-D7) | service_role |

Co-location was set up by switching DBOSConfig from
`database_url` to explicit `application_database_url` +
`system_database_url` both pointing at `postgres`. Default DBOS
behaviour creates a separate `<db>_dbos_sys` database (which would
prevent Supabase Realtime from publishing workflow_status changes).

## ID identity

| Layer | ID | Format | Source |
|---|---|---|---|
| `unified_tasks.id` | snowflake | bigint | DB sequence |
| `unified_tasks.celery_task_id` | wf_id | uuid string | uuid.uuid4() at router time |
| `dbos.workflow_status.workflow_uuid` | wf_id | uuid string | router-supplied via SetWorkflowID |
| Child workflows | `<parent-uuid>-<step_idx>` | derived | DBOS auto |

The router pre-generates `wf_id = uuid.uuid4()` so the unified_tasks
INSERT and DBOS dispatch share the same ID. Without this, the
@tracked_workflow decorator's mark_started_step UPDATE
(WHERE celery_task_id=X) would race the router's post-dispatch
_atomic_update and leave `started_at=null` forever.

## Lifecycle status mapping

| DBOS status | unified_tasks.status (via @tracked_workflow) |
|---|---|
| PENDING | (router INSERTs as `pending`) |
| ENQUEUED | `pending` |
| PENDING (in body) | `running` (mark_started_step) |
| SUCCESS | `completed` (mark_completed_step, progress=100) |
| ERROR | `failed` (mark_failed_step, error_msg=str(exc)) |
| MAX_RECOVERY_ATTEMPTS_EXCEEDED | `failed` |
| CANCELLED | `cancelled` |

## Frontend Realtime (post-D7)

Channels subscribed by `TaskManagerContext`:

```ts
// 1. Legacy — RLS by user_id, INSERT/UPDATE/DELETE
.on('postgres_changes', { schema: 'public', table: 'unified_tasks',
    filter: `user_id=eq.${userId}` }, ...)

// 2. New (D7) — RLS by authenticated_user, INSERT/UPDATE
.on('postgres_changes', { schema: 'dbos', table: 'workflow_status' },
    payload => dispatch({ type: 'DBOS_PATCH', payload: payload.new }))
```

The `DBOS_PATCH` reducer matches by `celery_task_id == workflow_uuid`
and patches status / started_at / completed_at / error_msg from the
DBOS row. It never INSERTs — the router-side unified_tasks INSERT
covers user-visible rows, and the legacy channel picks them up.

RLS on `dbos.workflow_status`:
```sql
USING (authenticated_user = (auth.uid())::text)
```
Populated by `DBOSContextSetAuth(user=user_id)` wrapping every
dispatch in `start_workflow_routed`.

## Backend API surface

`/api/v1/workflows/...` (auth required):

| Endpoint | Method | Purpose |
|---|---|---|
| `/workflows` | GET | List user's workflows (filter by user, name, status, paginated) |
| `/workflows/{id}/status` | GET | Single workflow status (includes input/output/error) |
| `/workflows/{id}/steps` | GET | Step list (function_name, error per step) |
| `/workflows/{id}/events` | GET (SSE) | Live status stream until terminal |
| `/workflows/{id}/cancel` | POST | DBOS.cancel_workflow_async |
| `/workflows/{id}/resume` | POST | DBOS.resume_workflow_async |
| `/workflows/{id}/restart` | POST | DBOS.fork_workflow_async (start_step=1) |

These read directly from `dbos.workflow_status` — no unified_tasks
indirection.

## Known gaps / D8 candidates

1. **Drop unified_tasks**: once the frontend's DBOS_PATCH path is
   trusted (after some prod soak), remove the unified_tasks
   subscription, then the @tracked_workflow decorator (since DBOS
   itself is the source of truth), then the table.

2. **Rename `celery_task_id` column**: still named for historical
   reasons. Becomes a no-op once unified_tasks is dropped.

3. **Realtime subscription publishes ALL events** (subject to RLS).
   For a power user with hundreds of workflows / day, that's noisy.
   Consider client-side debouncing or a server-pushed broadcast
   channel that batches transitions.

4. **Workflow `output` deserialisation**: `_serialize_status` falls
   back to repr() for non-JSON-safe outputs. DBOS pickles outputs —
   for our use case strings + dicts are fine, but anything
   exotic (binary, custom classes) gets repr'd, not JSON.

5. **Reaper still exists**: `scheduled_recovery.reap_stuck_pending_tasks`
   in workflows/ — kept as a safety net for orphan unified_tasks
   rows from non-DBOS code paths. Likely removable in D8.

## Deploy checklist

- [ ] Migration 179 applied (RLS + GRANTs in Part A — owner = mediahub_dbos)
- [ ] Migration 179 Part B applied (publication ADD — owner = postgres)
- [ ] Backend DBOSConfig has `application_database_url` + `system_database_url`
      pointing at the SAME `postgres` DB
- [ ] Backend `start_workflow_routed` wraps with `DBOSContextSetAuth`
- [ ] Frontend TaskManagerContext subscribes to `dbos.workflow_status` schema

## Provenance

Implemented in branch `feat/dbos-pr-d2` over 17 commits:

- **D7 phase 3a (cleanup)**: physically deleted Celery modules,
  docker-compose celery services, env rename
- **E2E bug fixes**: timezone reaper, parse_helpers regressions,
  DBOS.start_workflow can't run inside @DBOS.step
- **`@tracked_workflow` decorator**: bridges 9 user-facing workflows
  (parse / download / transcode / ai_transcription / ai_summary /
  analyze_l1 / 8 storyboard / thumbnail) into unified_tasks
- **Phase 2**: `/api/v1/workflows` REST + DBOSContextSetAuth
- **Phase 3**: DBOS sys → app DB co-location + Realtime publication
  + frontend subscription
