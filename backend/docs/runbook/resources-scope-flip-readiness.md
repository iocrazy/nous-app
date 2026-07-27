# Resources Scope Enforcement — Flip Readiness Runbook

> Epic A of the ORM 2.0 migration: make `resources` the first **app-layer
> tenant-scope-enforced** table. This runbook is the pre-flip checklist + the
> production flip/rollback procedure. **Status as of branch
> `feature/resources-scope-activation` (HEAD `a79338a7`): all pre-flip work
> DONE; the flag is still OFF (fully inert). The flip itself is a deliberate
> ops step — not done by this branch.**

## What the flag does

`SCOPE_ENFORCE_RESOURCES` (env, default `false`). When `true`, the app-layer
choke point in `app/db/scope.py` enforces tenant isolation on **ORM** statements
that touch the `resources` table:

- **SELECT** → injects `creator_id == scope.user_id` (deny-by-default: a query
  with no ambient scope raises `UnscopedQueryError`; JOIN/subquery references to
  `resources` are injected too).
- **bulk/Core INSERT/UPDATE/DELETE under a USER scope** → forbidden (must be
  load-then-modify ORM instance ops, which route through the owner-stamp).
- **SYSTEM scope** (set via `system_request_scope`/`system_session`) → bypass
  (no injection, no forbid) for deliberate cross-user/system work.

Off → `resources` behaves exactly as today (byte-for-byte legacy).

## ⚠️ Critical prerequisite: `USE_ORM_RESOURCES` must be ON

The choke point only sees **ORM** statements. Resources access through the
**legacy Supabase REST repo** (`ResourcesRepository`) bypasses it entirely.
`USE_ORM_RESOURCES` defaults **false**, so production currently routes resources
through REST. **Flipping `SCOPE_ENFORCE_RESOURCES` alone, while
`USE_ORM_RESOURCES` is off, does almost nothing** — nearly all resources access
stays on the REST bypass.

**Therefore the real rollout order is:**
1. `USE_ORM_RESOURCES=true` first (with its own shadow/parity validation — this
   is the ORM repo cutover, separate from scope), verify resources behavior is
   unchanged on the ORM path.
2. **Then** `SCOPE_ENFORCE_RESOURCES=true`.

These can be staged on separate deploys, or together with extra verification —
but scope enforcement is meaningful **only** once the ORM repo is live.

## Coverage map (what is protected once both flags are on)

**USER-scoped (ambient `request_scope(Scope(user_id))`):**
- All resources HTTP routers — `ScopedRequestDep` (pass 1).
- Download workflows — `finalize_post_download_step`, `chain_followups_step`,
  soda download ×2 (pass 2/4b).
- `save_metadata_only` (resource create) + `_ensure_carousel_resource` (pass 4b).
- `extract_audio_workflow` / `ai_transcription_workflow` chain calls (pass 4b,
  via copy_context through `run_async` — pass 4a).
- `transcode_workflow` (user path) (pass 5).
- Media repo ORM reads that JOIN resources (`get_user_media_list`, `search`,
  `get_statistics`, `get_pending_downloads`) — injected on the JOIN, verified
  end-to-end (A4).

**SYSTEM-scoped (`system_request_scope(reason)`):**
- Sweepers — `temp_resource_sweeper`, `scheduled_cleanup` ×2 (pass 3).
- `thumbnail_workflow` (no user_id, owner-agnostic derived asset) + transcode
  batch/None path (pass 5).
- `count_resources_by_media_id` — always-global GC refcount (A2.5).

**Repo write shape:** Core insert/update/delete on `resources` converted to
load-then-modify so they don't fail-closed-crash under a USER scope (A2.5).

**Raw SQL:** the 2 genuinely creator-scoped `text()` reads
(`get_owned_platform_ids`, `get_completed_resource_by_url_and_creator`) routed
through the `scoped_sql` predicate builder (A3, security-reviewed).

## Known NOT-enforced when flipped (deliberate, documented)

These bypass the choke point and are **no worse than today** — they are not
regressions, just not-yet-covered:

- **REST-bypass paths** — anything using `ResourcesRepository` over Supabase
  REST: `shares_router`, `search_router`, `media_permissions.check_media_access`,
  and the download-chain helpers' resource reads. Self-controlled by their own
  manual filters / membership checks. Closed by the eventual REST→ORM migration.
- **5 membership/folder-scoped `text()`** on resources —
  `get_resource_items` (team-library listing by `resource_items.scope_id`),
  `_resource_ids_for_platforms`, `get_trashed_resources`,
  `restore_folder_cascade`, `trash_folder_cascade`. These are scoped by
  scope_id / folder membership, **not** by `creator_id`. Forcing creator scope
  would break team libraries and shared-folder restore. Allowlisted in the CI
  guard (`tests/db/test_resources_text_sql_guard.py`); any NEW bare `text()` on
  `resources` outside `scoped_sql` fails CI.
- **`ai_transcription` workflow `text()` reads** (`load_transcribe_inputs`,
  `_run_volcengine_asr`).

## Pre-flip checklist

- [x] All callers wired (passes 1-5) — routers / workflows / sweepers / chain /
      transcode / thumbnail.
- [x] Repo Core DML → load-then-modify; global refcount (A2.5).
- [x] `scoped_sql` primitive + 2 creator reads routed + CI guard (A3,
      adversarial security review = SAFE-TO-PROCEED).
- [x] parsed_media-via-resources ORM JOIN injection verified end-to-end (A4).
- [x] Flag on/off lifecycle regression green; full unit suite green (2859
      passed); flag-OFF parity proven.
- [ ] **`USE_ORM_RESOURCES` rolled out + parity-verified in prod** (the gating
      prerequisite — see above).
- [ ] Branch merged to master + backend deployed.

## Flip procedure (prod)

1. Confirm `USE_ORM_RESOURCES=true` is live + verified (prerequisite).
2. Set `SCOPE_ENFORCE_RESOURCES=true` on the NAS backend env
   (`/volume1/docker/mediahub/docker/.env`, then `stop -t0` / `start` — see
   `reference_nas_backend_env`). This is the persistent bind-mounted `.env`, not
   a compose `environment:` (Watchtower won't apply compose changes).
3. Smoke-verify (a single user account):
   - Download a video → resource is created + finalize step completes (no
     `UnscopedQueryError`).
   - Resource library list / detail / update / delete — own resources only.
   - L2 dedup (re-download) works.
   - Trash → restore → permanent delete; shared-media file GC only fires when
     the **global** refcount hits 0 (cross-user reference protected).
   - Transcode + thumbnail run.
   - **Team-library listing still shows other members' shared resources** (the
     membership/REST path — must NOT have regressed to own-only).
4. Canary `application_logs` for `UnscopedQueryError` for ~30 min:
   ```sql
   SELECT module, message, COUNT(*) FROM application_logs
   WHERE level='ERROR' AND logged_at >= NOW() - INTERVAL '30 minutes'
     AND message ILIKE '%UnscopedQueryError%'
   GROUP BY module, message ORDER BY count DESC;
   ```
   Any hit = a missed caller → roll back, wire it, retry.

## Rollback

Set `SCOPE_ENFORCE_RESOURCES=false` + restart. Instant and complete — the choke
point short-circuits to legacy with the flag off. No data migration to undo.

## Post-flip cleanup (≈2 weeks after a stable flip)

- Remove the `SCOPE_ENFORCE_RESOURCES` flag + the enforced-set gate.
- Remove now-redundant **manual `WHERE creator_id`** filters / explicit
  `creator_id` args from the ORM repo (`get_resource_by_media_id_and_creator`,
  `find_by_hash`, the 2 scoped_sql reads' superseded args). **Do NOT do this
  pre-flip** — those filters are what scope `resources` while the flag is off;
  removing them early breaks flag-off parity.

## Deferred follow-ups (tracked, not flip-blockers)

- **Orphan-file accounting:** on a transient count failure during
  `permanent_delete`, the resource row is deleted but the shared-file/parsed_media
  GC is safely skipped — and **no scheduled sweeper reclaims** the
  `global/resources/.../{media_id}` download tree or the parsed_media row (the
  orphan sweeper only handles `uploads/{resource_id}`). Files leak until a later
  successful `permanent_delete` or manual cleanup. Accepted (leaking on a rare
  error beats deleting files another user references); revisit if file-leak
  accounting matters.
- **REST→ORM migration** of the bypass paths (shares/search/permissions/chain
  helpers) — the real long-term closure of the deferred coverage above.
