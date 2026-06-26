# Team Chat — Typing Indicator + Presence Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development. Steps use `- [ ]`.

**Goal:** Show "X is typing…" while teammates type, and an online indicator for members currently viewing a channel. Closes spec CHAT-RT-03 (typing + presence). Uses Supabase Realtime **broadcast** (typing) + **presence** (online) — ephemeral, no DB writes, no migration.

**Architecture:** A new `useChannelPresence(channelId, me)` hook opens a SEPARATE realtime channel `chat-presence-${channelId}` (distinct from the `chat-${channelId}` postgres_changes channel so concerns stay isolated). It (a) tracks Supabase **presence** to expose the set of online user ids, and (b) listens for `broadcast` `typing` events to expose the set of currently-typing user ids (each with a self-expiring ~4s timeout). The composer fires a throttled `onTyping()` as the user types; ChatPage calls the hook's `sendTyping()` which broadcasts `{ user_id, name }`. ChatPage renders the existing `TypingIndicator` with the typing names, and shows an online dot in the header.

**Tech Stack:** React 19 + Supabase Realtime (presence + broadcast) + i18next. Frontend-only.

## Global Constraints
- **Branch:** `feature/chat-typing-presence` (off origin/master with A+B+C).
- **Frontend-only, ephemeral.** No DB, no migration, no backend. Typing + presence are realtime-only; nothing persists.
- **Separate realtime channel** `chat-presence-${channelId}` — do NOT add presence/broadcast onto the existing `useChannelRealtime` postgres_changes channel (keep the message-delivery channel focused; mixing risks resubscribe/gap-fill interactions).
- **Self-expiring typing.** A received typing event marks that user typing for ~4s; a fresh event resets the timer; on expiry the name disappears. Never show your OWN typing. Clear all on channel switch/unmount.
- **Throttle outbound typing** to at most ~1 broadcast / 2s while actively typing (don't broadcast per keystroke).
- **Presence identity.** Track presence keyed by the current user id with `{ user_id, name }`. Online set = distinct user ids present. Exclude self from the "others online" display if you show a roster; a self "you're connected" is not needed.
- **Graceful when realtime is unavailable.** Null-guard `getSupabaseClient()`; if null, the hook is a no-op (returns empty sets + no-op `sendTyping`). No crashes, no console spam.
- **Island UI:** zero emoji, no `zinc-*`; match existing chat hex styling. Reuse the existing `frontend/components/chat/TypingIndicator.tsx` (extend it to accept the typing text/names).
- **i18n** for visible text (en+zh parity, valid JSON).
- **Verification:** `npx tsc --noEmit` (no NEW errors) + `npm run build` per task.

## File Structure
- `frontend/hooks/useChannelPresence.ts` — new: presence + typing broadcast/receive.
- `frontend/components/chat/TypingIndicator.tsx` — extend to render names ("Alice is typing…" / "Alice and Bob are typing…" / "Several people are typing…").
- `frontend/components/chat/Composer.tsx` — add throttled `onTyping?: () => void` fired on input.
- `frontend/pages/ChatPage.tsx` — use the hook, wire composer onTyping → sendTyping, render TypingIndicator with typing names, show an online indicator in the channel header.
- `frontend/public/locales/{en,zh}.json` — `chat.typing.*` keys.

---

## Task 1: useChannelPresence hook

**Files:** Create `frontend/hooks/useChannelPresence.ts`.

**Interfaces (Produces):**
```ts
export interface ChannelPresence {
  onlineUserIds: string[];     // distinct user ids currently present
  typingUsers: { user_id: string; name: string }[];  // currently typing (others only), self-expiring
  sendTyping: () => void;      // throttled broadcast of the current user typing
}
export function useChannelPresence(
  channelId: string | null,
  me: { user_id: string; name: string } | null,
): ChannelPresence
```

- [ ] **Step 1: Build the hook.**
  - Null-guard: if `!channelId || !me || !getSupabaseClient()` → return `{ onlineUserIds: [], typingUsers: [], sendTyping: () => {} }` (stable no-op).
  - In an effect keyed on `[channelId, me?.user_id]`: create `const ch = supabase.channel('chat-presence-' + channelId, { config: { presence: { key: me.user_id } } })`.
  - Presence: `ch.on('presence', { event: 'sync' }, () => { const state = ch.presenceState(); setOnlineUserIds(Object.keys(state)); })`. On `'join'`/`'leave'` you can also recompute from `presenceState()`.
  - Typing broadcast receive: `ch.on('broadcast', { event: 'typing' }, ({ payload }) => { if (payload.user_id === me.user_id) return; addOrRefreshTyping(payload); })`.
  - `ch.subscribe(async (status) => { if (status === 'SUBSCRIBED') await ch.track({ user_id: me.user_id, name: me.name }); })`.
  - Cleanup: clear all per-user typing timers, `supabase.removeChannel(ch)`.
  - **Typing state with timers:** keep a `Map<user_id, timeoutId>` in a ref. `addOrRefreshTyping({user_id,name})`: clear any existing timer for that id, set a 4s timer that removes the user from `typingUsers` state, and upsert the user into `typingUsers` state. On unmount/channel-switch, clear all timers.
  - **sendTyping (throttled):** keep a `lastSentRef` timestamp; `sendTyping = useCallback(() => { const now = Date.now(); if (now - lastSentRef.current < 2000) return; lastSentRef.current = now; channelRef.current?.send({ type: 'broadcast', event: 'typing', payload: { user_id: me.user_id, name: me.name } }); }, [me?.user_id, me?.name])`. (Use a ref to the channel so the callback stays stable.)
  - NOTE: `Date.now()` is fine in app runtime code (the Date.now restriction applies only to Workflow scripts, not app code).
- [ ] **Step 2:** `cd frontend && npx tsc --noEmit` (no new errors) + `npm run build`. Commit — `feat(chat): useChannelPresence hook (presence + typing broadcast)`.

---

## Task 2: TypingIndicator names + Composer onTyping + ChatPage wiring + i18n

**Files:** Modify `frontend/components/chat/TypingIndicator.tsx`, `frontend/components/chat/Composer.tsx`, `frontend/pages/ChatPage.tsx`, `frontend/public/locales/{en,zh}.json`.

**Interfaces (Consumes):** `useChannelPresence` (Task 1).

- [ ] **Step 1: TypingIndicator names.** Extend `TypingIndicator` to accept `names: string[]` (default `[]`). When `names.length === 0` render nothing (or just the dots if a caller wants the bare animation — but ChatPage will only render it when someone is typing). Render the animated dots + a label via i18n: 1 name → `t('chat.typing.one', { name })`; 2 → `t('chat.typing.two', { a, b })`; ≥3 → `t('chat.typing.many')`. Keep the existing dot animation + island styling.
- [ ] **Step 2: Composer onTyping.** Add `onTyping?: () => void`. Call it on every input change (the hook throttles, so no throttle needed here) — but only when the textarea has non-empty content (don't broadcast typing for an empty field / on blur-clear). Do not change existing behavior.
- [ ] **Step 3: ChatPage wiring.**
  - Build `me = currentUserId ? { user_id: currentUserId, name: <current user's display name> } : null`. (Derive the name from the existing auth/user source; if only an id is available, fall back to the id — acceptable.)
  - `const { onlineUserIds, typingUsers, sendTyping } = useChannelPresence(activeId, me);`
  - Pass `onTyping={sendTyping}` to `<Composer>`.
  - Render `<TypingIndicator names={typingUsers.map(u => u.name)} />` just above the composer (only renders when non-empty).
  - Show an online indicator in the channel header: e.g. a small green dot + `t('chat.typing.online', { count })` where count = `onlineUserIds.length` (or online members excluding self). Keep it subtle, island-styled.
- [ ] **Step 4: i18n.** Add to en.json + zh.json under `chat.typing`: `one` ("{{name}} is typing…"/"{{name}} 正在输入…"), `two` ("{{a}} and {{b}} are typing…"/"{{a}} 和 {{b}} 正在输入…"), `many` ("Several people are typing…"/"多人正在输入…"), `online` ("{{count}} online"/"{{count}} 人在线"). Matching key sets, valid JSON.
- [ ] **Step 5:** `npx tsc --noEmit` + `npm run build` + JSON parse check. Commit — `feat(chat): typing indicator + online presence in chat UI + i18n`.

---

## Self-Review
**Spec coverage:** CHAT-RT-03 — live "X is typing…" via realtime broadcast (throttled out, self-expiring in) + online presence count via Supabase presence. Ephemeral, no DB.
**Deferrals:** per-member online dots in the sidebar/member list (header count only this slice); "last seen" timestamps (presence is live-only); typing in the tiptap `ChatInput` (ChatPage uses `Composer`); read receipts (separate). 
**Robustness:** separate realtime channel so it can't interfere with message delivery or reconnect gap-fill; null-guarded no-op when realtime unavailable; all timers cleared on channel switch/unmount (no leaks, no cross-channel bleed).
**Verification:** build-level; true E2E after deploy (two accounts in one channel: A types → B sees "A is typing…" that disappears ~4s after A stops; both show as online).

## Execution Handoff
Execute via superpowers:subagent-driven-development; final whole-branch review; then ship (frontend-only → Vercel + merge).
