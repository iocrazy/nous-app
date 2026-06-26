# Task 4 Report: ChatPage wiring + live sidebar mention badge + i18n

## Step 1 — Member + agent sources
- Added `membersForComposer` and `agentsForComposer` state to `ChatPage`.
- Added `useEffect` (deps: `[selectedTeamId, currentUserId]`) that calls `fetchTeamMembers(selectedTeamId)` and `aiLibraryService.listAgents()` in parallel (exact same calls as `CreateGroupModal` uses).
- Members mapped to `{ user_id, label: name ?? email ?? user_id }`, filtered to exclude `user_id === currentUserId`.
- Agents filtered to `chat_permissions?.enabled === true`, mapped to `{ slug, label: name ?? slug }`.
- Both arrays passed to `<Composer members={membersForComposer} agents={agentsForComposer} />`.

## Step 2 — Send handler updated to 2-arg form
- `handleSend(text, mentionUserIds)` now accepts the second arg from `Composer`.
- Body starts as `{ text }` and conditionally adds `mention_user_ids: mentionUserIds` when array is non-empty.
- Existing `appendMessage` / `sending` / error-toast path unchanged.

## Step 3 — `useMentionBadges` hook
- Created `frontend/hooks/useMentionBadges.ts`.
- Signature: `(userId: string | null, onChange: (channelId: string, mentionCount: number) => void): void`.
- Mirrors `useChannelRealtime` exactly: stable `cbRef`, null-guard on `userId` and `getSupabaseClient()`, single `channel` subscription to `postgres_changes` UPDATE on `public.channel_members` filtered `user_id=eq.${userId}`, cleanup via `supabase.removeChannel`.

## Step 4 — Badge state wired in ChatPage
- `useMentionBadges(currentUserId, (channelId, mentionCount) => setChannels(...))` installed after `useChannelRealtime` call.
- `scheduleMarkRead` success branch now sets `{ ...ch, unread: 0, mentions: 0 }` (previously only `unread: 0`), mirroring the server-side reset on mark-read.

## Step 5 — Sidebar mention badge
- `ChannelRow` in `ChatSidebar.tsx` now calls `useTranslation()`.
- When `channel.mentions > 0`, renders an amber pill (`bg-amber-400/15 text-amber-400`) showing `@{channel.mentions}`, placed after the neutral indigo unread count badge.
- `title` attribute uses `t('chat.mentionsBadgeTitle', { count: channel.mentions })` for accessibility.

## Step 6 — i18n
- `en.json`: added `"chat.mentionsBadgeTitle": "{{count}} mentions"`.
- `zh.json`: added `"chat.mentionsBadgeTitle": "{{count}} 条提及"`.
- Both files validated with `python3 -m json.tool`: valid.

## Verification
- `npx tsc --noEmit`: zero errors in changed files (ChatPage, ChatSidebar, useMentionBadges); pre-existing errors in unrelated files unchanged.
- `npm run build`: ✓ built in 11.42s — clean build.
- JSON parse check: both locale files valid.
