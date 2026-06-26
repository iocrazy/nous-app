# Team Chat — Reconnect Gap-Fill Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development. Steps use `- [ ]`.

**Goal:** When the Supabase Realtime connection drops and recovers (network blip, laptop sleep, tab backgrounded), messages sent during the gap are missed (realtime only pushes live events). On reconnect — and on tab-focus / network-online — fetch the messages newer than what we have and merge them in. Closes spec CHAT-RT-04 (reconnect gap-fill).

**Architecture:** `useChannelRealtime`'s `.subscribe()` already receives a status callback; expose an `onResubscribe` that fires when the channel returns to `SUBSCRIBED` after a prior error/close (i.e. a *re*-connect, not the first connect). ChatPage runs a `gapFill()` that forward-paginates from the highest seq it already has (reusing the existing `before_seq` keyset endpoint, descending, stopping once it reaches known territory) and appends the fresh messages through the existing dedupe path. The same `gapFill()` is also triggered by `window` `online` and `document` `visibilitychange→visible`. **Frontend-only**, no backend/API change, no migration.

**Tech Stack:** React 19 + Supabase Realtime + existing `chatService.listMessages`.

## Global Constraints
- **Branch:** `feature/chat-reconnect-gapfill` (off origin/master, which now has edit/delete + mentions).
- **Frontend-only.** Reuse `chatService.listMessages(channelId, beforeSeq?, limit?)` (descending keyset). Do NOT add a backend "after seq" endpoint this slice.
- **seq is a Snowflake string.** Compare with `BigInt(a) > BigInt(b)` — never numeric `>` (precision) and never string `>` (lexical). Guard against non-numeric with a try/catch or a helper.
- **Dedupe is authoritative via the existing `seenIds` path.** `gapFill` appends only messages not already seen; appended messages must keep the array sorted ascending by seq (same invariant the rest of ChatPage relies on).
- **Bounded work.** Cap gap-fill at a small number of pages (e.g. 5 × 30 = 150) so a very long offline period can't trigger an unbounded fetch loop; if the gap is larger, `log` it (console.warn) — the user can scroll/reload for the rest. Never silently imply full coverage.
- **No double-fire storms.** Debounce/guard `gapFill` so overlapping triggers (online + visibility + resubscribe firing together) don't run concurrently for the same channel (a simple in-flight ref).
- **Only the active channel.** gap-fill targets the currently open channel (`activeIdRef.current`); per-channel unread for other channels still comes from `get_my_channels` on next load + the realtime badge hooks.
- **Island UI:** if you add any "reconnecting…" affordance, zero emoji, no `zinc-*`, i18n. (Optional — a silent gap-fill with no UI is acceptable for this slice.)
- **Verification:** `npx tsc --noEmit` (no NEW errors) + `npm run build` per task.

## File Structure
- `frontend/hooks/useChannelRealtime.ts` — expose `onResubscribe?: () => void` fired on reconnect (status callback).
- `frontend/pages/ChatPage.tsx` — `gapFill()` (forward paginate + merge), wire to onResubscribe + `online` + `visibilitychange`, in-flight guard.
- (optional) `frontend/public/locales/{en,zh}.json` — only if a visible string is added.

---

## Task 1: useChannelRealtime — onResubscribe on reconnect

**Files:** Modify `frontend/hooks/useChannelRealtime.ts`.

**Interfaces (Produces):** `useChannelRealtime(channelId, onInsert, onUpdate?, onResubscribe?)`. `onResubscribe` fires when the realtime channel transitions to `SUBSCRIBED` AFTER a previous non-subscribed state in the same effect lifetime (a reconnect), NOT on the initial subscribe.

- [ ] **Step 1:** Read the current hook. It calls `.subscribe()` with no status handler. Add a stable `resubRef` (mirror `cbRef`/`updateCbRef`). Inside the effect, keep a local `hasSubscribed = false` flag. Change `.subscribe()` to `.subscribe((status) => { if (status === 'SUBSCRIBED') { if (hasSubscribed) resubRef.current?.(); else hasSubscribed = true; } })`. (So the very first SUBSCRIBED arms the flag; any subsequent SUBSCRIBED — which Supabase emits after CHANNEL_ERROR/TIMED_OUT/CLOSED auto-rejoin — fires `onResubscribe`.) Keep all existing `.on(...)` handlers and null-guards. The new param is optional so existing callers compile.
- [ ] **Step 2:** `cd frontend && npx tsc --noEmit` (no new errors) + `npm run build`. Commit — `feat(chat): useChannelRealtime fires onResubscribe on realtime reconnect`.

---

## Task 2: ChatPage — gapFill + triggers

**Files:** Modify `frontend/pages/ChatPage.tsx`. (Add i18n only if you introduce visible text.)

**Interfaces (Consumes):** useChannelRealtime onResubscribe (Task 1); existing `chatService.listMessages`, `appendMessage`/`seenIds`, `messagesRef`/`activeIdRef`, `scheduleMarkRead`.

- [ ] **Step 1: seq compare helper.** Add a small module-scope helper:
```ts
const seqGt = (a: string, b: string): boolean => {
  try { return BigInt(a) > BigInt(b); } catch { return false; }
};
```
- [ ] **Step 2: gapFill.** Add an in-flight ref `const gapFillInFlight = useRef(false);` and:
```ts
const gapFill = useCallback(async () => {
  const channelId = activeIdRef.current;
  if (!channelId || gapFillInFlight.current) return;
  const list = messagesRef.current;          // ascending
  const lastSeq = list.length ? list[list.length - 1].seq : '0';
  gapFillInFlight.current = true;
  try {
    let cursor: string | undefined = undefined;   // start from latest
    const fresh: ChatMessage[] = [];
    for (let page = 0; page < 5; page++) {
      const batch = await chatService.listMessages(channelId, cursor, 30); // DESC
      if (channelId !== activeIdRef.current) return;   // channel switched mid-fetch
      if (!batch.length) break;
      // batch is newest→oldest; collect those strictly newer than lastSeq
      const newer = batch.filter(m => seqGt(m.seq, lastSeq));
      fresh.push(...newer);
      const oldestInBatch = batch[batch.length - 1].seq;
      if (!seqGt(oldestInBatch, lastSeq)) break;   // reached known territory
      cursor = oldestInBatch;                       // page further back
      if (page === 4 && seqGt(oldestInBatch, lastSeq)) {
        console.warn('[chat] gapFill: gap exceeds 150 messages; reload to see the rest');
      }
    }
    if (fresh.length) {
      // ascending, dedup via seenIds (appendMessage already guards)
      fresh.sort((a, b) => (seqGt(a.seq, b.seq) ? 1 : -1));
      for (const m of fresh) appendMessage(m);
      const maxSeq = fresh[fresh.length - 1].seq;
      scheduleMarkRead(channelId, maxSeq);
    }
  } catch (err) {
    console.error('[chat] gapFill failed', err);
  } finally {
    gapFillInFlight.current = false;
  }
}, [appendMessage, scheduleMarkRead]);
```
(Adjust the exact names to whatever ChatPage already uses — `appendMessage`, `scheduleMarkRead`, `messagesRef`, `activeIdRef` were confirmed present. If `messagesRef` does not exist, add a ref kept in sync with `messages` like the edit/delete feature did.)
- [ ] **Step 3: Wire onResubscribe.** Pass `gapFill` as the 4th arg: `useChannelRealtime(activeId, appendMessage, updateMessage, gapFill)`.
- [ ] **Step 4: Wire online + visibility.** In an effect (mount-scoped):
```ts
useEffect(() => {
  const onOnline = () => { void gapFill(); };
  const onVisible = () => { if (document.visibilityState === 'visible') void gapFill(); };
  window.addEventListener('online', onOnline);
  document.addEventListener('visibilitychange', onVisible);
  return () => {
    window.removeEventListener('online', onOnline);
    document.removeEventListener('visibilitychange', onVisible);
  };
}, [gapFill]);
```
- [ ] **Step 5:** `npx tsc --noEmit` + `npm run build`. Commit — `feat(chat): reconnect gap-fill (forward paginate missed messages on resubscribe/online/focus)`.

---

## Self-Review
**Spec coverage:** CHAT-RT-04 — after a realtime drop/recover (or tab-focus / network-online), missed messages in the active channel are fetched forward from the highest known seq and merged via the existing dedupe path; mark-read advances to the newest.
**Deferrals:** gap-fill for non-active channels (their unread/mention badges already reconcile on next channel load + the badge hooks); gaps larger than 150 messages (logged, user reloads); a visible "reconnecting…" banner (silent this slice). No "after_seq" backend endpoint (reused keyset paging).
**Correctness:** BigInt seq comparison (no precision/lexical bugs); in-flight guard prevents concurrent runs; channel-switch check aborts a stale fetch; dedupe via seenIds means re-delivered live messages won't double-insert.
**Verification:** build-level; true E2E after deploy (open a channel, kill wifi 30s while another account posts, restore → missed messages appear without reload).

## Execution Handoff
Execute via superpowers:subagent-driven-development; final whole-branch review; then ship (frontend-only → Vercel + merge).
