# Small-Image Object Storage Migration (Supabase Storage) — Plan

**Date:** 2026-07-05 · **Status:** ✅ LIVE (Phase 1 shipped + Phase 2 go-live done 2026-07-05)
**Decision context:** user discussion 2026-07-05 — workload-split architecture approved in principle.

## Final deployed state (2026-07-05)

- Prod flag ON (`FEATURE_CHAT_MEDIA_OBJECT_STORE=true`, host .env). Rollback = remove flag + stop-t0/start.
- storage-api: **file backend**, both stacks. Disk location moved off volume1
  (7TB SSD) to **`/volume2/sources/MediaHub.library/object-storage`** (56TB).
- Tenant naming de-stubbed: `STORAGE_TENANT_ID=mediahub`, `GLOBAL_S3_BUCKET: media`
  → final object path `…/object-storage/media/mediahub/chat-media/t{scope}/{sha[:2]}/{sha[2:4]}/{sha}.ext`.
- `FILE_SIZE_LIMIT` raised 50MB → 5GB (both stacks, user request).
- **Ops iron law learned:** touching ONE service in the drifted supabase stack
  requires `docker compose up -d --no-deps <svc>` — a bare `compose up` cascaded
  db/rest/realtime and took PostgREST down ~7min (502s). Also: recreating a
  long-untouched container can expose missing env (storage needed
  `PGRST_JWT_SECRET` added to compose).
- **Post-go-live review catch (v0.25.121):** vision signed URLs were built on the
  LAN `SUPABASE_URL` — unreachable by cloud providers, silently breaking vision
  for sb:// images. Vision now defaults to base64 bytes for object-store images;
  signed URLs are opt-in via `STORAGE_SIGNED_URL_PUBLIC_BASE` (set only to a
  genuinely public base; the public kong entry does not currently route
  /storage/v1, so leave empty until that's configured).
- Known non-blocking leftovers: imgproxy compose line still mounts the old
  volume1 path (unused; sync if imgproxy transforms are ever enabled); dev stack
  drift (missing `generated_media` table, mig 307+ never applied to dev);
  chat-message soft-delete does not delete its generated_media row/object
  (pre-existing, true for filesystem too).

## Problem & Decision

Two storage workloads live on the NAS filesystem today with opposite access profiles:

| Workload | Profile | Filesystem fit |
|---|---|---|
| Video originals, HLS segments, download pipeline | large, sequential, local ffmpeg/yt-dlp writes | ✅ ideal (nginx sendfile/Range) |
| Chat images, AI generations, (later) avatars | small, numerous, random point-GETs | ❌ classic small-file problem |

Small files pay fixed inode + directory-entry cost per file, make rsync/snapshot
backup an order of magnitude slower per byte, and get nothing from sendfile.
Date-bucketing (v0.25.113, #1029) mitigates directory accumulation but not inode
growth or backup pain. Object storage is the industry-standard home for this
profile and unlocks: signed URLs, bucket-level RLS policies, imgproxy dynamic
transforms, and content-hash dedup.

**Decision:** split by workload. Video/download pipeline stays on filesystem +
nginx FOREVER (that part is not up for debate — it's the correct engine).
Small images move to Supabase Storage, new-writes-only, dual-track.

## Non-goals

- NO migration of existing files (old rows keep `file_path` semantics).
- NO change to video/HLS/download-pipeline storage.
- NO cloud OSS/CDN in this phase (self-hosted Supabase Storage on the NAS;
  the API surface is S3-shaped, so a later cloud/MinIO move is a config change).
- NO removal of pre-generated thumbnails yet (imgproxy replacement is Phase 3).

## Prerequisites (verify BEFORE Phase 1 — ops checklist)

1. **storage-api container runs in both NAS stacks** (`mediahub-sb-prod`,
   `mediahub-sb-dev`). The self-hosted Supabase compose ships it, but our
   stacks were brought up service-by-service (see NAS reboot runbook) —
   verify it exists, is healthy, and survives the reboot runbook order.
2. **imgproxy container** present if Phase 3 (transforms) is wanted; not
   required for Phase 1/2.
3. Storage backend config: file-backend rooted on the NAS volume (default) is
   fine — the win here is API/auth/key-space, not durability.
4. ⚠️ Any compose change requires manual `docker compose up -d` on the NAS
   (Watchtower does not read compose — standing lesson from #172).
5. Buckets + policies are SQL (`storage.buckets` / `storage.objects` RLS) →
   ship as a normal numbered migration through CI.

## Design

### Bucket & key scheme (content-addressed, 大厂-style)

- Bucket: `chat-media` (private).
- Key: `t{scope_id}/{sha256[:2]}/{sha256[2:4]}/{sha256}.{ext}`
  - Content hash = dedup for free: same image uploaded twice → same key;
    the second upload is a metadata-only insert (skip the PUT on 409/exists).
  - Hash prefix fan-out replaces date buckets in the flat key space.
  - Original filename is NEVER in the key (privacy/encoding/collision) — it
    stays in `generated_media` metadata and returns via `Content-Disposition`.

### Location column (dual-track truth)

`generated_media.file_path` stays the single source of location truth, with a
scheme prefix for new writes:

- legacy rows: `teams/42/chat/2026/07/05/{uuid}/shot.png` (filesystem, as today)
- new rows: `sb://chat-media/t42/ab/cd/{sha256}.png`

One helper `resolve_media_source(row) -> LocalFile | StorageObject` becomes the
ONLY place that interprets the column. Every reader goes through it (grep-able,
testable, and the rollback point).

### Write path (2 functions, 1 choke point)

`register_uploaded_media` / `register_generated_media` gain a
`storage_backend` switch (env flag `FEATURE_CHAT_MEDIA_OBJECT_STORE`, default
false, standard 2-week flag lifecycle):

1. sha256 the bytes → key.
2. HEAD/exists check → skip PUT if present (dedup).
3. PUT via storage-api (service key, server-side only).
4. INSERT `generated_media` row with `sb://` path + `content_sha256` column
   (new nullable column, one migration; useful for dedup + later integrity audit).

Failure mode: storage PUT fails → fall back to filesystem write + legacy path
(log loudly). Chat upload must never hard-fail because storage-api is down.

### Reader matrix (complete inventory, all go through the resolver)

| Reader | Today | After |
|---|---|---|
| `generated_media_router` GET `/{id}/cover`, `/{id}/file` | `FileResponse(DOWNLOAD_PATH/file_path)` | legacy → unchanged; `sb://` → 302 redirect to a short-TTL **signed URL** (or stream-proxy if we want to hide the storage host; start with redirect — cheaper) |
| `promote_generated_media_service` | `shutil.copy2` local→local | legacy → unchanged; `sb://` → GET bytes from storage → write into resources store (resources stay filesystem this phase) |
| `conversation_agent_turn._inject_image_blocks` (agent vision) | local `read_bytes` → base64 data URL | legacy → unchanged; `sb://` → **signed URL passed straight to the provider** (doubao fetches it; kills the +33% base64 token tax). Fallback to bytes+base64 if provider can't fetch |
| Frontend `image_url` in message body | API URL to `/file` endpoint | unchanged (endpoint redirects) — no frontend change in Phase 1 |
| `main.py` `/media/{path}` legacy route | parsed-media only | untouched (not generated_media) |

### RLS / permissions

Phase 1 keeps the existing model: all reads go through our FastAPI endpoints
(membership-checked), storage bucket is private, only the backend's service key
touches it. Bucket RLS policies for direct-from-browser access are **Phase 2**
(needs the storage JWT wired into the frontend Supabase client + policies that
join `conversation_members` — design carefully against the RLS lessons in
`bug_rls_log_tables_world_readable`).

## Phases

**Phase 1 — dual-track writes + resolver readers** (1 PR backend, flag-dark)
- Migration: `content_sha256` column; bucket creation SQL.
- `resolve_media_source` helper + reader matrix ports + write-path switch.
- Tests: resolver (legacy/sb/malformed), dedup skip-PUT, fallback-on-storage-down,
  vision signed-URL path, promote-from-storage. Un-mocked smoke against dev
  stack storage-api before flag-on (standing lesson: mocked tests miss live
  protocol breaks, #918).

**Phase 1 — DONE (2026-07-05)**
- 1a foundation (#1036, v0.25.115): mig 337 (`chat-media` bucket +
  `content_sha256` col), `media_storage.py` (`resolve_media_source` /
  `content_key` / `ObjectStore`), flag. Inert.
- 1b write path + readers (#1037, v0.25.116): `register_uploaded_media`
  object-store branch (image-only, dedup, filesystem fallback); all four
  readers ported (cover/file stream-proxy, promote, agent-vision signed-URL).
  Flag still default-off → behavior-neutral.

**Phase 2 — flag-on + ops runbook** (no code; gated on NAS access)

Findings from the 2026-07-05 recon (settle the prerequisites):
- Supabase Storage was used before: mig 052 provisioned a public `thumbnails`
  bucket. It's now DORMANT — local dev has 1 bucket / 0 objects; the thumbnail
  workload moved to filesystem+nginx. So storage-api existed at some point but
  may have been removed since.
- The NAS `MediaHub.library` (SMB-mounted `/volume2/sources/…`) has an empty
  `s3/` subdir created 2026-07-05 — strongly suggests the NAS Supabase Storage
  was recently pointed here, but nothing has landed yet. Empty dir can't tell
  file-backend from s3-backend.
- Repo has zero storage-api / STORAGE_BACKEND config — it lives in the NAS
  supabase stack compose (Portainer), NOT git. `deploy/nas` + `docker/` only
  manage redis + backend.

Steps:
1. On the NAS, settle backend + health in one shot:
   ```bash
   sudo docker ps | grep -i storage        # container alive?
   sudo docker inspect $(sudo docker ps -qf name=storage) \
     | grep -iE "STORAGE_BACKEND|STORAGE_FILE_BACKEND_PATH|GLOBAL_S3|S3_ENDPOINT"
   ```
   - `STORAGE_BACKEND=s3` + `GLOBAL_S3_*` → S3 backend (MinIO; data root is the
     `s3/` dir). `STORAGE_BACKEND=file` + `STORAGE_FILE_BACKEND_PATH=…/s3` →
     file backend. Either is transparent to our code (we PUT/GET by key).
   - nothing / no container → storage-api not deployed; deploy it first (add to
     the NAS supabase compose, then `docker compose up -d` — Watchtower does
     NOT read compose, standing lesson #172). If it lands on `MediaHub.library`,
     give it a DEDICATED subdir it owns exclusively (opaque internal layout —
     don't mix with the existing `teams/` tree). macOS local dev: use a named
     Docker volume, NOT a bind mount to `/Volumes/...` (Supabase docs: bind
     mounts lack xattr/permissions and break storage).
2. Confirm mig 337 applied on prod (`SELECT id FROM storage.buckets WHERE
   id='chat-media'`).
3. Un-mocked smoke on the DEV stack first. Two layers:
   a. Protocol smoke (no flag flip, no DB) — proves the live storage-api
      round-trip (put/get/signed-url/put_file/remove):
      ```bash
      RUN_STORAGE_SMOKE=1 uv run pytest \
        tests/test_storage_object_store_smoke.py -v
      ```
      (skipped by default; needs the backend Supabase env + `chat-media`
      bucket from mig 337.)
   b. Feature smoke — set `FEATURE_CHAT_MEDIA_OBJECT_STORE=true` in the dev
      backend .env → `stop -t0`/`start` (standing lesson: .env needs a real
      restart), then upload a chat image AND generate a short video →
      confirm `sb://` rows + real objects in the bucket → @agent vision
      describes the image → promote → serves via `/cover` / `/file`.
4. Flip prod: host `.env` `FEATURE_CHAT_MEDIA_OBJECT_STORE=true` +
   `stop -t0`/`start`. Canary: upload → render → @agent vision → promote →
   error-funnel query (`application_logs` ERROR since flip).
- Rollback = flag off (new rows already written to storage keep working via
  resolver; only NEW writes revert to filesystem).

**Phase 3 — optional follow-ups** (separate decisions, not commitments)
- imgproxy transforms → retire pre-generated thumbnails for chat images.
- Browser-direct signed upload (cuts the FastAPI hop on upload).
- Avatars + other small-file families onto the same bucket pattern.
- MinIO/cloud OSS behind the same S3 surface if/when multi-node arrives.

## Risks

| Risk | Mitigation |
|---|---|
| storage-api not actually deployed/healthy on NAS stacks | Prerequisite check FIRST; the whole plan gates on it |
| storage-api down at upload time | filesystem fallback in write path, loud log |
| Signed URL leaks (sent to LLM provider) | short TTL (60–300s), image-only bucket, no listing |
| Double-write complexity creep | `resolve_media_source` is the ONLY interpreter; no scattered `startswith("sb://")` |
| Same-disk durability illusion | documented: this phase buys API/key-space/dedup, NOT durability |

## Effort

Phase 1 ≈ one focused day (backend-only, ~5 files + tests). Phase 2 ≈ an ops
hour on the NAS. Phase 3 items are independently schedulable.
