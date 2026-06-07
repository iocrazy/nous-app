# Unified Agent Media Context + Asset Lifecycle — Design

**Date:** 2026-05-25
**Status:** Design (brainstormed interactively) → pending user review → implementation plan
**Scope:** A unified media-asset layer so any media (user-uploaded, downloaded, or AI-generated) is a **resource** on NAS, addressable by the chat agent (transcript + multimodal), with a temp→permanent lifecycle. **Separate from** the "New Issue dialog" UI epic (#1 status / #4 assignee / #5 project / #6 Live badge / #3 dates) — those are independent UI work.

---

## Problem / vision

The user wants:
1. Paste/drag image upload in the **issue description** and **chat reply**.
2. The agent to **return generated images/videos** into the chat.
3. To **`@`-reference existing resources** (uploaded or downloaded) in chat so the agent can read their **transcript** (already in `videos`) or **multimodally read the video** itself.
4. Chat-uploaded images to be **temporary**, viewable + promotable to permanent storage.

The insight that drives the architecture: the media the agent should reference (transcripts, video bytes) **already lives in the `resources` → `parsed_media` → `videos` system on NAS**, behind the media-token access control we just shipped (#276/#275). Splitting new uploads into a separate store (e.g. Supabase Storage) would **fragment** agent media access (no transcript pipeline, two access paths, two auth models). So: **everything is a `resource` on NAS; one access model.**

## Storage decision (locked)

**NAS / the existing `resources` library — NOT Supabase Storage.** Rationale (decided after weighing both):
- The agent-referenceable media (transcript via `videos`, multimodal via media bytes) is already in `resources`/`videos` on NAS.
- Reuse the media-token access control (#276/#275) → one auth model for all media (uploaded / downloaded / generated).
- Self-hosted Supabase Storage is *also* on the NAS (file-backed) → no capacity advantage, but adds hops (storage container + Kong + `storage.objects` metadata), inherits the self-hosted Supabase reliability wobble, is worse for large-video streaming than the existing nginx/HLS + `/media` path, and couples assets to Supabase (portability cost vs the planned migrations).
- Trade-off accepted: upload + access-control plumbing is hand-rolled (but the media-token machinery already exists to reuse).

**Storage layout** (confirmed against the live NAS `MediaHub.library`):
```
MediaHub.library/
  global/resources/web/...            (existing)
  teams/{teamid}/uploads/...          (permanent user uploads — existing)
  teams/{teamid}/temp/...             (NEW — chat/issue temp uploads)
```
`{teamid}` is a real team's bigint snowflake **or** a user's UUID for their **personal team** (the live layout already shows both UUID and bigint dirs under `teams/`; the UUID dirs are user ids — e.g. `8e1584e3-…` is MH-1's `created_by_user_id`). So personal scope needs no special case — it routes under `teams/{user_personal_teamid}/`. (Implementation: confirm the exact personal-team resolution — `scope_type='user'`/`scope_id` vs a real personal-team row — when planning.)

---

## Components

### C1 — Temp uploads as resources (UPGRADE the existing endpoint, D2)
- **Reuse, don't rebuild:** `/api/v1/ai-library/chat-attachments/upload` already exists (`ai_library_router.py:2090+`) — it does per-user temp upload with magic-byte anti-spoofing, a 50MB cap, image/video/pdf allow-list, and is already consumed by `run_session_turn(attachments=…)`. Today it writes to ephemeral `/tmp/mediahub_chat_attachments` with a hardcoded 24h reap.
- **Upgrade it (D2):** redirect its writes to a **temp resource** at `teams/{teamid}/temp/` (reuse the validation + the turn-consumption wiring untouched). Returns the resource id + media-token URL.
- Temp flag: `is_temp` boolean **or** a reserved `temp` folder under the team's uploads (decide in plan).
- Issue-description paste/drag uses the same endpoint/flow.

### C2 — Temp lifecycle (upload → promote / sweep)
- **Promote:** a "move to Uploads" action turns a temp resource permanent (clear `is_temp` / move out of the temp folder).
- **Sweeper TTL is user-configurable in Settings:** `7 days | 30 days | never`. A DBOS `@scheduled` sweeper deletes temp resources older than the configured TTL (skips when `never`). Default: TBD (suggest 30 days). The setting lives in `user_settings` (per-user; or per-team — decide in plan).
- Deletion = soft-delete (`is_trashed`) → Recycle Bin, consistent with existing resource delete.

### C3 — Resources UI: Temp view
- Add a **"Temp"** entry under **"My Uploads"** in the Resources sidebar (matches the screenshot: Shared / Recycle Bin / My Downloads / My Uploads / Smart Folders).
- Lists temp resources for the active scope; each has a **"Promote / Save to Uploads"** action + the normal delete.

### C4 — `@`-reference a resource in chat
- In the chat composer, `@` opens a resource picker (search the user's/team's resources — uploads, downloads, temp).
- Selecting a resource attaches it to the turn. The agent then receives, per resource type:
  - **image** → multimodal (works today via `app/agent_framework/multimodal.py` + `run_session_turn(attachments=…)`).
  - **video → text only (D3, 2026-05-25):** the `@`-picker lets the user choose **`.transcript`** (from `parsed_media.ai_extract_text`) **or `.summary`** (if available), injected as text context. **Native video / keyframe multimodal is explicitly DEFERRED** ("先跑起来") — current `multimodal.py` only treats video as a single thumbnail frame (`VIDEO_THUMBNAIL`), so real video understanding needs ffmpeg keyframe extraction or a video-native model — a later addition, not v1.
  - Guardrail: transcripts can be 10k+ chars (mig 154) → cap/truncate injected text to a token budget.

### C5 — AI-generated media → resources
- When the agent generates an image/video, store it as a **resource** (temp scope by default, under `teams/{teamid}/temp/`) and return its media-token URL in the assistant message → renders inline in chat. Promotable like any temp resource.

---

## What's reused vs new

**Reused:** `resources` table + `/resources/upload` + folders/scope, `parsed_media`/`videos` (transcripts), media-token access control (#276/#275), `AgentRunner` multimodal (`build_user_message`), the Resources sidebar/views, Recycle Bin soft-delete, DBOS `@scheduled` for the sweeper.

**New:** temp scope/flag + `teams/{teamid}/temp/` path; paste/drag upload handlers (chat + issue); the Temp sidebar view + Promote action; the user-configurable temp-TTL setting + sweeper; the chat `@`-resource picker + the wiring that injects transcript/multimodal into the turn; AI-generated-media → resource write-back.

---

## Decisions locked
- Storage = **NAS/resources unified** (not Supabase Storage); reuse media-token.
- Temp path = `teams/{teamid}/temp/`; personal scope routes under the user's personal teamid (no special case).
- Temp TTL = **user-configurable in Settings (7d / 30d / never)** via a sweeper.
- Promote temp → permanent Uploads.
- AI-generated media stored as resources.
- `@`-reference gives the agent transcript and/or multimodal access.

## Open items (resolve in the implementation plan)
- Exact personal-team resolution (scope_type='user' vs a personal-team row) + how `{teamid}` is chosen for a chat/issue with no explicit team.
- transcript-default vs multimodal-on-demand policy + the cost guardrail for multimodal video.
- temp flag mechanism: `is_temp` column vs a reserved `temp` folder (or both).
- TTL setting scope (per-user vs per-team) + default value.
- Whether issue-description images and chat-reply images share the exact same temp path/flow (assume yes).

## Non-goals (this spec)
- The "New Issue dialog" UI epic (status dropdown / assignee / project / Live badge / scheduled dates) — separate.
- Migrating existing NAS media off the download pipeline — unchanged.
- Supabase Storage — explicitly rejected here.

## Decomposition (suggested sub-plans)
1. **Asset foundation**: temp resources + `/resources/upload` extension + temp path/flag + media-token URLs. (backend)
2. **Temp lifecycle**: Settings TTL + DBOS sweeper + Promote action + Temp sidebar view. (backend + frontend)
3. **Upload UX**: paste/drag in chat reply + issue description. (frontend)
4. **`@`-reference + agent media context**: chat resource picker + inject transcript/multimodal into the turn. (frontend + backend) ← the highest-value, most novel piece.
5. **AI-generated media → resource**: write-back + inline render. (backend + frontend)

---

## Eng review (gstack /plan-eng-review, 2026-05-25)

**Verdict: feasible, scope expanded by user (full 5 sub-plans), with the spec corrected to reuse existing infra and to scope video to text-only-for-v1.**

### Decisions made during review
- **D1 — scope:** full 5-sub-plan epic (user declined the reduce-to-core option). Implement as separate sub-plans, but all are in.
- **D2 — uploads:** **upgrade** the existing `/chat-attachments/upload` (`ai_library_router.py:2090+`) from `/tmp`-ephemeral to resources-backed `teams/{teamid}/temp/`. Reuse its magic-byte/cap/type validation + the `run_session_turn(attachments=…)` wiring. Add promote + configurable TTL + Temp sidebar on top. One path, DRY.
- **D3 — video in chat:** `@video.transcript` OR `@video.summary` (user-chosen, if available) as **text** context. Native video / keyframe multimodal **deferred** to a later addition ("先跑起来"). Image multimodal already works.

### What already exists (reuse, do NOT rebuild)
- **Chat upload + temp + reap + turn-consumption:** `/chat-attachments/upload` (validation, 50MB cap, image/video/pdf, 24h reap) + `run_session_turn(attachments=…)`. → sub-plans 1/3 + image-multimodal of 4 are ~80% done; the new work is making them resources-backed + promotable.
- **Image multimodal:** `app/agent_framework/multimodal.py` (`image_url` + base64). Video = thumbnail-only today.
- **Transcript text:** `parsed_media.ai_extract_text` (mig 154) + `video_transcripts` table. Summary: `videos`/`resources` summary fields.
- **Scope model:** resources `scope_type`/`scope_id` (`resources_repository.py`) + `ai_sessions.team_id`/`context_type`/`context_id` (mig 138). → temp scope = session's team, or user scope when `team_id` null. **No "personal team" row needed** — the `teams/{user-uuid}/` dirs are a storage convention over user-scoped resources.
- **Soft-delete → Recycle Bin**, **DBOS `@scheduled`** (for the sweeper), **media-token access** (#276/#275).

### Resolved open items (were unknowns in the draft)
- Transcript location → `parsed_media.ai_extract_text` ✓
- Scope resolution → `scope_type/scope_id` + `ai_sessions.team_id`; personal = user scope ✓
- Video multimodal → text-only (transcript/summary) for v1; native video deferred ✓

### Still to decide in the implementation plan
- `is_temp` column vs reserved `temp` folder (lean: a `temp` folder + scope, fewer schema changes).
- TTL setting scope (per-user vs per-team) + default (suggest 30d). Lives in `user_settings`.
- The `@`-picker UX for choosing `.transcript` vs `.summary` on a video.

### Tests the plan must include (gstack test gate)
- **Regression (CRITICAL):** chat image attachments still upload + reach the turn after the `/tmp`→resources-temp upgrade. The existing path must not break.
- Upload → temp resource created at correct scope/path; magic-byte rejection still fires.
- Promote: temp → permanent (flag/folder change), shows in My Uploads, leaves Temp.
- Sweeper: temp older than TTL is soft-deleted; `never` setting skips; non-temp untouched.
- `@`-ref: video → transcript text injected; video → summary injected; missing transcript/summary handled; image → multimodal; transcript truncated to token budget.
- Scope: team session → `teams/{team_id}/temp`; no-team → user scope.

### Performance / failure modes
- **Transcript injection size:** 10k+ char transcripts → token blowup + cost. Guardrail: truncate to a budget (e.g. first N tokens) + note truncation. (perf, confidence 8/10)
- **Sweeper query:** index temp resources by `(scope, is_temp, created_at)` so the `@scheduled` sweep is not a full scan as temp grows. (perf)
- **Upgrade failure mode:** if the resources-temp write fails, the chat upload must fail gracefully (don't leave the turn half-attached) — preserve the existing endpoint's error handling.

### NOT in scope (deferred, explicit)
- Native video / keyframe multimodal (D3 — later).
- AI-generated **video** generation (only image/video *storage* as resources is in; generation pipelines are separate).
- Supabase Storage (rejected — NAS/resources unified).
- The "New Issue dialog" 6-feature UI epic (status/assignee/project/Live badge/dates) — separate.

### Suggested build order (sequencing the 5 sub-plans)
1. **Asset foundation + upgrade chat-attachments → resources-temp** (D2) — unblocks everything, reuses most existing code.
2. **Temp lifecycle** (TTL setting + sweeper + promote + Temp sidebar).
3. **Upload UX** (paste/drag in chat + issue description — mostly already there via the upgraded endpoint).
4. **`@`-reference + agent text context** (transcript/summary) — the novel high-value piece.
5. **AI-generated media → resource** writeback.
