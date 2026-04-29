# PR-D7 — Celery teardown + project_tasks drop checklist

State at end of D6 (this branch):

- All user-facing task_types ported to DBOS workflows under
  `app/workflows/` (parse / download / transcode / analyze_l1 /
  ai_summary / ai_transcription / thumbnail / script_outline /
  agent_runs_sweeper / write_memory / 11 scheduled / 8 storyboard /
  agent_workforce). 32 workflows total.
- D4 SSE endpoint live at `/api/v1/workflows/{id}/events`.
- D5 DBOS queue available behind `WORKFORCE_USE_DBOS_QUEUE` env flag.
- D6 Issues page + REST API + project_tasks → issues backfill (175).
- Celery `beat_schedule` emptied (D7 commit). The 11 cron jobs now
  fire only via `@DBOS.scheduled`.
- `migration 176` ready to drop `project_tasks` + `task_assets` —
  guarded by the `unmigrated_project_tasks` coverage view.

What's NOT done yet (intentional, this is the D7 outstanding list):

## 1. Routing-table flip — per task_type

`dbos_workflow_routing` rows still default to `'celery'`. Cutover order
per task_type:

```sql
-- 1. Flip to shadow mode (both fire, DBOS output discarded).
UPDATE public.dbos_workflow_routing SET mode='shadow'
 WHERE task_type IN ('ai_summary', 'thumbnail', 'ai_transcription');

-- 2. Watch logs for ~1 hour. The shadow line in dbos_orchestrator
--    logs both task_ids; compare a sample of outputs.

-- 3. Flip the verified ones to dbos.
UPDATE public.dbos_workflow_routing SET mode='dbos'
 WHERE task_type IN ('ai_summary', 'thumbnail', 'ai_transcription');
```

Recommended cutover order (low → high blast radius):
- `thumbnail` (no user impact, easy rollback)
- `ai_transcription` / `ai_summary` (already validated via PoC #8)
- `script_outline_gen` / `write_memory` (off-chat-path)
- `analyze_l1` / `ai_extract`
- `transcode` (heavy I/O, validate against a real ResourceVersion)
- `parse` + `download` together (chained — flip them in one window)
- `storyboard_*` (8 workflows; flip the lot in one window)

## 2. Frontend KanbanBoard removal

`frontend/components/KanbanBoard.tsx` + `services/projectTasksService.ts`
read `project_tasks`. Migration 176 will drop the table — frontend
must be deployed without these BEFORE the migration runs.

Steps:
1. Remove KanbanBoard from any page that mounts it (grep:
   `KanbanBoard`)
2. Delete `frontend/services/projectTasksService.ts`
3. Delete `frontend/components/KanbanBoard.tsx`
4. Confirm no `project_tasks` references remain:
   ```bash
   rg -l 'project_tasks|projectTasksService|KanbanBoard' frontend/
   ```
5. Build + deploy frontend
6. Run migration 176 (which will fail if any project_tasks row hasn't
   been mirrored — re-run migration 175 if needed)

## 3. Celery worker / beat process removal

After all `dbos_workflow_routing` rows are at `'dbos'`:

a. **docker-compose** — remove the `celery-worker` + `celery-beat`
   services. Keep `redis` (still used by tracker pub/sub for
   real-time progress + DBOS may also use it for caching).

b. **Backend** — leave `app/celery_app.py` import-clean (already done
   in this branch — `beat_schedule={}`). Optionally delete:
   - `app/celery_app.py` itself (will need to find/replace remaining
     `from app.celery_app import celery_app` callers — there are still
     ~20 of these in the legacy task files).
   - `app/tasks/signals.py` — the Celery → unified_task_manager bridge.
     Frontend should be on D4 SSE for any DBOS-routed task; legacy
     tasks no longer fire after step (3a).
   - `app/tasks/scheduled_tasks.py` — task functions only, no schedule
     entries. Drop after confirming no callsite imports them.

c. **Routing table** — keep migration 169 in place (config storage is
   useful for audit even if every row is at 'dbos').

## 4. Final pytest + lint sweep

After (1)–(3):
```bash
uv run black --check app/ && uv run isort --check-only app/ && \
  uv run flake8 app/ && uv run pytest tests/ -q
```

Should still be 873 passed + lint clean.

## 5. E2E validation matrix

Manual smoke before cutting the master PR. Each row should pass on
NAS dev under DBOS routing for ALL task_types:

| Flow | Verifies |
|------|----------|
| Paste Douyin URL → fetch → download → analyze | parse / download / analyze_l1 chain |
| Paste yt-dlp URL → fetch → download → transcode | yt-dlp branch + transcode |
| Open ChatPanel → ask agent → see response | AgentRunner async path |
| Generate storyboard image from a prompt | storyboard_image_workflow |
| Split a script into a storyboard | storyboard_script_split_workflow |
| Export storyboard as PDF | storyboard_export_workflow |
| Create issue → dispatch → watch SSE updates | execute_issue + D4 SSE |
| Trigger memory write → see row in agent_memories | write_memory_workflow |
| Wait 30s → /admin sees system_status update | scheduled_health (every 30s) |
| Daily free points fire (or set DAILY_FREE_POINTS=0) | scheduled_quotas idempotency |

## 6. Rollback plan

Any flip can be rolled back per-task_type by setting
`dbos_workflow_routing.mode='celery'` for the offending row. Celery
worker process needs to be running for the rollback to actually
execute the legacy task. Keep celery-worker in docker-compose for
≥1 week post-flip as the safety net.

---

When all 6 sections are checked off:

```bash
git rebase origin/master
git push -u origin feat/dbos-pr-d2
gh pr create --base master --head feat/dbos-pr-d2 \
  --title "feat(dbos): full Celery → DBOS migration (D1-D7)" \
  --body "$(cat backend/D7_CUTOVER.md)"
```
