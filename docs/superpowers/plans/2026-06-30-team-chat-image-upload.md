# Team Chat — Send Images (unified staged-media store + opt-in save-to-library) Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development. Steps use `- [ ]`.
> **REVISED v3 (2026-06-30):** media layer is being UNIFIED (3 stores → 1). Instead of a separate `chat_attachments` table, chat images become rows in the existing **`generated_media`** store (now the single "staged media" tier: AI generations + chat uploads, later 1:1 AI-chat attachments). They are NOT in the resource library. A per-image **"Save to library"** (team OR personal) COPIES the file into `resources` via the existing promote flow. The original v2 `chat_attachments` table (mig 326, committed 5389e239, never deployed) is being REPLACED by this approach.

**Goal:** Send images in Team Chat (Discord/Feishu-style: click + paste + drag-drop, inline render, click-to-expand). Uploaded chat images live in the **unified staged store** (`generated_media` row, `origin_kind='chat_upload'`, file at `teams/{team}/chat/{uuid}/{name}` on disk) — NOT in the resource library. A per-image **"Save to library"** action copies the file into the **team OR personal** resource library (opt-in import).

**Architecture (verified against code):** `generated_media` (mig 307) is already a scoped, service-role-only, provenance-rich blob store with a no-auth `GET /generated-media/{id}/cover` serve and a `promote → resources` flow. We GENERALIZE it into the single staged tier: add a nullable `channel_id` column; `origin_kind` is unconstrained TEXT so `'chat_upload'` needs no constraint change. A chat image upload writes raw bytes to `teams/{team}/chat/{uuid}/{name}` and inserts a `generated_media` row (`origin_kind='chat_upload'`, `channel_id` set, `media_kind='image'`). Inline `<img>` uses the **existing** `GET /generated-media/{id}/cover` (no-auth, image-only, realpath-guarded, immutable cache) — so NO new serve route. The chat message is a `media_card` with body `{kind:'image', generated_media_id, image_url:'/api/v1/generated-media/{id}/cover', alt}` (reuse `media_card` — no content_type migration). Promote is generalized to accept a **target scope** (team or personal) distinct from the row's own scope; a new `POST /chat/attachments/{id}/promote {scope_id}` reuses it. `MessageBubble` renders `kind:'image'` inline + click → `ImageLightbox`.

**Tech Stack:** FastAPI + db_engine (service-role, BYPASSRLS) + `os`/`shutil`/`DOWNLOAD_PATH`; React 19 + existing composer affordances (T1 done) + RTL.

## Global Constraints
- **Branch:** `feature/chat-image-upload` (T1 already committed: composer click/paste/drop → `onAttachFiles`, a29f933e).
- **ONE staged-media store — generalize `generated_media`, do NOT add a parallel table.** Chat images are `generated_media` rows. Keep the table name `generated_media` and the `/generated-media/*` routes (renaming would break `/generated-media/{id}/cover` URLs already persisted in canvas/message bodies). Treat the table semantically as the "staged media" tier; update its COMMENT to say so.
- **NOT the resource library.** Staged rows do not appear in `resources` unless the user explicitly "Saves to library" (promote = physical copy).
- **Reuse, don't reinvent:** writer mirrors `generated_media_service.register_generated_media` (atomic `.part`→`os.replace`); serve is the EXISTING `get_generation_cover`; promote is the EXISTING `PromoteGeneratedMediaService.promote` generalized to a target scope.
- **Reuse `media_card` content_type** (body free-form): image messages set `body.kind='image'`. No new content_type, no DB CHECK / Pydantic-Literal change.
- **Membership gates:** upload requires the caller be a member of the channel (reuse `ChatRepository.is_member`); the staged row's `scope_id` = the channel's `team_id`. Promote requires the caller can READ the row (channel member for `chat_upload`; scope access for generations) AND can WRITE the target `scope_id` (member of that team, or it is their personal team).
- **Serve security:** unchanged — `get_generation_cover` is world-readable-by-snowflake-id + `os.path.realpath` under `DOWNLOAD_PATH` traversal guard + immutable cache. Chat images reuse it as-is.
- **Migration number = 326** (max is 325). The 326 file currently CREATES `chat_attachments`; REWRITE it to (a) `DROP TABLE IF EXISTS public.chat_attachments` and (b) `ALTER TABLE public.generated_media ADD COLUMN channel_id` + index + COMMENT update. No new RLS (generated_media is already service-role-only).
- **Image-only this slice:** non-image files rejected with a toast. Limits via `ChatAttachmentPicker.helpers` (≤4 files, ≤50MB).
- **1:1 AI-chat attachments (the 3rd store) fold-in is Task 6 — GATED.** It is behavior-changing (1:1 attachments would stop being first-class `resources`), so it is a separate, independently-shippable task that MUST get an explicit human go-ahead before execution. SDD stops after Task 5.
- **Island UI**, zero emoji, no `zinc-*`; i18n en+zh parity. Backend lint black/isort/flake8; frontend tsc + build + vitest.
- **Verification:** unit/build per task + a **real logged-in browser pass** (paste/drop/pick → inline → expand → save-to-library team+personal → confirm it appears in the resource library) per `feedback_ui_early_visual_ux_pass`; clean up test data after.

## File Structure
- `supabase/migrations/326_chat_attachments.sql` — REWRITE: drop `chat_attachments`, alter `generated_media` (add `channel_id`).
- `backend/app/repositories/generated_media_repository.py` — add `channel_id` to `_COLS` + `_BIGINT_COLS`.
- `backend/app/services/library/generated_media_service.py` — add `channel_id` to `GenerationOrigin`; add `register_uploaded_media` (raw-bytes writer).
- `backend/app/services/chat/chat_attachment_service.py` — `save_chat_image` now writes a `generated_media` row via `register_uploaded_media` (keep membership/mime checks).
- `backend/app/services/library/promote_generated_media_service.py` — generalize `promote` to a target scope + read/write authorization.
- `backend/app/api/chat_router.py` — `POST /channels/{cid}/attachments` (returns `/generated-media/{id}/cover` url); DELETE the v2 `GET /attachments/{id}/file` serve route; add `POST /attachments/{id}/promote`.
- `backend/app/api/generated_media_router.py` — update existing `promote_generation` to the new service signature (behavior unchanged: target = personal).
- DELETE `backend/app/repositories/chat_attachment_repository.py` (folded into generated_media repo).
- `backend/tests/test_chat_attachments.py` — rewrite against the generated_media path.
- `frontend/services/chatService.ts` — `uploadChatImage` + `saveChatImageToLibrary`.
- `frontend/pages/ChatPage.tsx` — `handleAttachFiles` + save handler + uploading state.
- `frontend/components/chat/MessageBubble.tsx` — inline image render + save-to-library action.
- `frontend/components/chat/ImageLightbox.tsx` — fullscreen viewer (new).
- `frontend/public/locales/{en,zh}.json` — `chat.image.*`.

---

## Task 2: Backend — fold chat uploads into the `generated_media` staged store

**Files:** REWRITE `supabase/migrations/326_chat_attachments.sql`; edit `generated_media_repository.py`, `generated_media_service.py`, `chat_attachment_service.py`, `chat_router.py`; DELETE `chat_attachment_repository.py`; rewrite `backend/tests/test_chat_attachments.py`. Read `307_generated_media.sql`, `generated_media_service.py` (`_download_to`/`register_generated_media`/`GenerationOrigin`), `generated_media_router.py:49-72` (`get_generation_cover`), `generated_media_repository.py` (`_COLS`/`_normalize`/`get_by_id`), `chat_repository.py` (`get_channel`/`is_member`/`_bigint`).

- [ ] **Step 1: REWRITE migration 326.** Replace the whole file with:
  ```sql
  -- 326 — Unify media layer: chat uploads become generated_media rows (the
  -- single "staged media" tier). Drops the short-lived chat_attachments table
  -- (branch-only, never deployed) and adds channel_id to generated_media.
  DROP TABLE IF EXISTS public.chat_attachments;

  ALTER TABLE public.generated_media
      ADD COLUMN IF NOT EXISTS channel_id BIGINT
      REFERENCES public.channels(id) ON DELETE SET NULL;

  CREATE INDEX IF NOT EXISTS idx_genmedia_channel
      ON public.generated_media (channel_id);

  COMMENT ON TABLE public.generated_media IS
      'Unified "staged media" tier: AI generations (origin_kind agent_run/canvas_run) '
      'and chat uploads (origin_kind chat_upload, channel_id set). Cheap/high-churn; '
      'promote to resources on keep/use. Backend-only (service-role RLS).';
  ```
- [ ] **Step 2: repo `channel_id`.** In `generated_media_repository.py`: append `, channel_id` to `_COLS` (after `promoted_resource_id,` keep `created_at` last — i.e. `"... promoted_resource_id, channel_id, created_at"`); add `"channel_id"` to `_BIGINT_COLS`. (No other change — `get`/`get_by_id`/`list_for_scope`/`mark_promoted` all use `_COLS`.)
- [ ] **Step 3: `GenerationOrigin.channel_id` + INSERT.** In `generated_media_service.py`: add `channel_id: Optional[int] = None` to the `GenerationOrigin` dataclass; in `register_generated_media`'s INSERT add the `channel_id` column + `:channel_id` param + `"channel_id": origin.channel_id` to the params dict (keep the existing `agent_run`/`canvas_run` callers working — they pass no channel_id, so it stays NULL).
- [ ] **Step 4: `register_uploaded_media` (raw-bytes writer).** Add to `generated_media_service.py`:
  ```python
  import re

  def _safe_filename(name: str) -> str:
      name = os.path.basename(name or "")
      name = re.sub(r"[^\w.\-]", "_", name)
      return name or "attachment"

  async def register_uploaded_media(
      *,
      user_id: str,
      scope_id: int,
      file_bytes: bytes,
      filename: str,
      mime: str,
      origin: GenerationOrigin,
      subdir: str = "chat",
  ) -> dict:
      """Write uploaded bytes into the staged store and insert one row. Returns it."""
      kind = media_kind_from_mime(mime)
      gen_uuid = _uuid.uuid4().hex
      rel = f"teams/{scope_id}/{subdir}/{gen_uuid}/{_safe_filename(filename)}"
      dest = f"{settings.DOWNLOAD_PATH}/{rel}"
      os.makedirs(os.path.dirname(dest), exist_ok=True)
      part = dest + ".part"
      try:
          with open(part, "wb") as fp:
              fp.write(file_bytes)
          os.replace(part, dest)
      except BaseException:
          Path(part).unlink(missing_ok=True)
          raise
      row = await db_engine.execute_returning_one(
          "INSERT INTO public.generated_media "
          "(scope_id, creator_id, media_kind, mime, file_path, file_size_bytes, "
          " origin_kind, channel_id) "
          "VALUES (:scope_id, :creator_id, :media_kind, :mime, :file_path, "
          " :file_size_bytes, :origin_kind, :channel_id) RETURNING *",
          {
              "scope_id": scope_id,
              "creator_id": user_id,
              "media_kind": kind,
              "mime": mime,
              "file_path": rel,
              "file_size_bytes": len(file_bytes),
              "origin_kind": origin.kind,
              "channel_id": origin.channel_id,
          },
      )
      return row or {}
  ```
  Add `register_uploaded_media` and `_safe_filename` to `__all__`.
- [ ] **Step 5: `save_chat_image` → generated_media.** Rewrite `chat_attachment_service.py` so `save_chat_image(*, channel_id, user_id, file_bytes, filename, mime, chat_repo=None)`:
  - keep `if not mime.startswith("image/"): raise ValueError(...)`;
  - `chat_repo = chat_repo or get_chat_repository()`; `channel = await chat_repo.get_channel(channel_id=channel_id)` → ValueError if None; `scope_id = int(channel["team_id"])`;
  - `if not await chat_repo.is_member(channel_id=channel_id, user_id=user_id): raise PermissionError(...)`;
  - `row = await register_uploaded_media(user_id=user_id, scope_id=scope_id, file_bytes=file_bytes, filename=filename, mime=mime, origin=GenerationOrigin(kind="chat_upload", channel_id=channel_id), subdir="chat")`; `return row`.
  - Remove the `ChatAttachmentRepository` import + the local file-write block (now in `register_uploaded_media`). Import `register_uploaded_media`, `GenerationOrigin` from `app.services.library.generated_media_service`.
- [ ] **Step 6: routes.** In `chat_router.py`:
  - `POST /channels/{channel_id}/attachments` — unchanged body; change the return to `{"id": att_id, "mime": row.get("mime"), "file_size_bytes": row.get("file_size_bytes"), "url": f"/api/v1/generated-media/{att_id}/cover"}`.
  - DELETE the entire `GET /attachments/{attachment_id}/file` route (serve is now `/generated-media/{id}/cover`). Remove now-unused imports: `os`, `FileResponse`, `settings`, `get_chat_attachment_repository`. Keep `UploadFile`.
- [ ] **Step 7: DELETE** `backend/app/repositories/chat_attachment_repository.py`.
- [ ] **Step 8: rewrite tests** `test_chat_attachments.py` (mock `chat_repo` with `get_channel`/`is_member`; monkeypatch `settings.DOWNLOAD_PATH` to `tmp_path`; let the real INSERT be mocked by monkeypatching `db_engine.execute_returning_one`): (1) non-image mime → ValueError; (2) non-member → PermissionError; (3) member + image → writes a file under `tmp_path/teams/{scope}/chat/...` AND calls the INSERT with `origin_kind='chat_upload'` + `channel_id` set; (4) `_safe_filename` strips `../` and unsafe chars. Drop the old serve-route/realpath test (route removed; `get_generation_cover` already covered by generated-media tests). Backend lint. Run `uv run pytest tests/test_chat_attachments.py -v`.
- [ ] **Step 9: Commit** — `refactor(chat): fold chat uploads into generated_media staged store (mig 326 rewrite)`.

---

## Task 3: Backend — promote staged image to library (team OR personal)

**Files:** `backend/app/services/library/promote_generated_media_service.py`, `backend/app/api/chat_router.py`, `backend/app/api/generated_media_router.py`, extend `backend/tests/test_chat_attachments.py` (+ touch `test_generated_media*` if it asserts the promote signature). Read `promote_generated_media_service.py` (the 4-step copy), `generated_media_repository.py` (`get_by_id`), `chat_repository.py` (`is_member`), and how team membership is checked elsewhere (`team_repository` / `teams_router`).

- [ ] **Step 1: generalize `promote`.** Change `PromoteGeneratedMediaService.promote` signature to `promote(*, gen_id:int, user_id:str, target_scope_id:int)`:
  - `gen = await self.gen_repo.get_by_id(gen_id)` (no scope filter); `if not gen: raise ValueError("generation not found")`.
  - **Read authorization:** if `gen.get("origin_kind") == "chat_upload"`: require `await self._is_channel_member(int(gen["channel_id"]), user_id)` (inject a `ChatRepository`); else require `await self._can_access_scope(user_id, int(gen["scope_id"]))`. Raise `PermissionError` on failure.
  - **Write authorization:** require `await self._can_access_scope(user_id, target_scope_id)` (member of that team OR it's the caller's personal team) — else `PermissionError`.
  - idempotency unchanged (return existing if `promoted_resource_id`).
  - everywhere the method used `scope_id`, use `target_scope_id` (resource_item scope, the `teams/{target_scope_id}/uploads/...` path). Provenance metadata: add `"from": gen.get("origin_kind")`, `"channel_id": gen.get("channel_id")`. For `source_type`: keep `'generated'` for agent/canvas origins; use `'upload'` when `origin_kind=='chat_upload'`.
  - Add helpers: `_can_access_scope(user_id, scope_id)` → true if scope is the user's personal team (`_resolve_personal_team_id`) or the user is a member of that team (reuse the team-membership query used by `teams_router`/`team_repository`); `_is_channel_member(channel_id, user_id)` → `ChatRepository().is_member(...)`.
- [ ] **Step 2: keep generations route working.** In `generated_media_router.py::promote_generation`, change the call to `promote(gen_id=gen_id, user_id=str(auth.user_id), target_scope_id=await _scope(auth))` (personal scope — behavior unchanged). 
- [ ] **Step 3: chat promote route.** In `chat_router.py` add:
  ```python
  class ChatAttachmentPromote(BaseModel):
      scope_id: int

  @router.post("/attachments/{attachment_id}/promote")
  async def promote_chat_attachment(attachment_id: int, payload: ChatAttachmentPromote, auth: AuthDep):
      try:
          res = await PromoteGeneratedMediaService().promote(
              gen_id=attachment_id, user_id=str(auth.user_id), target_scope_id=payload.scope_id,
          )
      except PermissionError as exc:
          raise HTTPException(status_code=403, detail=str(exc))
      except ValueError as exc:
          raise HTTPException(status_code=404, detail=str(exc))
      return {"promoted_resource_id": str(res["id"])}
  ```
  Import `PromoteGeneratedMediaService`.
- [ ] **Step 4: Tests:** promote a `chat_upload` row to a target scope copies the file to `teams/{target}/uploads/...` + creates resource/version/resource_item under target; idempotent (2nd call same id, no dup); non-member of target scope → PermissionError; non-member of the source channel → PermissionError. Mock repos + tmp DOWNLOAD_PATH. Lint. `uv run pytest tests/test_chat_attachments.py -v`.
- [ ] **Step 5: Commit** — `feat(chat): promote staged chat image to team/personal library (copy)`.

---

## Task 4: Frontend — upload + send image message

**Files:** `frontend/services/chatService.ts`, `frontend/pages/ChatPage.tsx`. Reuse `ChatAttachmentPicker.helpers` (`validateFileBatch`), the T1 `onAttachFiles`, `selectedTeamId`/personal team from `useTeamContext`, the existing `sendMessage`/`appendMessage` path.

- [ ] **Step 1: chatService** — `uploadChatImage(channelId: string, file: File): Promise<{id:string; url:string; mime:string}>` → multipart POST `/chat/channels/{channelId}/attachments` (FormData `file`, auth headers, do NOT set Content-Type manually). `saveChatImageToLibrary(attachmentId: string, scopeId: string): Promise<{promoted_resource_id:string}>` → POST `/chat/attachments/{attachmentId}/promote` body `{scope_id: Number(scopeId)}`.
- [ ] **Step 2: ChatPage `handleAttachFiles(files)`** — guard `activeIdRef.current` + `selectedTeamId`; `validateFileBatch`; keep only `image/*` (else toast `chat.image.onlyImages`); `setUploadingImages(n => n + imgs.length)`; for each image: `const att = await chatService.uploadChatImage(activeIdRef.current!, file)`; `const body = {kind:'image', generated_media_id: att.id, image_url: att.url, alt: file.name}`; `const msg = await chatService.sendMessage(activeIdRef.current!, body, 'media_card')`; `appendMessage(msg)`; `scheduleMarkRead?.()`; on error toast `chat.image.uploadError` + `console.error`; finally `setUploadingImages(n => n - 1)`. Pass `onAttachFiles={handleAttachFiles}` to `<Composer>`; show a `chat.image.uploading` indicator while `uploadingImages > 0`.
- [ ] **Step 3:** `npx tsc --noEmit` + `npm run build`. Commit — `feat(chat): upload chat image (staged store) + send inline image message`.

---

## Task 5: Frontend — inline render + lightbox + save-to-library

**Files:** `frontend/components/chat/MessageBubble.tsx`, `frontend/components/chat/ImageLightbox.tsx` (new), `frontend/components/chat/MessageList.tsx` (thread the new props), `frontend/pages/ChatPage.tsx` (provide personal team id + save handler), `frontend/public/locales/{en,zh}.json`. Read the `MediaCard` sub-component + a `fixed inset-0` modal for the lightbox pattern.

- [ ] **Step 1: ImageLightbox.tsx** — `{src, alt?, onClose}`; `fixed inset-0 z-[60] bg-black/80` overlay, centered `<img className="max-w-[92vw] max-h-[92vh] object-contain">`, backdrop click / Esc / close button → onClose, image click `stopPropagation`. Island, zero emoji.
- [ ] **Step 2: MessageBubble inline image** — `const isImage = message.content_type==='media_card' && (message.body as any).kind==='image' && typeof (message.body as any).image_url==='string'`. Render BEFORE the `isMediaCard` branch: an inline `<button>` wrapping `<img src={body.image_url} alt={body.alt} className="max-w-[min(78%,360px)] max-h-[320px] rounded-[12px] object-cover" loading="lazy" onError={hide}/>`, click → open local lightbox state; render `<ImageLightbox>` when open. Alignment uses the existing own/other `items-end/start`. On hover show a small **"Save to library"** button (Island style) → `onSaveImage?.(generatedMediaId)` opens a tiny two-button popover (team / personal). The existing non-image `media_card` branch stays unchanged.
- [ ] **Step 3: Save-to-library UX** — small popover with two buttons (`chat.image.saveTeam` / `chat.image.savePersonal`). `ChatPage` provides `handleSaveImage(generatedMediaId, scope:'team'|'personal')` → `chatService.saveChatImageToLibrary(generatedMediaId, scope==='team' ? selectedTeamId : personalTeamId)` → toast `chat.image.saved` (or `chat.image.saveError`). Thread `onSaveImage` + `personalTeamId` through `MessageList` → `MessageBubble` as new optional props. (Personal team id from `useTeamContext`.)
- [ ] **Step 4: i18n** `chat.image.*`: `uploading`, `uploadError`, `onlyImages`, `close`, `imageAlt`, `save` ("Save to library"/"保存到素材库"), `saveTeam` ("Team library"/"团队素材库"), `savePersonal` ("My library"/"我的素材库"), `saved` ("Saved to library"/"已保存到素材库"), `saveError` ("Save failed"/"保存失败"). en+zh parity, valid JSON. (`dropHint` already added in T1.)
- [ ] **Step 5: Tests** — MessageBubble: `kind:'image'` renders `<img>` (not the boxed card / no "Open in Library"); non-`kind` media_card unchanged; clicking image opens lightbox (state). Keep suite green.
- [ ] **Step 6:** `npx vitest run components/chat/MessageBubble.test.tsx` + tsc + build + JSON parse. Commit — `feat(chat): inline image + lightbox + save-to-library (team/personal)`.

---

## Task 6 (GATED — needs explicit human go-ahead): fold 1:1 AI-chat attachments into the staged store

> **Do NOT execute as part of the SDD run.** This is the third store (1:1 AI-chat attachments, currently `resources` rows in a `temp` folder via `chat_upload.save_chat_temp_upload`). Folding it into `generated_media` completes 3→1 but **changes 1:1 behavior**: AI-chat attachments would no longer be first-class `resources` (they'd be staged, promote-on-keep). Confirm the behavior change first.

**Sketch (for the eventual plan):** add `session_id BIGINT` to `generated_media` (mig 327); route `ai_library_router.upload_chat_attachment` → `register_uploaded_media(origin=GenerationOrigin(kind="ai_chat_upload", ...), subdir="ai-chat")`; 1:1 attachment refs point at `generated_media` ids; retire `chat_upload.save_chat_temp_upload` + the (already-disabled) `temp_resource_sweeper`. Inline render reuses `/generated-media/{id}/cover`. Promote already supports it (add `ai_chat_upload` to the read-auth branch: session ownership).

---

## Self-Review
**Spec coverage:** unify media (3→1) per user decision — chat uploads fold into `generated_media` (the staged tier); click/paste/drop upload (T1); inline render + fullscreen lightbox (Discord/Feishu); opt-in **save to team OR personal library** = physical copy via the generalized promote. 1:1 fold = gated Task 6. Agent standalone generations = `generated_media` (unchanged).
**Deferrals:** non-image attachments; image width/height capture (skipped — YAGNI; CSS-capped box); agent POSTING images into channels (store ready; agent-turn image path separate); 1:1 fold (Task 6, gated); gallery grouping; cross-chat dedup.
**Security:** serve = existing world-readable-by-snowflake-id + realpath guard; upload membership-gated; promote read+write authorization; `generated_media` RLS service-role-only (unchanged).
**Type consistency:** `generated_media_id` (string, bigIntSafe) flows upload→message body→save. `channel_id` added to `_COLS`/`_BIGINT_COLS` and `GenerationOrigin`. Promote signature `target_scope_id` updated at BOTH call sites (generations route + chat route).
**Verification:** backend pytest + frontend unit/build + real logged-in browser pass (upload via paste/drop/pick → inline → expand → save to team & personal → confirm it appears in the resource library) + cleanup.

## Execution Handoff
Execute via superpowers:subagent-driven-development: T2 (fold to generated_media) → T3 (generalized promote) → T4/T5 frontend → final whole-branch review → **real logged-in UI verification** → ship (backend → CI → ACR deploy; frontend → Vercel) → flip private → clean up test data. **Stop before Task 6** (gated).
