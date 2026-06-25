# Team Chat PHASE-1b — Frontend Chat UI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development. Steps use `- [ ]`.

**Goal:** A working team chat page wired to the PHASE-1 backend (`/api/v1/chat/*`): list my channels with unread badges, open a channel, load messages (keyset "load older"), send messages, receive new messages live via Supabase Realtime, and mark-read on view. Visuals match the already-approved island-UI mockups.

**Architecture:** A team-scoped route `/team/:teamId/chat` renders `ChatPage`, which holds state (selected channel, message list, sending). Data via a thin `chatService.ts` (REST + `getAuthHeaders()`). New messages arrive through a Supabase Realtime `postgres_changes` subscription on `channel_messages` filtered by the open channel (RLS already guards it). Presentational components (`ChatSidebar`, `MessageList`, `Composer`) are props-driven and translate the approved mockup markup into React.

**Tech Stack:** React 19 + TS + Tailwind (island tokens), react-router (team-scoped routes), `getSupabaseClient()` Realtime, i18next.

## Global Constraints
- **Visual source of truth:** the committed mockups in `docs/superpowers/specs/2026-06-25-team-chat-mockup.html` (dark group view: sidebar + message list + composer + media card) and `...-mockup-pages.html` (DM / agent / light). Implementers translate that exact markup/classes into React — do not invent new aesthetics. **Zero emoji** (the mockups' placeholder emoji are NOT to be copied), no `zinc-*`, island tokens only (`bg-island`, `border-line`, `text-ink-*`/`text-content*`, `indigo`, `amber` for agent), 18px island radius (`rounded-2xl`/`rounded-lg`).
- **BIGINT safety:** `channel_id`, `message id`, `seq`, `last_message_seq`, `last_read_seq` are Snowflake — treated as **strings** on the wire (`bigIntSafeFetch` in `supabaseClient.ts` auto-quotes 16+ digit numbers). Keep them `string` in TS; compare seq numerically via `Number(...)` only where ordering is needed (seq < 2^53 in practice, safe to Number() for compare/sort).
- **Auth:** REST calls use `await getAuthHeaders()` (from `services/parserService`) + `API_BASE` (`import.meta.env.VITE_API_URL || 'http://localhost:8080'`).
- **Realtime:** `getSupabaseClient()` (from `supabaseClient`), `.channel(name).on('postgres_changes', {event:'INSERT', schema:'public', table:'channel_messages', filter:`channel_id=eq.${id}`}, cb).subscribe(...)`; cleanup with `supabase.removeChannel(ref)` on channel switch/unmount (mirror `TaskManagerContext.tsx:595-642`).
- **i18n:** `useTranslation()` + `t('chat.*')`; add keys to `public/locales/en.json` + `zh.json`.
- **Toast:** `const { addToast } = useToast()` from `components/Toast`; `addToast(msg, 'success'|'error'|'info')`.
- **Verification:** no FE unit-test infra; each task verifies with `npx tsc --noEmit` (no NEW errors) + `npm run build`. Live E2E happens after the backend migrations are applied (post-merge) — note this; do not fake runtime verification.
- **Lint/build before commit.** Files kebab-case; components match existing `frontend/components` style.

## File Structure
- `frontend/types.ts` — `Channel`, `ChatMessage`, `ChatMessageBody` interfaces.
- `frontend/services/chatService.ts` — REST client.
- `frontend/components/chat/ChatSidebar.tsx` — channel list + unread.
- `frontend/components/chat/MessageList.tsx` + `MessageBubble.tsx` — message stream.
- `frontend/components/chat/Composer.tsx` — input + send.
- `frontend/pages/ChatPage.tsx` — container (state + data + realtime).
- `frontend/hooks/useChannelRealtime.ts` — Realtime subscription hook.
- `frontend/router.tsx` + `frontend/components/Sidebar.tsx` — route + nav entry.
- `frontend/public/locales/{en,zh}.json` — `chat.*` keys.

---

## Task 1: Types + chatService (REST)

**Files:** Modify `frontend/types.ts`; Create `frontend/services/chatService.ts`.

**Interfaces (Produces):** `Channel`, `ChatMessage` types; `chatService` with `listChannels()`, `createChannel(p)`, `addMembers(channelId, userIds)`, `listMessages(channelId, beforeSeq?, limit?)`, `sendMessage(channelId, body, contentType?, replyToId?)`, `markRead(channelId, lastReadSeq)`.

- [ ] **Step 1: Add types to `frontend/types.ts`**

```ts
export interface Channel {
  id: string;
  team_id: string;
  type: 'dm' | 'group' | 'public';
  history_mode: 'shared' | 'joined';
  name: string | null;
  topic: string | null;
  last_message_seq: string;
  unread: number;
  created_at: string;
}

export interface ChatMessage {
  id: string;
  channel_id: string;
  seq: string;
  sender_id: string | null;
  sender_type: 'user' | 'agent';
  content_type: 'text' | 'media_card' | 'task_card' | 'system';
  body: Record<string, unknown>;
  reply_to_id: string | null;
  edited_at: string | null;
  deleted_at: string | null;
  created_at: string;
}
```

- [ ] **Step 2: Create `frontend/services/chatService.ts`**

```ts
import { getAuthHeaders } from './parserService';
import type { Channel, ChatMessage } from '../types';

const API_BASE =
  ('VITE_API_URL' in import.meta.env ? import.meta.env.VITE_API_URL : '') ||
  'http://localhost:8080';

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const headers = await getAuthHeaders();
  const resp = await fetch(`${API_BASE}/api/v1${path}`, { ...init, headers });
  if (!resp.ok) {
    let detail = `${resp.status}`;
    try {
      detail = (await resp.json())?.detail ?? detail;
    } catch (err) {
      console.error('[chatService] error body parse failed', err);
    }
    throw new Error(detail);
  }
  return resp.json() as Promise<T>;
}

export const chatService = {
  listChannels: () => req<Channel[]>('/chat/channels'),

  createChannel: (p: {
    type: 'group' | 'dm' | 'public';
    team_id: string;
    name?: string | null;
    history_mode?: 'shared' | 'joined';
    member_ids?: string[];
  }) => req<Channel>('/chat/channels', { method: 'POST', body: JSON.stringify(p) }),

  addMembers: (channelId: string, userIds: string[]) =>
    req<{ added: number }>(`/chat/channels/${channelId}/members`, {
      method: 'POST',
      body: JSON.stringify({ user_ids: userIds }),
    }),

  listMessages: (channelId: string, beforeSeq?: string, limit = 30) => {
    const q = new URLSearchParams();
    if (beforeSeq) q.set('before_seq', beforeSeq);
    q.set('limit', String(limit));
    return req<ChatMessage[]>(`/chat/channels/${channelId}/messages?${q.toString()}`);
  },

  sendMessage: (
    channelId: string,
    body: Record<string, unknown>,
    contentType: 'text' | 'media_card' | 'task_card' = 'text',
    replyToId?: string,
  ) =>
    req<ChatMessage>(`/chat/channels/${channelId}/messages`, {
      method: 'POST',
      body: JSON.stringify({ content_type: contentType, body, reply_to_id: replyToId ?? null }),
    }),

  markRead: (channelId: string, lastReadSeq: string) =>
    req<{ ok: boolean }>(`/chat/channels/${channelId}/read`, {
      method: 'POST',
      body: JSON.stringify({ last_read_seq: Number(lastReadSeq) }),
    }),
};
```

- [ ] **Step 3:** `cd frontend && npx tsc --noEmit` — no new errors referencing chatService/Channel/ChatMessage. Commit — `feat(chat): frontend chat types + REST service`.

---

## Task 2: Presentational components (translate the mockup)

**Files:** Create `frontend/components/chat/ChatSidebar.tsx`, `MessageList.tsx`, `MessageBubble.tsx`, `Composer.tsx`.

**Interfaces (Produces):**
- `ChatSidebar({ channels, activeId, onSelect })`
- `MessageList({ messages, onLoadOlder, hasOlder, loadingOlder })` (renders ascending; `messages` passed newest-last)
- `MessageBubble({ message })`
- `Composer({ onSend, disabled, placeholder })`

- [ ] **Step 1: Build the components from the mockup.** Open `docs/superpowers/specs/2026-06-25-team-chat-mockup.html` and translate its sidebar, message rows, and composer into these React components — same classes/structure (island tokens, agent rows get the amber `AGENT` tag when `message.sender_type === 'agent'`, `content_type === 'media_card'` renders the media card block). Props-driven, no data fetching inside. Text via `t('chat.*')`. No emoji.

Key behaviors to implement (the rest is styling copied from the mockup):
- `ChatSidebar`: sections by `channel.type` (Groups = group/public, Direct Messages = dm). Each row shows name (or a DM peer label fallback), and an unread badge (`channel.unread > 0`) — indigo badge.
- `MessageBubble`: `sender_type==='agent'` → amber avatar + `AGENT` tag; `content_type==='media_card'` → card from `message.body` (`{title, image_url, fields:[{k,v}], ...}`); else `message.body.text` as plain text.
- `MessageList`: a top "Load older" button shown when `hasOlder` (calls `onLoadOlder`), messages rendered oldest→newest, auto-scroll to bottom on new last message.
- `Composer`: textarea + send button; Enter sends, Shift+Enter newline; calls `onSend(text)` and clears; `disabled` disables input + button.

- [ ] **Step 2:** `npx tsc --noEmit` + `npm run build` succeed. Commit — `feat(chat): chat presentational components from mockup`.

---

## Task 3: useChannelRealtime hook

**Files:** Create `frontend/hooks/useChannelRealtime.ts`.

**Interfaces (Produces):** `useChannelRealtime(channelId: string | null, onInsert: (m: ChatMessage) => void)` — subscribes to INSERTs on `channel_messages` for that channel; resubscribes on channelId change; cleans up.

- [ ] **Step 1: Implement** (mirror `TaskManagerContext.tsx:595-642`)

```ts
import { useEffect, useRef } from 'react';
import { getSupabaseClient } from '../supabaseClient';
import type { ChatMessage } from '../types';

export function useChannelRealtime(
  channelId: string | null,
  onInsert: (m: ChatMessage) => void,
) {
  const cbRef = useRef(onInsert);
  cbRef.current = onInsert;

  useEffect(() => {
    if (!channelId) return;
    const supabase = getSupabaseClient();
    const channel = supabase
      .channel(`chat-${channelId}`)
      .on(
        'postgres_changes',
        { event: 'INSERT', schema: 'public', table: 'channel_messages', filter: `channel_id=eq.${channelId}` },
        (payload) => cbRef.current(payload.new as ChatMessage),
      )
      .subscribe();
    return () => {
      supabase.removeChannel(channel);
    };
  }, [channelId]);
}
```

- [ ] **Step 2:** Confirm `getSupabaseClient` export name in `supabaseClient.ts` (adjust import if it's `getSupabaseClient`/default). `npx tsc --noEmit`. Commit — `feat(chat): channel Realtime subscription hook`.

---

## Task 4: ChatPage container (state + data + realtime)

**Files:** Create `frontend/pages/ChatPage.tsx`.

**Interfaces (Consumes):** chatService, the components (Task 2), useChannelRealtime (Task 3), `useParams` for teamId.

- [ ] **Step 1: Implement the container.** Responsibilities:
- On mount: `chatService.listChannels()` → state; auto-select first channel.
- On channel select: `chatService.listMessages(id)` (newest 30, DESC from API → reverse to ascending for display); store `messages`, compute `hasOlder = page.length === 30`.
- "Load older": `listMessages(id, oldestSeqInState, 30)`, prepend.
- Send: optimistic? No — call `chatService.sendMessage`, append returned message (Realtime will also deliver it to others; dedupe by `id`).
- Realtime: `useChannelRealtime(activeId, (m) => append if not already present by id)`.
- Mark-read: when a channel is open and messages load / new message arrives while focused, call `chatService.markRead(id, lastSeq)` (debounce ~1s) and zero its sidebar unread locally.
- Errors via `addToast(..., 'error')`.
- Layout: `<ChatSidebar/>` + `<MessageList/>` + `<Composer/>` in the island three/two-pane shell from the mockup. Right info panel collapsed by default (per earlier decision).

Dedupe rule: keep a `Set` of message ids; ignore inserts whose id is already present (handles the "sender appends + realtime echo" double).

- [ ] **Step 2:** `npx tsc --noEmit` + `npm run build`. Commit — `feat(chat): ChatPage container wiring service + realtime`.

---

## Task 5: Route + nav entry

**Files:** Modify `frontend/router.tsx` (add `/team/:teamId/chat` → `ChatPage`, lazy + SuspenseWrap like siblings); `frontend/components/Sidebar.tsx` (add a `MessageSquare` nav item → `navigate('/team/${currentTeam?.id}/chat')`, label `t('chat.title')`, active when on the chat route).

- [ ] **Step 1:** Add the route (match the existing lazy/SuspenseWrap pattern at `router.tsx:142-156`). Add the Sidebar item (match existing `SidebarItem` usage + island active styling). `npx tsc --noEmit` + `npm run build`.
- [ ] **Step 2:** Commit — `feat(chat): /chat route + sidebar nav entry`.

---

## Task 6: i18n keys

**Files:** Modify `frontend/public/locales/en.json` + `zh.json`.

- [ ] **Step 1:** Add a `chat` object with keys used by the components: `title`, `groups`, `directMessages`, `agents`, `send`, `messagePlaceholder`, `loadOlder`, `newGroup`, `noChannels`, `noChannelSelected`, `loading`. en = English, zh = Chinese. Verify both parse (`node -e "JSON.parse(...)"`). Commit — `feat(chat): i18n for chat UI (en/zh)`.

---

## Self-Review
**Spec coverage:** RT-03/04/05 (Realtime subscribe + unread + reconnect-ish) Tasks 3-4; UI-01~05 (island, sidebar consistency, i18n) Tasks 2,5,6; consumes the PHASE-1 REST API.
**Deferrals (no silent caps):** create-group modal, member picker, DM peer naming, typing/presence (broadcast), message edit/delete UI, media-card composer, agent DM (PHASE-2) — all deferred; list + open + send + live-receive + unread is the MVP slice. Reconnect gap-fill (seq range refetch) deferred — on reconnect the page can re-list; note it.
**No live E2E here:** migrations aren't applied locally, so verification is tsc + build + visual; true end-to-end after merge/deploy. Stated, not faked.

## Execution Handoff
Execute via superpowers:subagent-driven-development; final whole-branch review; then finishing-a-development-branch.
