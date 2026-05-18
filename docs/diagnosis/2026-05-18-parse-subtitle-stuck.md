# parse_workflow failed but subtitle stuck on "Initializing..." — diagnosis + fix

Date: 2026-05-18
Investigation task: #22 (from Batch 1 manual QA discovery)

## Symptom

Failed parse tasks render in the Task Center with:
- `phase=failed` ✓ (correct — lifecycle trigger fired)
- `subtitle="Initializing..."` ✗ (frozen at task-creation default)
- `error_msg="Workflow failed — open detail to see the exception."` ✗ (generic placeholder)

User sees a failed task with no useful information about what went wrong or where it got stuck.

## Scope

```sql
SELECT
  COUNT(*) FILTER (WHERE phase='failed' AND subtitle='Initializing...') AS stuck_init_failed,
  COUNT(*) FILTER (WHERE phase='failed' AND error_msg LIKE 'Workflow failed%') AS generic_errmsg_failed,
  COUNT(*) FILTER (WHERE phase='failed') AS all_failed
FROM task_tracking
WHERE task_type='parse' AND created_at >= NOW() - INTERVAL '14 days';
```

Result (run 2026-05-18): 3/3 failed parses match BOTH conditions. 100% of failed parses present this way.

## Root cause (two-part)

### Part 1 — subtitle stuck at "Initializing..."

`parse_workflow` (backend/app/workflows/parse.py:301-450) has 7 sequential steps:

| # | Step | Subtitle after |
|---|------|----------------|
| 0 | `mark_parse_processing_step` | (unchanged — "Initializing...") |
| 1 | `extract_url_step` | (unchanged) |
| 2 | `fetch_and_parse_step` (DrissionPage / IES heavy I/O) | (unchanged) |
| 3 | `save_media_step` | (unchanged) |
| 3a | `attach_tags_step` | (unchanged) |
| 3b | `update_parse_tracking_step` | **updated to video title** |
| 4–7 | auto_tag, dispatch_download, log | (no subtitle updates) |

Subtitle is only updated at step **3b**. Failures at steps 0-3 (which is where the actual production failures happen — fetch+save are the heaviest, riskiest steps) leave subtitle at the `manager.create()` default of `"Initializing..."`.

The 3 failures in the funnel all raised at step 2 (`fetch_and_parse_step`) with NO_ROUTER_DATA or DrissionPage timeout exceptions — never reached 3b, subtitle never advanced.

### Part 2 — error_msg collapsed to placeholder

The `mirror_dbos_lifecycle_to_tracking` trigger calls `dbos_error_to_text(err)`:

```sql
-- supabase/migrations/180_task_tracking_rename.sql:174-192
CREATE FUNCTION public.dbos_error_to_text(err TEXT) RETURNS TEXT AS $$
BEGIN
  ...
  -- Pickle protocol 4/5 start with 0x80 0x04 / 0x80 0x05 → base64 'gAS'/'gAU'/'gAQ'/'gAV'
  IF length(err) > 80
     AND substring(err, 1, 3) = ANY (ARRAY['gAS', 'gAU', 'gAQ', 'gAV'])
  THEN
    RETURN 'Workflow failed — open detail to see the exception.';
  END IF;
  RETURN substring(err, 1, 500);
END;
$$;
```

DBOS persists `dbos.workflow_status.error` as **base64-encoded pickled Python exception**. SQL can't unpickle Python objects, so the function returns the generic placeholder for every workflow ERROR. Detailed exception is only available via `/api/v1/workflows/{id}/status` which uses Python pickle to deserialize.

Per CLAUDE.md 路线 C 第 2 条, `error_msg` is **trigger-managed** — business code must NOT PATCH it. So fixing error_msg requires either:
- (a) Improving the trigger (still bounded by SQL's inability to unpickle Python objects)
- (b) Adding a separate business-side error field (e.g. `failure_reason` in `metadata` jsonb)

Both are bigger scope than this PR; deferred.

## Fix (this PR)

Add a generic `update_parse_subtitle_step(workflow_id, subtitle)` DBOS step that calls `manager.update_progress(subtitle=...)` — touches only the business-decorated `subtitle` field, never `phase / status / progress / error_msg` (which are trigger-managed per route C §2).

Insert subtitle progression at strategic points:

```python
mark_parse_processing_step(DBOS.workflow_id)
update_parse_subtitle_step(DBOS.workflow_id, "Validating URL...")  # NEW

valid_url = extract_url_step(url)
update_parse_subtitle_step(DBOS.workflow_id, "Fetching metadata...")  # NEW

fetched = fetch_and_parse_step(...)
update_parse_subtitle_step(DBOS.workflow_id, "Saving metadata...")  # NEW

saved_video = save_media_step(...)
# step 3b already exists — updates subtitle to video_title
update_parse_tracking_step(workflow_id=..., subtitle=video_title[:120])
```

Now a raise mid-flight leaves subtitle reflecting the last completed checkpoint:
- Raise at URL validation → subtitle="Validating URL..."
- Raise at fetch+parse (the common case for the 3 in-funnel failures) → subtitle="Fetching metadata..."
- Raise at save → subtitle="Saving metadata..."

`update_parse_subtitle_step` is a `@DBOS.step` (so DBOS replay sees it as a checkpoint) but its inner failure is swallowed — a transient Supabase hiccup writing the cosmetic subtitle field must never poison a real workflow run.

## Out of scope (follow-ups)

- **Improve error_msg surfacing**: SQL can't unpickle. Options for a follow-up PR:
  - Have the workflow `try / except / record business_error_msg / raise` (then error_msg comes from `metadata.business_error_msg` instead of trigger). Crosses route C §2 boundary but reasonable if metadata-only.
  - Have DBOS write the exception's `repr` to a side column at raise time so trigger can read it without unpickling.
- **Cascading improvements to other workflows**: `download_workflow`, `transcode_workflow`, `extract_audio_workflow`, `ai_transcription_workflow`, `ai_summary_workflow`, `analyze_l1_workflow` all likely have the same pattern. Audit + same fix as a separate PR if any of them surface stuck-subtitle on failure.

## Verification plan

After deploy:

```sql
-- Failed parses should now have informative subtitles
SELECT subtitle, COUNT(*) FROM task_tracking
WHERE task_type='parse' AND phase='failed' AND completed_at >= '<deploy_ts>'
GROUP BY subtitle ORDER BY 2 DESC;
```

Expected (after the next handful of real-world failures): subtitles distributed across "Validating URL...", "Fetching metadata...", "Saving metadata..." — not all on "Initializing...".

If "Initializing..." still appears, the workflow is failing BEFORE `mark_parse_processing_step` even runs (e.g. DBOS init bug, import error). Different root cause, different fix.
