# Team Chat — Frontend Unit Tests Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development. Steps use `- [ ]`.

**Goal:** Add vitest unit tests for the NEW Team Chat epic frontend code, which shipped with only build-level verification. Backend chat is already well-covered (test_chat_service / _router / _edit_delete / _user_mention / _agent_broadcast / _channel_agent_turn / _mention_parser). This closes the frontend test gap.

**Architecture:** Pure test addition — NO source changes unless a test surfaces a real bug (if it does, fix the source + note it). vitest + jsdom + globals + `@testing-library/react` (RTL), setup `frontend/tests/setup.ts`. Service tests mock `fetch`; hook tests use fake timers + a faked `getSupabaseClient`; component tests use RTL `render`.

**Tech Stack:** vitest 4 + jsdom + RTL + i18next (already initialized in tests/setup — verify; if `t()` returns keys in tests, assert on keys or the interpolated args, not English).

## Global Constraints
- **Branch:** `feature/chat-tests` (off origin/master).
- **Test-only, behavior-neutral:** do not change chat source files. EXCEPTION: if a test reveals a genuine bug, fix the source minimally + call it out in the report (don't write a test that asserts buggy behavior). If a pure helper isn't exported (e.g. `seqGt` in ChatPage), prefer testing it via the public surface; only export it if that's the only sane way (and note the export as the one source change).
- **Match existing idioms:** copy the mock setup from `frontend/services/resourceService.test.ts` (fetch stub, module mocks) and a hook test like `frontend/hooks/useResourceSearch.test.ts`, and a component test like `frontend/components/chat/ChatInput.test.tsx`. Use `vi.useFakeTimers()` for timer/throttle logic.
- **Deterministic:** no real network, no real timers (fake them), no `Date.now` flakiness (advance fake timers). Each test file runnable in isolation: `cd frontend && npx vitest run <file>`.
- **Coverage targets (the behaviors that matter, not line %):** the mapping/transform logic, the security-relevant gates (owner gate, anti-self-mention), the bug-prone bits the reviews caught (word-boundary mention match, timer self-expiry, throttle, dedupe).
- **i18n:** tests assert on i18n KEYS or interpolation values (the test i18n may not load English). Verify how existing chat component tests assert text.
- **Verification per task:** `cd frontend && npx vitest run <new file(s)>` green + `npx tsc --noEmit` (no new errors). Do NOT run the whole suite each task (slow) — run the new files; a final task runs the full chat subset.

## File Structure (new test files)
- `frontend/services/chatService.test.ts`
- `frontend/components/chat/Composer.test.tsx`
- `frontend/hooks/useChannelPresence.test.ts`
- `frontend/components/chat/MessageBubble.test.tsx`
- `frontend/components/chat/TypingIndicator.test.tsx`
- `frontend/components/chat/MentionDropdown.test.tsx`

---

## Task 1: chatService.test.ts

**Files:** Create `frontend/services/chatService.test.ts`. First read `frontend/services/chatService.ts` (the `req` helper, `toChannel` mapping, all methods) + `resourceService.test.ts` for the fetch-stub idiom.

- [ ] **Step 1:** Mock `./parserService` `getAuthHeaders` (→ `{}`), set `VITE_API_URL` or mock the API_BASE source, and stub `globalThis.fetch`. Tests:
  - `listChannels` → GETs `/chat/channels`; maps each row's `mention_count` → `mentions` (and defaults `mentions:0` when absent). Assert the returned `Channel[]` has `mentions` numbers.
  - `createChannel` → POSTs `/chat/channels` with the payload JSON; maps `mention_count`→`mentions` on the returned channel (default 0).
  - `sendMessage(channelId, body, 'media_card', replyToId)` → POSTs `/chat/channels/{id}/messages` with `{content_type:'media_card', body, reply_to_id}`; default contentType `'text'`, default no reply_to.
  - `editMessage` → PATCH `/chat/channels/{id}/messages/{mid}` with `{body}`. `deleteMessage` → DELETE same path.
  - `markRead` → POST `/chat/channels/{id}/read` with `{last_read_seq: Number(seq)}` (assert numeric coercion).
  - `addMembers`/`addAgent`/`listMessages` (query string incl. `before_seq`/`limit`).
  - Error path: `!ok` response → `req` throws an Error with the parsed `detail`.
- [ ] **Step 2:** `cd frontend && npx vitest run services/chatService.test.ts` green + `npx tsc --noEmit`. Commit — `test(chat): chatService unit tests`.

---

## Task 2: Composer.test.tsx (@mention logic — the bug-prone core)

**Files:** Create `frontend/components/chat/Composer.test.tsx`. Read `Composer.tsx` (the `_detectMention` regex, `handlePick`, the still-present word-boundary filter in `handleSend`, the keyboard handler, `onTyping`) + `ChatInput.test.tsx` for RTL idiom.

- [ ] **Step 1:** RTL render `<Composer onSend members agents onTyping />` with members `[{user_id:'U1',label:'Alice'},{user_id:'U2',label:'Alice Smith'}]` + agents `[{slug:'bot',label:'Bot'}]`. Tests:
  - Typing `@Al` opens `MentionDropdown` with the matching candidates (Alice, Alice Smith).
  - Picking "Alice" (user) inserts `@Alice ` into the textarea and, on submit, `onSend` is called with `mentionUserIds` containing `U1`.
  - Picking the agent inserts `@bot ` and does NOT add an id to `mentionUserIds`.
  - **Word-boundary (the #895 review bug):** pick BOTH "Alice" and "Alice Smith"; delete the standalone `@Alice ` token leaving only `@Alice Smith`; on submit `mentionUserIds` contains ONLY `U2` (NOT U1) — `@Alice` must not match inside `@Alice Smith`.
  - Empty/whitespace text → `onSend` NOT called.
  - `onTyping` fires on non-empty input change, NOT on an empty field.
  - Enter with dropdown CLOSED → submits; Enter with dropdown OPEN → does NOT submit (picks the active candidate). Esc closes dropdown without submitting.
  (Drive input via `fireEvent.change`/`input` on the textarea + `fireEvent.keyDown`. If the still-present filter needs the literal `@label` in the value, set the textarea value to the final string before submit.)
- [ ] **Step 2:** `npx vitest run components/chat/Composer.test.tsx` green + tsc. Commit — `test(chat): Composer @mention dropdown + word-boundary mention tests`.

---

## Task 3: useChannelPresence.test.ts (timers + throttle + null-guard)

**Files:** Create `frontend/hooks/useChannelPresence.test.ts`. Read `useChannelPresence.ts` + `useResourceSearch.test.ts` (hook + fake-timer idiom). Use `renderHook` from `@testing-library/react`.

- [ ] **Step 1:** Mock `../supabaseClient` `getSupabaseClient` to return a fake channel object with `.on()` (chainable, captures the broadcast+presence handlers), `.subscribe()` (invokes its status cb with `'SUBSCRIBED'`), `.track()`, `.presenceState()`, and a `.send()` spy; `supabase.removeChannel` spy. Use `vi.useFakeTimers()`. Tests:
  - **Null-guard:** `useChannelPresence(null, me)` and `useChannelPresence('c1', null)` return stable `{onlineUserIds:[], typingUsers:[], sendTyping}` and never create a channel.
  - **Typing receive + self-expiry:** simulate a broadcast `typing` event for another user → `typingUsers` includes them; advance fake timers 4000ms → they're removed.
  - **Own typing ignored:** a broadcast event with `payload.user_id === me.user_id` → `typingUsers` stays empty.
  - **Refresh resets timer:** two events for the same user 3s apart → still present at 3s, removed 4s after the SECOND.
  - **sendTyping throttle:** calling `sendTyping()` twice within 2000ms → `channel.send` called once; after advancing >2000ms, a third call sends again.
  - **Presence sync:** invoke the `presence/sync` handler with a `presenceState` of two keys → `onlineUserIds` has both.
  - **Cleanup:** unmount → `removeChannel` called + pending timers cleared (no act warnings / no setState-after-unmount).
- [ ] **Step 2:** `npx vitest run hooks/useChannelPresence.test.ts` green + tsc. Commit — `test(chat): useChannelPresence timers/throttle/null-guard tests`.

---

## Task 4: MessageBubble + TypingIndicator + MentionDropdown component tests

**Files:** Create `frontend/components/chat/MessageBubble.test.tsx`, `TypingIndicator.test.tsx`, `MentionDropdown.test.tsx`. Read each component + check how `ChatInput.test.tsx` asserts i18n text (key vs English).

- [ ] **Step 1: MessageBubble.test.tsx** (RTL). `currentUserId='U1'`. Cases:
  - text message renders `body.text`; agent message (`sender_type:'agent'`) shows the AGENT tag.
  - media_card renders the card (title + fields).
  - deleted (`deleted_at` set) → renders the tombstone (`chat.deleted`), NO edit/delete actions, NO body.
  - edited (`edited_at` set, not deleted) → shows the edited marker (`chat.edited`).
  - **owner gate:** own text message (sender_id===currentUserId, sender_type 'user') → hover cluster has edit + delete; a non-own message → neither; own MEDIA_CARD → delete present but NO edit (edit gated to `content_type==='text'`).
  - clicking delete calls `window.confirm` (mock it true) → `onDelete(id)`; entering edit + Save calls `onEdit(id, newText)` (and is a no-op when unchanged/empty).
- [ ] **Step 2: TypingIndicator.test.tsx**. `names=[]` and `show` falsy → renders null. `names=['A']` → `chat.typing.one`. `names=['A','B']` → `chat.typing.two`. `names` of 3 → `chat.typing.many`. `show` true with empty names → renders the bare dots (no label). (Assert on i18n key/args or the dot elements.)
- [ ] **Step 3: MentionDropdown.test.tsx**. `open=false` or empty items → null. open with items → renders each candidate; `activeIndex` row has the active styling; clicking a row → `onPick(candidate)`; a user candidate shows an avatar initial, an agent candidate shows the amber AGENT treatment.
- [ ] **Step 4:** `npx vitest run components/chat/MessageBubble.test.tsx components/chat/TypingIndicator.test.tsx components/chat/MentionDropdown.test.tsx` green + tsc. Commit — `test(chat): MessageBubble/TypingIndicator/MentionDropdown component tests`.

---

## Self-Review
**Coverage:** the new chat epic frontend — service mapping + request shapes, the @mention word-boundary logic (the #895 bug), presence timer/throttle/null-guard, and the message-bubble owner gate + tombstone/edited rendering. These are the transform + security-relevant + bug-prone surfaces.
**Deferrals (note explicitly, don't silently skip):** `useChannelRealtime` / `useMentionBadges` (supabase postgres_changes mocking — lower value, can add later); `ChatPage` container + `gapFill` (heavy integration — `seqGt` is module-private; test via a small extract only if cheap); `CreateGroupModal` / `ChatSidebar` / `ResourcePicker` (mostly presentational — covered indirectly); **E2E (Playwright)** — needs a running app + backend; local stack is blocked this session, so E2E is deferred to a Vercel-preview/prod pass.
**Verification:** per-file `vitest run` + a final full chat-subset run; tsc clean.

## Execution Handoff
Execute via superpowers:subagent-driven-development; light per-task review (test files — check assertions are real, not tautological); then ship (frontend-only → Vercel + merge → flip private).
