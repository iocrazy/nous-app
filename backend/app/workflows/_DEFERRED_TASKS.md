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

## Scheduled jobs (`scheduled_tasks.py`) — ✅ D3c2 done

11 of 12 ported as @DBOS.scheduled (update_statistics is deprecated
no-op, dropped). Files:

| group | file | jobs |
|---|---|---|
| cleanup | `app/workflows/scheduled_cleanup.py` | cleanup_temp_files / cleanup_trashed_resources / cleanup_old_unified_tasks |
| health | `app/workflows/scheduled_health.py` | update_system_status (every 30s, 6-field cron) / health_check |
| recovery | `app/workflows/scheduled_recovery.py` | retry_failed_downloads / reap_stuck_pending_tasks / recover_stale_orchestrator_locks |
| quotas | `app/workflows/scheduled_quotas.py` | reset_monthly_quotas / grant_daily_free_points / reclaim_daily_free_points |

Notable callouts honored on port:
- `retry_failed_downloads` keeps the `if not user_id: skipped_orphan += 1; continue` filter from the CLAUDE.md regression notes.
- `update_system_status` preserves the 30s cadence via 6-field cron `*/30 * * * * *` (DBOS croniter is initialized with `second_at_beginning=True`).
- `recover_stale_orchestrator_locks` ported as transitional; will be deleted in D3d once DBOS workflow_id replaces the orchestrator dedup lock model.
- `grant_daily_free_points` keeps the `existing is None and existing.data` guard for the supabase-py `maybe_single()` quirk.

D3d follow-ups when removing celery-beat:
1. delete `backend/app/celery_app.py` `beat_schedule` entries for ported jobs
2. delete `recover_stale_orchestrator_locks` workflow + step (obsolete)
3. add integration test for grant/reclaim daily_free_points pair before flipping celery-beat off (real money proxy)

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
