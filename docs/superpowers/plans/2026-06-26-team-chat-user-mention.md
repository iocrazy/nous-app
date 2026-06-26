# Team Chat — @People Mention + Unread Fanout Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development. Steps use `- [ ]`.

**Goal:** Let a user @-mention teammates (and chat-enabled agents) from the composer; mentioned members get a persisted, live-updating mention badge in the sidebar. Closes spec CHAT-UNREAD-03 (mention write-fanout) and the @people half of the mention UX.

**Architecture:** The composer detects `@<query>`, shows a dropdown of the current team's members + chat-enabled agents. Picking a **person** inserts their display name as text and records their `user_id`; picking an **agent** inserts the literal `@slug` (so the existing background agent-summon text parser still works — unchanged). On send, the page sends `body = { text, mention_user_ids }`. Backend `post_message` validates those ids are channel members (excluding the sender) and calls the existing `increment_mentions`, which bumps `channel_members.mention_count`. `get_my_channels` now returns `mention_count`; the sidebar renders it as a distinct badge; `mark_read` already resets it. Live cross-channel updates come from a global Supabase Realtime subscription to `channel_members` UPDATEs for the current user.

**Tech Stack:** FastAPI + db_engine; React 19 + Supabase Realtime + i18next. **No migration** — `channel_members.mention_count` exists (migration 320), `increment_mentions`/`mark_read` exist, `channel_members` is in the realtime publication with REPLICA IDENTITY FULL (migration 322).

## Global Constraints
- **Branch:** `feature/chat-user-mention` (off origin/master, which now includes the edit/delete feature).
- **Agent summon path is unchanged.** The background `dispatch_summons` parses agent slugs from `body.text` via `extract_agent_mentions`. This plan does NOT touch agent dispatch; agent mentions stay text-`@slug`. Only **user** mentions get the new structured `mention_user_ids`.
- **Mentions are member-scoped + exclude the sender.** Backend MUST filter `mention_user_ids` to actual channel members and drop the sender's own id before incrementing (never notify yourself; never bump a non-member).
- **mention_user_ids travels inside `body` jsonb** (no new request schema field). Backend reads `body.get("mention_user_ids")`, coerces to a list of strings, ignores if absent/empty/malformed (fail-soft: a bad mentions payload must never block sending the message).
- **Two-boundary model:** backend uses privileged db_engine + in-app validation; the global realtime subscription is RLS-guarded (a user only receives their own `channel_members` rows).
- **db_engine:** `from app.db import engine as db_engine`; reuse the `_bigint` coercion + existing query style in `chat_repository.py`.
- **Realtime:** the per-channel hook (`useChannelRealtime`) stays as-is. Add a SEPARATE global hook for `channel_members` UPDATE filtered to the current user — do not overload the per-channel channel.
- **Island UI:** zero emoji, no `zinc-*`; match the existing hardcoded-hex chat style. Mention badge visually distinct from the generic unread count (e.g. an amber `@n` pill vs the neutral unread dot).
- **i18n** for all visible text (`chat.*`, en+zh parity, valid JSON).
- **Verification:** backend `uv run pytest` per task + black/isort/flake8; frontend `npx tsc --noEmit` + `npm run build`.

## File Structure
- `backend/app/repositories/chat_repository.py` — add `mention_count` to `get_my_channels` SELECT; add `list_member_ids(channel_id) -> list[str]` (for membership filtering) if not derivable cheaply.
- `backend/app/services/chat_service.py` — in `post_message`, after the row is created, fan out user mentions.
- `backend/tests/test_chat_user_mention.py` — new tests.
- `frontend/types.ts` — `Channel.mentions: number`.
- `frontend/services/chatService.ts` — map `mentions` in `listChannels`.
- `frontend/components/chat/MentionDropdown.tsx` — new: people+agent suggestion list.
- `frontend/components/chat/Composer.tsx` — `@` detection + dropdown + mention tracking; `onSend(text, mentionUserIds)`.
- `frontend/components/chat/ChatSidebar.tsx` — render the mention badge.
- `frontend/pages/ChatPage.tsx` — fetch team members, pass to composer, build body with `mention_user_ids`, global mention realtime, badge state.
- `frontend/hooks/useMentionBadges.ts` — new global `channel_members` UPDATE subscription.
- `frontend/public/locales/{en,zh}.json` — `chat.mentionPeople.*` keys.

---

## Task 1: Backend — mention_count in channel list + fan-out in post_message

**Files:** Modify `backend/app/repositories/chat_repository.py`, `backend/app/services/chat_service.py`. Test: `backend/tests/test_chat_user_mention.py`.

**Interfaces (Produces):**
- `get_my_channels` rows gain `mention_count: int`.
- `post_message` (unchanged signature) now, as a side effect, increments mention_count for validly-mentioned members.
- (if needed) `async def list_member_ids(self, channel_id: int) -> list[str]`.

- [ ] **Step 1: Write failing tests.** In `backend/tests/test_chat_user_mention.py`, mock the repo for the service test + mock db_engine for the repo test (follow `backend/tests/test_chat_edit_delete.py` patterns). Tests:
  - (repo) `get_my_channels` SELECT contains `mention_count` and the returned dict has a `mention_count` key.
  - (service) `post_message` with `body={"text":"hi @bob","mention_user_ids":["U_bob","U_self","U_outsider"]}`: after send, `increment_mentions` is called once with the channel id and a list that (a) excludes the sender `U_self`, (b) excludes non-member `U_outsider`, (c) includes `U_bob`. Use a mocked `list_member_ids` / `is_member` returning membership.
  - (service) `post_message` with no `mention_user_ids` → `increment_mentions` NOT called.
  - (service) malformed `mention_user_ids` (e.g. a string, or list with non-str) → no crash, `increment_mentions` not called (or called with the cleaned subset). Assert the message is still returned.
  Run `cd backend && uv run pytest tests/test_chat_user_mention.py -v` → FAIL.

- [ ] **Step 2: Implement repo.** Add `cm.mention_count AS mention_count` (alias to match) to the `get_my_channels` SELECT (next to the existing `unread` expression at ~line 122). Add `list_member_ids(channel_id)` returning `[row["user_id"] ...]` from `SELECT user_id FROM channel_members WHERE channel_id = :cid` (use `db_engine.fetch_all` + `_bigint`).

- [ ] **Step 3: Implement service fan-out.** In `post_message`, after the message row is created (and only when `content_type == "text"`): read `raw = body.get("mention_user_ids")`; if it's a non-empty list, build `ids = [str(x) for x in raw if isinstance(x, str)]`; fetch members via `await self.repo.list_member_ids(channel_id)`; compute `targets = [u for u in set(ids) if u in members and u != user_id]`; if `targets`, `await self.repo.increment_mentions(channel_id, targets)`. Wrap the whole fan-out in a try/except that logs and swallows (a mention-fanout failure must not fail the send). Return the created row as before.

- [ ] **Step 4: Run tests.** `uv run pytest tests/test_chat_user_mention.py -v` → PASS.

- [ ] **Step 5: Lint + commit.** black/isort/flake8 on changed files. Commit — `feat(chat): user-mention fan-out + mention_count in channel list`.

---

## Task 2: Frontend types + service — surface mention_count

**Files:** Modify `frontend/types.ts`, `frontend/services/chatService.ts`.

**Interfaces (Produces):** `Channel.mentions: number`; `listChannels()` populates it.

- [ ] **Step 1:** In `types.ts`, add `mentions: number;` to the `Channel` interface (next to `unread`).
- [ ] **Step 2:** In `chatService.ts` `listChannels` (and `createChannel`'s returned object if it constructs a Channel), map `mention_count` from the API row to `mentions` (default `0`). Check how `unread` is currently mapped and mirror it; the backend `ChannelOut`/`get_my_channels` returns `mention_count` (Task 1) and `unread` — map both. If `createChannel`'s response lacks `mention_count`, default `mentions: 0`.
- [ ] **Step 3:** `cd frontend && npx tsc --noEmit` + `npm run build`. Commit — `feat(chat): Channel.mentions wired from API`.

---

## Task 3: Composer — @mention dropdown (people + agents)

**Files:** Create `frontend/components/chat/MentionDropdown.tsx`. Modify `frontend/components/chat/Composer.tsx`.

**Interfaces (Produces):**
- `MentionDropdown` props: `{ open: boolean; items: MentionCandidate[]; activeIndex: number; onPick: (c: MentionCandidate) => void }` where `type MentionCandidate = { kind: 'user'; id: string; label: string } | { kind: 'agent'; slug: string; label: string }`.
- `Composer` gains props: `members: { user_id: string; label: string }[]`, `agents: { slug: string; label: string }[]`, and changes `onSend` to `onSend(text: string, mentionUserIds: string[]) => void`.

- [ ] **Step 1: Build MentionDropdown.tsx.** A small absolutely-positioned list above the composer; renders each candidate (user: avatar-initial + label; agent: amber tag + label), highlights `activeIndex`, click → `onPick`. Island styling, zero emoji, i18n for any static text. Empty list → render nothing.

- [ ] **Step 2: Composer @-detection.** In `Composer.tsx`, track the textarea value + caret. On input, detect an active mention token: the substring from the last `@` (preceded by start/space and with no whitespace after it up to the caret). If present, set `mentionQuery` and compute `candidates` = members+agents whose label starts-with/includes the query (case-insensitive, cap ~8). Render `<MentionDropdown open={candidates.length>0 && mentionQuery!==null} ... />`. Keyboard: ArrowUp/Down move `activeIndex`, Enter/Tab picks the active candidate (and does NOT submit when the dropdown is open), Esc closes the dropdown.

- [ ] **Step 3: Picking + tracking.** On pick:
  - user → replace the `@query` token with `@${label} ` in the text; add `id` to a `mentionedUserIds` ref/state (dedup); record the label→id so a later edit that removes the text can be ignored (simplest: on send, re-resolve — keep only ids whose `@label` still appears in the final text).
  - agent → replace token with `@${slug} ` (literal slug, so backend text-parse summons it); do NOT add to mentionedUserIds.
  - Close the dropdown after pick.
- [ ] **Step 4: Send.** On submit, compute the final `mentionUserIds` = the tracked user ids whose `@label` substring is still present in the text (drop any the user deleted). Call `onSend(text, mentionUserIds)`. Clear text + tracking. Keep the existing empty-text guard.
- [ ] **Step 5:** `npx tsc --noEmit` + `npm run build`. Commit — `feat(chat): composer @mention dropdown (people + agents)`.

---

## Task 4: ChatPage + Sidebar + live badge + i18n

**Files:** Modify `frontend/pages/ChatPage.tsx`, `frontend/components/chat/ChatSidebar.tsx`, `frontend/public/locales/{en,zh}.json`. Create `frontend/hooks/useMentionBadges.ts`.

**Interfaces (Consumes):** Composer.onSend(text, mentionUserIds) (Task 3), Channel.mentions (Task 2).

- [ ] **Step 1: Member + agent sources.** In ChatPage, when a channel is active, fetch the current team's members via `teamService.fetchTeamMembers(selectedTeamId)` → map to `{ user_id, label: name ?? email }` (exclude the current user from the @people list — you don't @ yourself). Fetch chat-enabled agents via `aiLibraryService.listAgents()` filtered to `chat_permissions?.enabled === true` → `{ slug, label: name ?? slug }`. Store both in state. (Reuse the exact calls CreateGroupModal uses — inspect it.) Pass `members` + `agents` to `<Composer>`.

- [ ] **Step 2: Send with mentions.** Change the send handler to `(text, mentionUserIds) => { body = { text }; if (mentionUserIds.length) body.mention_user_ids = mentionUserIds; chatService.sendMessage(activeId, body, 'text'); ... }` reusing the existing append/dedupe path. (Preserve `sender_name` if the current code adds it to body.)

- [ ] **Step 3: useMentionBadges hook.** Create `frontend/hooks/useMentionBadges.ts`:
  ```ts
  export function useMentionBadges(userId: string | null, onChange: (channelId: string, mentionCount: number, lastReadSeq: number) => void): void
  ```
  Subscribe (null-guarded `getSupabaseClient()`) to `postgres_changes` UPDATE on `public.channel_members` filtered `user_id=eq.${userId}`; on payload, call `onChange(String(payload.new.channel_id), payload.new.mention_count, payload.new.last_read_seq)`. Cleanup via `removeChannel`. Mirror `useChannelRealtime`'s ref + lifecycle pattern.

- [ ] **Step 4: Wire badge state.** In ChatPage, call `useMentionBadges(currentUserId, (channelId, mentionCount) => setChannels(prev => prev.map(ch => ch.id === channelId ? { ...ch, mentions: mentionCount } : ch)))`. (mark-read already resets server-side; also locally set `mentions: 0` in the existing `scheduleMarkRead` success branch alongside `unread: 0`.)

- [ ] **Step 5: Sidebar badge.** In `ChatSidebar.tsx`, where each channel renders its unread indicator, also render a mention badge when `channel.mentions > 0`: a small amber pill showing `@{channel.mentions}` (or `@` if you prefer count-less), visually distinct from the neutral unread dot. i18n the aria/title.

- [ ] **Step 6: i18n.** Add to en.json + zh.json under `chat.mentionPeople`: `placeholder` (dropdown empty hint, optional), `you` ("You"), and a sidebar `mentionsBadgeTitle` ("{{count}} mentions"/"{{count}} 条提及"). Add any other static strings introduced. Matching key sets, valid JSON.

- [ ] **Step 7:** `npx tsc --noEmit` + `npm run build` + JSON parse check. Commit — `feat(chat): wire @mentions in ChatPage + live sidebar mention badge + i18n`.

---

## Self-Review
**Spec coverage:** CHAT-UNREAD-03 — @-mention write-fanout (only mentioned members' `mention_count` bumped, not every member) + live sidebar badge + reset on read. People mention UX delivered; agent mention reuses the existing text-parse summon path.
**Deferrals:** `@everyone`/`@channel`; mention highlighting inside the rendered message body (the bubble doesn't yet style `@name` — separate); notification/toast on being mentioned; mention autocomplete inside the tiptap `ChatInput` (ChatPage uses the simple Composer). Editing a message's mentions does not re-fan-out (send-time only).
**Security:** backend filters mentions to members and excludes the sender; the global realtime subscription is RLS-scoped so a user only sees their own `channel_members` rows.
**No migration:** all columns + publication already exist.
**Verification:** backend pytest (mocked) + frontend build; true E2E after deploy (@ a teammate in channel A from another account → their sidebar shows an amber mention badge on channel A live; opening it clears it).

## Execution Handoff
Execute via superpowers:subagent-driven-development; final whole-branch review; then ship (backend+frontend → CI → merge; wait for ACR deploy).
