# Team Chat — Send Images (independent store + opt-in save-to-library) Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development. Steps use `- [ ]`.
> **REVISED (2026-06-30):** chat images are an INDEPENDENT store (not auto-added to the resource library). User can opt-in "save to library" (team OR personal) which COPIES the file. Mirrors the `generated_media` (Tier-1 store) + promote-to-resources pattern.

**Goal:** Send images in Team Chat (Discord/Feishu-style: click + paste + drag-drop, inline render, click-to-expand). Uploaded chat images live in an **independent chat store** (`teams/{team}/chat/...` on disk + a `chat_attachments` table), referenced by the message — NOT in the resource library. A per-image **"Save to library"** action copies the file into the **team OR personal** resource library (opt-in import).

**Architecture (verified):** Backend mirrors the proven `generated_media` trio. New `chat_attachments` table (clone of `generated_media`: id/scope_id/channel_id/creator/file_path/mime/size/promoted_resource_id, service-role RLS). `POST /chat/channels/{cid}/attachments` writes the file to `{DOWNLOAD_PATH}/teams/{team}/chat/{uuid}/{name}` + inserts a row. `GET /chat/attachments/{id}/file` serves it **world-readable-by-id, no auth** (FileResponse + realpath-under-DOWNLOAD_PATH guard + immutable cache — exactly like `get_generation_cover`). The chat message is a `media_card` with body `{kind:'image', attachment_id, image_url:'/api/v1/chat/attachments/{id}/file', alt}` (reuse `media_card` — no content_type migration). `MessageBubble` renders `kind:'image'` inline + click → `ImageLightbox`. `POST /chat/attachments/{id}/promote` body `{scope_id}` copies the file into resources under the chosen scope (team or personal) — a clone of `PromoteGeneratedMediaService`. Frontend "Save to library" offers team vs personal (personal = `personalTeamId` from `useTeamContext`).

**Tech Stack:** FastAPI + db_engine (service-role, BYPASSRLS) + `shutil`/`DOWNLOAD_PATH`; React 19 + existing composer affordances (T1 done) + RTL.

## Global Constraints
- **Branch:** `feature/chat-image-upload` (T1 already committed: composer click/paste/drop → `onAttachFiles`).
- **Independent store — NOT the resource library.** Chat images write to `teams/{team_id}/chat/{uuid}/{filename}` under `DOWNLOAD_PATH` and are tracked in `chat_attachments`. They do NOT appear in the resource library unless the user explicitly "Saves to library".
- **Clone the generated_media pattern** (don't reinvent): table mirrors `supabase/migrations/307_generated_media.sql`; repo/service mirror `generated_media_service.py`; serve route mirrors `get_generation_cover` (`generated_media_router.py:49`, realpath guard + immutable cache, NO auth); promote mirrors `promote_generated_media_service.py` (4-step: resource → `shutil.copy2` → version → resource_item; idempotent via `promoted_resource_id`).
- **Reuse `media_card` content_type** (body free-form): image messages set `body.kind='image'`. No new content_type, no DB CHECK / Pydantic-Literal change.
- **Membership gates:** upload requires the caller be a member of the channel (reuse `ChatService`/`is_member`); scope_id for the attachment = the channel's `team_id`. Promote requires the caller can access the attachment (channel member) AND can write to the target `scope_id` (member of that team, or it's their own personal team).
- **Serve security:** world-readable-by-id (unguessable snowflake) + `os.path.realpath` under `DOWNLOAD_PATH` traversal guard (copy from `generated_media_router.py:62-65`). Same posture as the existing cover routes.
- **Migration number = 326** (max is 325). Service-role-only RLS on `chat_attachments`.
- **Image-only this slice:** non-image files rejected with a toast. Limits via `ChatAttachmentPicker.helpers` (≤4 files, ≤50MB).
- **Agent images:** out of scope to GENERATE here; the principle "agent chat images use the chat store" is satisfied because any image posted to a channel goes through `chat_attachments`. Standalone agent generation (`generate_media_tools` → `generated_media`) is unchanged.
- **Island UI**, zero emoji, no `zinc-*`; i18n en+zh parity. Backend lint black/isort/flake8; frontend tsc + build + vitest.
- **Verification:** unit/build per task + a **real logged-in browser pass** (paste/drop/pick → inline → expand → save-to-library team+personal) per `feedback_ui_early_visual_ux_pass`; clean up test data after.

## File Structure
- `supabase/migrations/326_chat_attachments.sql` — new table + RLS.
- `backend/app/repositories/chat_attachment_repository.py` — insert / get / set_promoted.
- `backend/app/services/chat/chat_attachment_service.py` — save file (disk) + row; promote (copy → resource).
- `backend/app/api/chat_router.py` (or a new `chat_attachments_router.py` included by it) — upload / serve / promote routes.
- `backend/tests/test_chat_attachments.py`.
- `frontend/services/chatService.ts` — `uploadChatImage(channelId, file)` + `saveChatImageToLibrary(attachmentId, scopeId)`.
- `frontend/pages/ChatPage.tsx` — `handleAttachFiles` (upload → send image message); uploading state.
- `frontend/components/chat/MessageBubble.tsx` — inline image render + save-to-library action.
- `frontend/components/chat/ImageLightbox.tsx` — fullscreen viewer (new).
- `frontend/public/locales/{en,zh}.json` — `chat.image.*`.

---

## Task 2: Backend — chat_attachments table + upload + serve

**Files:** `supabase/migrations/326_chat_attachments.sql`, `backend/app/repositories/chat_attachment_repository.py`, `backend/app/services/chat/chat_attachment_service.py`, routes in `chat_router.py` (or new router), `backend/tests/test_chat_attachments.py`. Read `307_generated_media.sql`, `generated_media_service.py` (`_download_to`/`register_generated_media`), `generated_media_router.py` (`get_generation_cover` serve), `chat_repository.py` (`_bigint`, db_engine usage), `chat_service.py` (`is_member`/`_require_member`).

- [ ] **Step 1: Migration 326** — `chat_attachments`: `id BIGINT PK DEFAULT generate_snowflake_id()`, `scope_id BIGINT NOT NULL` (team), `channel_id BIGINT NOT NULL REFERENCES channels(id) ON DELETE CASCADE`, `creator_id UUID NOT NULL`, `mime TEXT`, `file_path TEXT NOT NULL`, `file_size_bytes BIGINT`, `width INT`, `height INT`, `promoted_resource_id BIGINT`, `created_at TIMESTAMPTZ NOT NULL DEFAULT now()`. Enable RLS + a service-role-only policy (copy the `generated_media` RLS block: `FOR ALL TO service_role USING(true) WITH CHECK(true)`, plus no anon/authenticated policy). Index on `channel_id`.
- [ ] **Step 2: Repository** `ChatAttachmentRepository`: `async def create(self, *, scope_id, channel_id, creator_id, mime, file_path, file_size_bytes, width=None, height=None) -> dict` (INSERT ... RETURNING * via `db_engine.execute_returning_one`, `_bigint` coercion); `async def get(self, attachment_id) -> dict|None`; `async def set_promoted(self, attachment_id, resource_id) -> None`.
- [ ] **Step 3: Service** `chat_attachment_service.save_chat_image(*, channel_id:int, user_id:str, file_bytes:bytes, filename:str, mime:str) -> dict`: resolve the channel's `team_id` (via `chat_repository.get_channel`) as `scope_id`; verify membership (`is_member(channel_id, user_id)` → else PermissionError); generate `uuid_hex`; rel path `f"teams/{scope_id}/chat/{uuid_hex}/{safe_name}"`; write bytes under `settings.DOWNLOAD_PATH` atomically (`.part`→`os.replace`, mirror `generated_media_service._download_to`); insert the row; return it. Validate `mime` starts with `image/` (else ValueError).
- [ ] **Step 4: Routes** in `chat_router.py`:
  - `POST /channels/{channel_id}/attachments` (multipart `file: UploadFile`, `AuthDep`) → read bytes, call service, return `{id, mime, file_size_bytes, url: f"/api/v1/chat/attachments/{id}/file"}`. Map PermissionError→403, ValueError→400.
  - `GET /attachments/{attachment_id}/file` (NO auth) → fetch row, build `full = realpath(DOWNLOAD_PATH / file_path)`, guard `full.startswith(realpath(DOWNLOAD_PATH))` (else 404), `FileResponse(full, media_type=mime, headers={Cache-Control: public, max-age=604800, immutable})`. (Copy the guard from `generated_media_router.py:62-65`.)
- [ ] **Step 5: Tests** `test_chat_attachments.py` (mock db_engine + a tmp DOWNLOAD_PATH or mock the write): service rejects non-image mime; service rejects non-member (PermissionError); create builds the `teams/{scope}/chat/...` path + inserts row; serve route's realpath guard rejects `..` traversal. Backend lint. Run `uv run pytest tests/test_chat_attachments.py -v`.
- [ ] **Step 6:** Commit — `feat(chat): chat_attachments store + upload/serve endpoints (mig 326)`.

---

## Task 3: Backend — promote chat image to library (team OR personal)

**Files:** `backend/app/services/chat/chat_attachment_service.py` (add `promote`), route in `chat_router.py`, extend `test_chat_attachments.py`. Read `promote_generated_media_service.py` (the 4-step copy) + `resources_service.py:146-162` (upload layout).

- [ ] **Step 1: promote service** `promote_chat_image(*, attachment_id:int, user_id:str, target_scope_id:int) -> dict`: fetch attachment (404 if none); verify caller is a member of the attachment's channel (access) AND a member of `target_scope_id` team OR it's the caller's personal team (write-perm) — else PermissionError; idempotent (if `promoted_resource_id` set, return it). Clone `PromoteGeneratedMediaService.promote`: create resource (`source_type='upload'`, provenance in metadata: `{from:'chat', attachment_id, channel_id}`) → `shutil.copy2` file from `teams/{att.scope}/chat/...` → `teams/{target_scope}/uploads/{rid}/v1/{filename}` → create version → create resource_item (scope=target_scope) → `set_promoted(attachment_id, rid)`. Return `{promoted_resource_id}`.
- [ ] **Step 2: route** `POST /attachments/{attachment_id}/promote` body `{scope_id:int}` (`AuthDep`) → call service with `target_scope_id=scope_id`; PermissionError→403, ValueError→400. Returns `{promoted_resource_id: str}`.
- [ ] **Step 3: Tests:** promote copies the file + creates a resource under the chosen scope; idempotent (second call returns same id, no dup); non-member of target scope → 403. Lint.
- [ ] **Step 4:** Commit — `feat(chat): promote chat image to team/personal library (copy)`.

---

## Task 4: Frontend — upload + send image message

**Files:** `frontend/services/chatService.ts`, `frontend/pages/ChatPage.tsx`. Reuse `ChatAttachmentPicker.helpers` (`validateFileBatch`), the T1 `onAttachFiles`, `selectedTeamId`/`personalTeamId` from `useTeamContext`, the existing `appendMessage`/send path.

- [ ] **Step 1: chatService** — `uploadChatImage(channelId: string, file: File): Promise<{id:string; url:string; mime:string}>` → multipart POST `/chat/channels/{channelId}/attachments` (FormData `file`, auth headers, strip Content-Type). `saveChatImageToLibrary(attachmentId: string, scopeId: string): Promise<{promoted_resource_id:string}>` → POST `/chat/attachments/{attachmentId}/promote` `{scope_id:Number(scopeId)}`.
- [ ] **Step 2: ChatPage `handleAttachFiles(files)`** — guard activeId+selectedTeamId; `validateFileBatch`; keep only `image/*` (else toast `chat.image.onlyImages`); `setUploadingImages(n+1)`; for each: `const att = await chatService.uploadChatImage(activeIdRef.current, file)`; `body = {kind:'image', attachment_id: att.id, image_url: att.url, alt: file.name}`; `const msg = await chatService.sendMessage(activeIdRef.current, body, 'media_card')`; `appendMessage(msg)`; `scheduleMarkRead`; on error toast `chat.image.uploadError`; finally decrement. Pass `onAttachFiles={handleAttachFiles}` to `<Composer>` + show `chat.image.uploading` indicator when `uploadingImages>0`.
- [ ] **Step 3:** `npx tsc --noEmit` + `npm run build`. Commit — `feat(chat): upload chat image (independent store) + send inline image message`.

---

## Task 5: Frontend — inline render + lightbox + save-to-library

**Files:** `frontend/components/chat/MessageBubble.tsx`, `frontend/components/chat/ImageLightbox.tsx` (new), `frontend/pages/ChatPage.tsx` (pass personalTeamId + a save handler down), `frontend/public/locales/{en,zh}.json`. Read MediaCard sub-component + a `fixed inset-0` modal (ConfirmDialog).

- [ ] **Step 1: ImageLightbox.tsx** — `{src, alt?, onClose}`; `fixed inset-0 z-[60] bg-black/80` overlay, centered `<img className="max-w-[92vw] max-h-[92vh] object-contain">`, backdrop/Esc/close-button → onClose, image click stopPropagation. Island, zero emoji.
- [ ] **Step 2: MessageBubble inline image** — detect `isImage = content_type==='media_card' && body.kind==='image' && typeof body.image_url==='string'`. Render inline `<button onClick=open lightbox><img src={body.image_url} alt={body.alt} className="max-w-[min(78%,360px)] max-h-[320px] rounded-[12px] object-cover" loading="lazy" onError=hide/></button>`, aligned by existing own/other `items-end/start`; local lightbox state + render `<ImageLightbox>`. On hover (own OR any — your call; for now ALL image messages), show a small **"Save to library"** button → calls a passed `onSaveImage?(attachmentId)` (which opens a tiny team/personal chooser). Existing non-image media_card branch unchanged.
- [ ] **Step 3: Save-to-library UX** — simplest: a small action on the image that, on click, shows two choices (team / personal) — implement as a lightweight inline menu or a `window.confirm`-style two-step is too crude; do a tiny popover with two buttons (`chat.image.saveTeam` / `chat.image.savePersonal`). ChatPage provides `handleSaveImage(attachmentId, scope:'team'|'personal')` → `chatService.saveChatImageToLibrary(attachmentId, scope==='team'?selectedTeamId:personalTeamId)` → toast `chat.image.saved`. Pass it + a flag down to MessageBubble via MessageList (new optional props `onSaveImage`). (personalTeamId from `useTeamContext`.)
- [ ] **Step 4: i18n** `chat.image.*`: `uploading`, `uploadError`, `onlyImages`, `close`, `imageAlt`, `save` ("Save to library"/"保存到素材库"), `saveTeam` ("Team library"/"团队素材库"), `savePersonal` ("My library"/"我的素材库"), `saved` ("Saved to library"/"已保存到素材库"), `saveError`. en+zh parity, valid JSON. (`dropHint` already added in T1.)
- [ ] **Step 5: Tests** — MessageBubble: `kind:'image'` renders `<img>` (not the boxed card / no "Open in Library"); non-`kind` media_card unchanged; clicking image opens lightbox (state). Keep suite green.
- [ ] **Step 6:** `npx vitest run components/chat/MessageBubble.test.tsx` + tsc + build + JSON parse. Commit — `feat(chat): inline image + lightbox + save-to-library (team/personal)`.

---

## Self-Review
**Spec coverage:** independent chat-image store (`teams/{team}/chat/` + `chat_attachments`, NOT the library) per user decision; click/paste/drop upload (T1); inline render + fullscreen lightbox (Discord/Feishu); opt-in **save to team OR personal library** = physical copy (clone of generated-media promote). Agent standalone images = `generated_media` (unchanged, answered); chat-posted images = `chat_attachments`.
**Deferrals:** non-image attachments; image width/height capture (nullable; layout uses capped box); agent POSTING images into channels (store is ready, the agent-turn image path is separate); gallery grouping; dedup across chat. 
**Security:** serve = world-readable-by-snowflake-id + realpath guard (same posture as cover routes); upload/promote membership-gated; `chat_attachments` RLS service-role-only.
**Verification:** backend pytest + frontend unit/build + real logged-in browser pass (upload via paste/drop/pick → inline → expand → save to team & personal → confirm it appears in the resource library) + cleanup.

## Execution Handoff
Execute via superpowers:subagent-driven-development; T2/T3 backend (mig+endpoints), T4/T5 frontend; final whole-branch review; **real logged-in UI verification**; ship (backend → CI → ACR deploy; frontend → Vercel) → flip private.
