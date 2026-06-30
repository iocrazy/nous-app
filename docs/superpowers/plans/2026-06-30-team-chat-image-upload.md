# Team Chat — Send Images (Discord/Feishu-style) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development. Steps use `- [ ]`.

**Goal:** Let users send images in Team Chat the way Discord/Feishu do: upload via **click + paste + drag-drop**, the image renders **inline** in the message bubble (not a boxed card-with-filename), click to **expand fullscreen**. Uploaded images are saved to the **current team's resource library** (NAS disk) and the message references the resource.

**Architecture (verified reuse path):** Composer gains upload affordances (wire the existing paperclip button to a hidden file input, attach `useComposerPaste` to the textarea, wrap the composer in `useComposerDropzone`); all funnel into one `onAttachFiles(files)` callback. ChatPage uploads each file via `resourceService.uploadResource(file, selectedTeamId)` (team-scoped — lands at `teams/{teamId}/uploads/...`), then sends a `media_card` message whose body is marked `kind:'image'` (`{resource_id, kind:'image', image_url: getResourceCoverUrl(id), alt}`). `MessageBubble` renders `kind:'image'` media_cards as an **inline `<img>`** (cover URL is public/no-auth) with click → a new `ImageLightbox` overlay. **Reuse the `media_card` content type** — body is free-form JSON, so no new content_type / DB migration / Pydantic-Literal change.

**Tech Stack:** React 19 + existing `resourceService.uploadResource` + `useComposerPaste`/`useComposerDropzone` hooks + `ChatAttachmentPicker.helpers` (validation) + i18next.

## Global Constraints
- **Branch:** `feature/chat-image-upload` (off origin/master).
- **Reuse, don't rebuild:** upload → `resourceService.uploadResource(file, scopeId, folderId?, onProgress?)` (scopeId = `selectedTeamId` from `useTeamContext`). paste → `useComposerPaste({onFiles})`. drop → `useComposerDropzone({onFiles})`. validation/limits → `ChatAttachmentPicker.helpers` (`validateFileBatch`, `MAX_FILES_AT_ONCE=4`, `MAX_FILE_SIZE_BYTES=50MB`, `ACCEPT_ATTR`). Do NOT reuse `useChatAttachmentUpload`/`aiLibraryService.uploadChatAttachment` — those go to the AI-chat 24h-TTL temp endpoint, NOT the team library.
- **Storage = team resource library** (the big-player object-storage/file-service equivalent): images uploaded in a team's chat become resources in that team's library (scope_id = teamId). This is intentional (Feishu-style chat-files-in-space) + reuses dedup/cover/serving.
- **Content type stays `media_card`**; image messages set `body.kind = 'image'`. Body shape: `{ resource_id: string, kind: 'image', image_url: string (cover URL), alt?: string }`. Non-image media_cards (the existing resource-picker cards) are unaffected (no `kind`, render as today).
- **Inline `<img>` uses the cover URL** (`getResourceCoverUrl(id)`, no token) — confirmed public/no-auth and serves the full original for image mimes. Do NOT use `/file` (needs `?token=`).
- **Multiple files**: a paste/drop/pick of N images (≤4) sends N image messages (one per image), each through the existing append/dedupe path. Validation rejects non-images for the image path (or routes non-images to the existing resource flow — for THIS slice, accept images only; reject others with a toast).
- **Optimistic UX**: while uploading, show a lightweight "sending image…" indicator (a transient state); on success the real message arrives via the normal send+append. Errors → toast, no half-sent state.
- **Island UI:** zero emoji, no `zinc-*`; match the chat module's hex/token style. Lightbox = a `fixed inset-0` overlay (follow existing modal pattern, e.g. ConfirmDialog), `object-contain`, click-backdrop / Esc to close.
- **i18n** for all visible text (en+zh parity, valid JSON).
- **Verification:** `npx vitest run <files>` + `npx tsc --noEmit` + `npm run build` per task; **plus a real logged-in browser pass** (per feedback_ui_early_visual_ux_pass): paste/drop/pick an image → it appears inline → click expands. (Register a test user; clean up after.)

## File Structure
- `frontend/components/chat/Composer.tsx` — wire paperclip → hidden `<input type=file accept=image>`; attach `useComposerPaste` + `useComposerDropzone`; `onAttachFiles?(files: File[])` prop + drag overlay.
- `frontend/pages/ChatPage.tsx` — `handleAttachFiles(files)`: validate → uploadResource(each, selectedTeamId) → build image media_card body → sendMessage; uploading state.
- `frontend/components/chat/MessageBubble.tsx` — render `body.kind==='image'` media_cards inline; click → lightbox.
- `frontend/components/chat/ImageLightbox.tsx` — new fullscreen image overlay.
- `frontend/public/locales/{en,zh}.json` — `chat.image.*` keys.

---

## Task 1: Composer upload affordances (click + paste + drag-drop)

**Files:** Modify `frontend/components/chat/Composer.tsx`. Read `useComposerPaste.ts`, `useComposerDropzone.ts`, `ChatAttachmentPicker.helpers.ts` first.

**Interfaces (Produces):** Composer gains `onAttachFiles?: (files: File[]) => void`. The paperclip button opens a hidden file input (`accept` = images); pasting image files / dropping files calls `onAttachFiles`. A drag overlay shows on `isDragActive`.

- [ ] **Step 1:** Add `onAttachFiles?: (files: File[]) => void` to `ComposerProps`. Add a hidden `<input ref type="file" accept="image/*" multiple>`; the existing paperclip button (`title={t('chat.attachResource')}`, currently no onClick) → `onClick` triggers the input; on change → `onAttachFiles(Array.from(files))` + reset input value. (Keep the existing 附加媒体/Image button → onAttachMedia ResourcePicker unchanged.)
- [ ] **Step 2:** Attach `const { onPaste } = useComposerPaste({ onFiles: (f) => onAttachFiles?.(f), disabled })` to the textarea's `onPaste`. (Read useComposerPaste's exact `onFiles` arg type — File[] or FileList; adapt.)
- [ ] **Step 3:** Wrap the composer root with `useComposerDropzone({ onFiles: (f) => onAttachFiles?.(f), disabled })` → spread `rootProps` on the wrapper, render a subtle drag overlay (island-styled, `t('chat.image.dropHint')`) when `isDragActive`.
- [ ] **Step 4:** `cd frontend && npx tsc --noEmit` (no new errors) + `npm run build`. Commit — `feat(chat): composer image attach affordances (click/paste/drop)`.

---

## Task 2: ChatPage upload + send image messages

**Files:** Modify `frontend/pages/ChatPage.tsx`. Reuse `resourceService.uploadResource`, `getResourceCoverUrl`, `validateFileBatch` (from ChatAttachmentPicker.helpers), `selectedTeamId`, the existing `appendMessage`/send path.

**Interfaces (Consumes):** Composer.onAttachFiles (Task 1).

- [ ] **Step 1:** Add `const [uploadingImages, setUploadingImages] = useState(0)` (count of in-flight uploads, for the indicator).
- [ ] **Step 2:** `handleAttachFiles(files)` (useCallback): guard `activeId` + `selectedTeamId`. Run `validateFileBatch(files)` (reuse the helper for count/size); keep only `image/*` files — if any non-image, `addToast(t('chat.image.onlyImages'),'error')` and drop them (this slice = images only). For each valid image, sequentially (or `Promise.all`, bounded): `setUploadingImages(n=>n+1)`, `const res = await resourceService.uploadResource(file, selectedTeamId)`, build `body = { resource_id: String(res.id), kind: 'image', image_url: getResourceCoverUrl(String(res.id)), alt: res.filename ?? 'image' }`, `const msg = await chatService.sendMessage(activeIdRef.current, body, 'media_card')`, `appendMessage(msg)` (reuse existing helper + dedupe), `scheduleMarkRead(...)`; on error `addToast(t('chat.image.uploadError'),'error')`; finally `setUploadingImages(n=>n-1)`.
- [ ] **Step 3:** Pass `onAttachFiles={handleAttachFiles}` to `<Composer>`. Show an "uploading image…" indicator (e.g. near the composer or TypingIndicator slot) when `uploadingImages > 0` (`t('chat.image.uploading')`).
- [ ] **Step 4:** `npx tsc --noEmit` + `npm run build`. Commit — `feat(chat): upload chat images to team library + send as inline image message`.

---

## Task 3: Inline image render + ImageLightbox

**Files:** Modify `frontend/components/chat/MessageBubble.tsx`; create `frontend/components/chat/ImageLightbox.tsx`. Read the existing MediaCard sub-component + a `fixed inset-0` modal (e.g. `ConfirmDialog.tsx`) for the overlay pattern.

- [ ] **Step 1: ImageLightbox.tsx** — props `{ src: string; alt?: string; onClose: () => void }`. A `fixed inset-0 z-[60] bg-black/80` overlay, centered `<img src alt className="max-w-[92vw] max-h-[92vh] object-contain rounded">`; click backdrop or Esc → `onClose` (add a keydown listener + cleanup); a small close button. Island-styled, zero emoji. Clicking the image itself should NOT close (stopPropagation).
- [ ] **Step 2: MessageBubble inline image** — in the content branch, BEFORE the existing `isMediaCard` card branch, detect `const isImage = message.content_type === 'media_card' && (message.body as any).kind === 'image' && typeof (message.body as any).image_url === 'string'`. When `isImage`, render an inline thumbnail: `<button onClick={()=>setLightbox(true)}><img src={body.image_url} alt={body.alt} className="max-w-[min(78%,360px)] max-h-[320px] rounded-[12px] object-cover ..." loading="lazy" onError={hide}/></button>` aligned by the existing own/other `items-end/start`. Local `const [lightbox, setLightbox] = useState(false)`; render `{lightbox && <ImageLightbox src={body.image_url} alt={body.alt} onClose={()=>setLightbox(false)} />}`. The existing non-image media_card branch (resource picker cards) stays unchanged.
- [ ] **Step 3:** Tests — add MessageBubble cases: a `media_card` with `body.kind:'image'` renders an `<img>` (not the boxed card / not "Open in Library"); a `media_card` WITHOUT `kind` still renders the existing card. (RTL: query `img` / role.) Keep existing 24 green.
- [ ] **Step 4: i18n** — add `chat.image.*` to en+zh: `uploading` ("Sending image…"/"图片发送中…"), `uploadError` ("Failed to send image"/"图片发送失败"), `onlyImages` ("Only images can be attached here"/"此处仅支持图片"), `dropHint` ("Drop images to send"/"拖拽图片以发送"), `close` ("Close"/"关闭"), `imageAlt` ("Image"/"图片"). Matching keys, valid JSON.
- [ ] **Step 5:** `npx vitest run components/chat/MessageBubble.test.tsx` + `npx tsc --noEmit` + `npm run build` + JSON parse. Commit — `feat(chat): inline image render + fullscreen lightbox`.

---

## Self-Review
**Spec coverage:** send images via click + paste + drag-drop (Discord/Feishu入口); inline image render + click-to-expand lightbox (大厂渲染); stored in the team resource library on NAS (大厂对象存储等价物 + 飞书式入库). Reuses `media_card` (no migration). 
**Deferrals:** non-image file attachments (this slice = images only; the paperclip can later route non-images to the resource flow); image dimensions/aspect-ratio placeholder (width/height backfilled async — use `object-cover` capped box for now, no layout shift handling); multi-image gallery grouping (each image = its own message); drag-to-reorder/captions; agent-sent images. The 附加媒体 ResourcePicker (existing-resource cards) is untouched.
**Storage note:** uploaded chat images ARE added to the team's资源库 (scope_id=teamId) — intentional, matches Feishu (chat files live in the team space) + reuses cover/serving/dedup. If the user later wants chat images excluded from the library browser, that's a separate filter.
**Verification:** build + unit + a real logged-in browser pass (paste an image → inline → click expands), then clean up the test user/data.

## Execution Handoff
Execute via superpowers:subagent-driven-development; final whole-branch review; **real logged-in UI verification** (per the early-visual-UX-pass rule); then ship (frontend-only → Vercel + merge → private).
