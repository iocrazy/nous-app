# DBOS Workflow Ports — Deferred to PR-D3

PR-D2.3 ported 3 user-facing tasks to demonstrate the orchestrator pattern:
`ai_summary` (full real-data port), `ai_transcription` (delegates to
existing WhisperService), and `thumbnail` (smallest, full port).

The remaining ~20 task_types are listed below with porting notes so the
PR-D3 sequence can pick them up without re-investigating each.

## Sized: easy (`<100 LOC` original Celery task, can wrap existing service)

| task_type | Service to delegate to | Notes |
|---|---|---|
| `parse` (parse_single_link_task) | (multiple — DouyinParser, YtDlpExtractor, etc) | 1033-line task file but the task body itself is mostly orchestration; extract the body into a callable + DBOS-wrap. PR-D3a candidate. |
| `upload` | (no dedicated celery task — file uploads handled in handler) | Probably skip — already synchronous in handlers. |
| `transcode` | TranscodeService (in transcode_tasks.py) | 427-line file with Redis pub/sub progress reporting + multi-stage retry. Port carefully in PR-D3a — needs end-to-end test against a real ResourceVersion. |
| `ai_extract` (analyze_l1) | VisualAnalysisService + EmbeddingService | ✅ ported — `app/workflows/analyze_l1.py`. analyze_l2 + batch_analyze + analyze_pending are wrappers around the same service; port pattern identical, add when needed. |
| `script_outline_gen` | script_ai_service | ✅ ported — `app/workflows/script_outline.py`. LLM call + chapter-node persistence as two steps. |

## Sized: medium (multi-step, internal state)

| task_type | Notes |
|---|---|
| `download` (download_unified_task) | Douyin vs yt-dlp branching; extract by media_type. ~200 LOC port. PR-D3a (or split D3a-1 and D3a-2). |
| `storyboard_image_gen` / `storyboard_video_gen` / `storyboard_script_split` / `storyboard_video_analysis` / `storyboard_scene_detect` / `storyboard_export` | Form a sub-workflow chain. PR-D3b — port as a single `storyboard_pipeline_workflow` with 6 steps. |
| `agent_runs_sweeper` | ✅ ported — `app/workflows/agent_runs_sweeper.py`. `@DBOS.scheduled('* * * * *')`; advisory lock dropped (DBOS dedup via deterministic workflow_id). Celery-beat schedule entry should be removed in D3d. |

## Sized: complex (touches more state, FYI)

| task_type | Notes |
|---|---|
| `memory_tasks` | ✅ ported — `app/workflows/write_memory.py`. Note: file is actually a single task with two phases (load messages + extract+persist), not multiple sub-tasks as the original ledger note suggested. |
| `signals` | Celery signal hooks for orchestrator dedup; redesign for DBOS native dedup (workflow_id) before porting. PR-D3d candidate. |

## Scheduled jobs (`scheduled_tasks.py`) — bucket for D3c2

These all run on celery-beat today. Each becomes a `@DBOS.scheduled(<cron>)`
workflow. None need design work — they delegate to existing services and
the cron expressions live in `backend/app/celery_app.py` `beat_schedule`.

| function | Notes |
|---|---|
| `cleanup_temp_files` | Pure FS cleanup, no DB writes; trivial port. |
| `retry_failed_downloads` | ⚠️ has the user_id=None orphan bug (CLAUDE.md memory). Source-side `WHERE user_id IS NOT NULL` filter must be preserved on port. |
| `update_statistics` | Aggregation; delegate to existing analytics service. |
| `update_system_status` | Health snapshot writer. |
| `reset_monthly_quotas` | Run on cron; safe to memoize via deterministic workflow_id. |
| `reap_stuck_pending_tasks` | Touches unified_tasks; coordinate with D3d signals redesign so we don't double-reap. |
| `cleanup_old_unified_tasks` | Trivial GC. |
| `health_check` | Trivial. |
| `recover_stale_orchestrator_locks` | Will be obsolete once DBOS dedup replaces orchestrator locks (D3d). |
| `grant_daily_free_points` / `reclaim_daily_free_points` | Daily cron pair; quota mutations — write integration test before port. |
| `cleanup_trashed_resources` | Soft-delete sweeper; delegate to ResourceRepository. |

## Pattern for ports

Each new workflow file:
1. lives in `app/workflows/<task_type>.py`
2. defines `@DBOS.workflow def <name>_workflow(...)` plus 1-3 `@DBOS.step`
   helpers
3. delegates to existing service code where possible (don't re-derive
   business logic)
4. imports + re-exports from `app/workflows/__init__.py`
5. has matching row in `dbos_workflow_routing` (added in migration 169) —
   start in `'celery'`, flip to `'shadow'` for parallel-run window, then
   `'dbos'` for cutover
