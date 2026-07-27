# Team Chat — Send Media Card Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development. Steps use `- [ ]`.

**Goal:** Let users drop a resource-library item into a chat as a rich media card (the Nous differentiator: discuss your media in chat). MessageBubble already renders `content_type === 'media_card'`; backend already accepts it. This adds the composer affordance: pick a resource → send a media_card message.

**Architecture:** The Composer's "attach media" button opens a `ResourcePicker` (lists the current team's resources, searchable). On select, the page builds a media_card body `{ resource_id, title, image_url, fields }` from the resource and sends it via `chatService.sendMessage(channelId, body, 'media_card')`, reusing the existing send/append path. Thumbnail URL reuses the existing resource-thumbnail logic (from ResourceCard / CompactMediaCard).

**Tech Stack:** React 19 + TS + Tailwind (island tokens), `resourceService.fetchResources`, `chatService.sendMessage`, i18next.

## Global Constraints
- **Branch:** `feature/team-chat-media-card` (off origin/master — has PHASE-0/1/2 + create-group UI).
- **Frontend-only** (backend already accepts `media_card`): ships via Vercel + admin-merge (GitHub Actions CI is currently billing-blocked; do NOT add backend changes).
- **media_card body shape** MessageBubble expects (`frontend/components/chat/MessageBubble.tsx`): `{ title?: string; image_url?: string; fields?: { title: string; value: string }[] }`. ALSO include `resource_id` for future "open in library". Build exactly this shape.
- **Thumbnail URL:** reuse the EXACT thumbnail-URL logic the resource library already uses (ResourceCard.tsx / CompactMediaCard.tsx) — do not invent a new URL scheme (media URLs are auth-gated). If thumbnails need a token, reuse whatever those components do.
- **Team scoping:** the picker lists resources visible to the user in the current team (`useTeamContext().selectedTeamId`) via the existing `fetchResources` filter — match how the resource library scopes.
- **Island UI:** zero emoji, no `zinc-*`. (Known module debt: chat components use hardcoded hex; match siblings for consistency — do not introduce a different token style.)
- **Verification:** `npx tsc --noEmit` + `npm run build` per task; visual via mockup (the media-card in `2026-06-25-team-chat-mockup.html`). Live E2E after deploy.
- **i18n** for visible text.

## File Structure
- `frontend/components/chat/ResourcePicker.tsx` — modal/popover listing + searching current-team resources (new).
- `frontend/components/chat/Composer.tsx` — wire the existing attach-media button to open the picker; expose `onSendMedia?(resource)` OR let it call back to the page.
- `frontend/pages/ChatPage.tsx` — host the picker state; build the media_card body + send via chatService.
- `frontend/public/locales/{en,zh}.json` — `chat.mediaCard.*` keys.

---

## Task 1: ResourcePicker component (list + search current-team resources)

**Files:** Create `frontend/components/chat/ResourcePicker.tsx`. Inspect `ResourceCard.tsx`/`CompactMediaCard.tsx` for the thumbnail-URL helper + `resourceService.fetchResources` signature first.

**Interfaces (Produces):** `<ResourcePicker open teamId onClose onSelect(resource) />` where `resource` is the chosen resource object (with id, filename, thumbnail/file_path, mime/type, size). Default export.

- [ ] **Step 1:** Open `frontend/services/resourceService.ts` (`fetchResources` ~line 562) to learn its params + return shape, and `ResourceCard.tsx` (or `CompactMediaCard.tsx`) to learn how it derives the thumbnail image URL from a resource. Document the thumbnail-URL helper to reuse.
- [ ] **Step 2:** Build `ResourcePicker.tsx`: when `open`, fetch resources for `teamId` (reuse `fetchResources` with the team scope the resource library uses; cap to a reasonable page, e.g. 50, with a search box that filters by filename — server-side if `fetchResources` supports a query param, else client-side over the fetched page with a note). Render a grid/list of resources (thumbnail + filename + type) using the reused thumbnail URL; clicking one calls `onSelect(resource)` then `onClose()`. Loading + empty + error (toast) states. Island styling matching the chat module. i18n `t('chat.mediaCard.*')`.
- [ ] **Step 3:** `cd frontend && npx tsc --noEmit` (no new errors referencing ResourcePicker) + `npm run build`. Commit — `feat(chat): ResourcePicker for attaching media to chat`.

---

## Task 2: Composer — wire attach-media button to the picker

**Files:** Modify `frontend/components/chat/Composer.tsx`.

**Interfaces (Produces):** Composer gains `onAttachMedia?: () => void` (called when the user clicks the existing attach/media icon button). Keep Composer presentational — the page owns picker state and the actual send.

- [ ] **Step 1:** Open `Composer.tsx`; find the existing media/attach icon button (per the mockup the composer toolbar has attach + media-card icons). Add an `onAttachMedia?: () => void` prop and wire it to that button's onClick. Do not change the text-send behavior. tsc clean.
- [ ] **Step 2:** Commit — `feat(chat): composer attach-media hook`.

---

## Task 3: ChatPage — host picker, build + send media_card, i18n

**Files:** Modify `frontend/pages/ChatPage.tsx`, `frontend/public/locales/{en,zh}.json`.

**Interfaces (Consumes):** ResourcePicker (Task 1), Composer.onAttachMedia (Task 2), chatService.sendMessage.

- [ ] **Step 1:** In ChatPage: add `const [showPicker, setShowPicker] = useState(false)`. Pass `onAttachMedia={() => setShowPicker(true)}` to `<Composer>`. Render `<ResourcePicker open={showPicker} teamId={selectedTeamId} onClose={() => setShowPicker(false)} onSelect={handleSendMedia} />`.
- [ ] **Step 2:** Implement `handleSendMedia(resource)`: build the media_card body:
  ```ts
  const body = {
    resource_id: String(resource.id),
    title: resource.filename ?? resource.name ?? 'Media',
    image_url: <thumbnail url via the reused helper>,
    fields: [
      { title: t('chat.mediaCard.type'), value: <mime/type> },
      { title: t('chat.mediaCard.size'), value: <human size, if available> },
    ].filter(f => f.value),
  };
  ```
  Then `const msg = await chatService.sendMessage(activeChannelId, body, 'media_card')` and append it the SAME way text sends are appended (reuse the existing send path: dedupe by id, push to messages). Errors → toast. Close the picker.
  - Reuse the existing send/append/dedupe logic in ChatPage rather than duplicating — if the text-send handler is a function, factor the "append a returned message" part so both text and media_card go through it.
- [ ] **Step 3:** i18n: add `chat.mediaCard` keys to en.json + zh.json: `title` ("Attach media"/"添加素材"), `search` ("Search resources…"), `empty` ("No resources"), `loadError`, `sendError`, `type` ("Type"/"类型"), `size` ("Size"/"大小"). Matching key sets, valid JSON.
- [ ] **Step 4:** `npx tsc --noEmit` + `npm run build` + JSON parse check. Commit — `feat(chat): send media card from resource picker + i18n`.

---

## Self-Review
**Spec coverage:** CHAT-MSG-06 (unified card schema — title/image/fields) + CHAT-MSG-08 (resource → media_card) now sendable from UI (were ◑/☐). The card renders via the existing MessageBubble media_card branch.
**Deferrals:** "Open in Library" / Download action buttons on the card (MSG-07 interactive buttons stays ⊘/deferred), agent sending media cards, video preview. Just the static card + thumbnail this slice.
**Iron-law note (agents):** unchanged — this is human-sent media cards; agent resource access still goes through the PHASE-2 scoped path.
**Verification:** build + visual; true E2E after deploy (attach a resource → card appears in the channel).

## Execution Handoff
Execute via superpowers:subagent-driven-development; final review; then ship (frontend-only → Vercel + admin-merge given CI billing block).
