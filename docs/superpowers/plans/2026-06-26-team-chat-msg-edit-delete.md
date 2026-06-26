# Team Chat — Message Edit / Delete Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development. Steps use `- [ ]`.

**Goal:** Let a user edit and soft-delete their own chat messages, with the change propagating live to other members. Closes spec CHAT-MSG-03 (edit) + CHAT-MSG-04 (delete).

**Architecture:** New backend `PATCH`/`DELETE` message endpoints → repository UPDATEs that gate on `sender_id = caller AND sender_type='user' AND deleted_at IS NULL` (fail-closed: a non-owner / agent message / already-deleted row matches nothing → `None` → 403/404). `channel_messages` is already in the realtime publication with `REPLICA IDENTITY FULL`, so the UPDATE fires a postgres_changes event; the frontend adds an UPDATE subscription and replaces the message in place. Edited messages show "(edited)"; deleted messages render a tombstone. **No migration** — `edited_at` / `deleted_at` columns already exist (migration 320).

**Tech Stack:** FastAPI + db_engine (privileged) + in-app membership gate; React 19 + Supabase Realtime + i18next.

## Global Constraints
- **Branch:** `feature/chat-msg-edit-delete` (off origin/master).
- **Two-boundary model:** backend uses `db_engine` (privileged) and enforces ownership/membership in-app; RLS still guards the frontend Realtime path. The edit/delete UPDATEs MUST include `sender_id = :uid AND sender_type = 'user'` in the WHERE so the DB itself rejects cross-user edits even though we call privileged.
- **Soft delete only:** DELETE sets `deleted_at = now()`; never physically removes the row (seq integrity, thread `reply_to_id`). `list_messages` already filters `deleted_at IS NULL` for history loads; the live tombstone is a frontend concern driven by the realtime UPDATE payload.
- **Edit scope:** only `content_type='text'` messages are editable (the composer only produces text + cards; cards are not editable this slice). The repo gate adds `content_type = 'text'`.
- **Phase columns are owned by this feature** (`body`, `edited_at`, `deleted_at` on a user's own message) — unrelated to the task_tracking trigger discipline (that's a different table). No trigger interaction here.
- **db_engine usage:** `from app.db import engine as db_engine`; methods `fetch_one`/`execute_returning_one`; named `:params`; coerce snowflake string IDs to BIGINT with the existing `_bigint()` helper in chat_repository (asyncpg int8 codec is strict — reuse the same coercion the other methods use).
- **Realtime:** reuse the existing single channel subscription in `useChannelRealtime` — add UPDATE on the same `channel_messages`/`channel_id` filter, do NOT open a second supabase channel.
- **Island UI:** zero emoji, no `zinc-*`. Match the existing hardcoded-hex style of sibling chat components (known module debt — do not introduce a different token scheme).
- **i18n** for all visible text under the `chat.*` namespace (en + zh, matching key sets, valid JSON).
- **Verification:** backend `cd backend && uv run pytest tests/<new test> -v` per task; frontend `npx tsc --noEmit` + `npm run build`. Backend lint before commit: `black`/`isort`/`flake8` on changed `.py`.

## File Structure
- `backend/app/repositories/chat_repository.py` — add `edit_message`, `soft_delete_message`.
- `backend/app/services/chat_service.py` — add `edit_message`, `delete_message` (membership + ownership orchestration).
- `backend/app/api/chat_router.py` — add `PATCH`/`DELETE /channels/{cid}/messages/{mid}` + `MessageEdit` schema.
- `backend/tests/test_chat_edit_delete.py` — new repo/service tests.
- `frontend/services/chatService.ts` — add `editMessage`, `deleteMessage`.
- `frontend/hooks/useChannelRealtime.ts` — add `onUpdate` subscription.
- `frontend/pages/ChatPage.tsx` — `updateMessage` helper, wire realtime UPDATE, pass `currentUserId` + edit/delete handlers down.
- `frontend/components/chat/MessageList.tsx` — thread `currentUserId` + handlers to bubbles.
- `frontend/components/chat/MessageBubble.tsx` — hover edit/delete actions (owner-gated), inline edit, "(edited)" marker, deleted tombstone.
- `frontend/public/locales/{en,zh}.json` — `chat.edit*` / `chat.delete*` keys.

---

## Task 1: Repository — edit_message + soft_delete_message

**Files:** Modify `backend/app/repositories/chat_repository.py`. Test: `backend/tests/test_chat_edit_delete.py`.

**Interfaces (Produces):**
```python
async def edit_message(self, channel_id: int, message_id: int, sender_id: str, body: dict[str, Any]) -> Optional[dict[str, Any]]
async def soft_delete_message(self, channel_id: int, message_id: int, sender_id: str) -> Optional[dict[str, Any]]
```
Both return the updated row dict (same column set `list_messages` returns, plus `from_bot_agent_id`) or `None` when no row matched the gate.

- [ ] **Step 1: Write failing tests.** In `backend/tests/test_chat_edit_delete.py`, mock `db_engine.execute_returning_one` (follow the mocking pattern already used in existing `backend/tests/test_chat_*` repo tests — inspect one first). Test: (a) `edit_message` issues an UPDATE whose SQL contains `sender_id = :sender_id`, `sender_type = 'text'`-guard… actually `content_type = 'text'`, `deleted_at IS NULL`, sets `edited_at = now()` and `body = :body`, and returns the row; (b) returns `None` when the driver returns no row; (c) `soft_delete_message` sets `deleted_at = now()` with the same ownership gate and returns the row; (d) snowflake string IDs are coerced via `_bigint`. Assert on the SQL text + bound params captured from the mock.

- [ ] **Step 2: Run tests, verify they fail.** `cd backend && uv run pytest tests/test_chat_edit_delete.py -v` → FAIL (methods undefined).

- [ ] **Step 3: Implement both methods.** Mirror the style of `send_message`/`mark_read` in the same file (same `_bigint` coercion, same `execute_returning_one`, same returned column list). Example shape:
```python
async def edit_message(self, channel_id, message_id, sender_id, body):
    row = await db_engine.execute_returning_one(
        """
        UPDATE channel_messages
           SET body = :body, edited_at = now()
         WHERE id = :mid AND channel_id = :cid
           AND sender_id = :sender_id AND sender_type = 'user'
           AND content_type = 'text' AND deleted_at IS NULL
        RETURNING id, channel_id, seq, sender_id, sender_type, content_type,
                  body, reply_to_id, from_bot_agent_id, edited_at, deleted_at, created_at
        """,
        {"body": json.dumps(body), "mid": _bigint(message_id),
         "cid": _bigint(channel_id), "sender_id": sender_id},
    )
    return _row_to_message(row) if row else None
```
Use whatever JSON-bind + row-normalization helpers the file already uses for `body` (check how `send_message` binds `body` and shapes its return — match it exactly; do not invent `_row_to_message` if the file inlines the dict). `soft_delete_message` is the same WHERE gate, `SET deleted_at = now()`, no body param.

- [ ] **Step 4: Run tests, verify pass.** `cd backend && uv run pytest tests/test_chat_edit_delete.py -v` → PASS.

- [ ] **Step 5: Lint + commit.** `black backend/app/repositories/chat_repository.py backend/tests/test_chat_edit_delete.py && isort … && flake8 …`. Commit — `feat(chat): repo edit/soft-delete message with ownership gate`.

---

## Task 2: Service + Router — PATCH/DELETE endpoints

**Files:** Modify `backend/app/services/chat_service.py`, `backend/app/api/chat_router.py`. Test: append to `backend/tests/test_chat_edit_delete.py`.

**Interfaces (Consumes):** `ChatRepository.edit_message` / `soft_delete_message` (Task 1).
**Interfaces (Produces):**
```python
# service
async def edit_message(self, channel_id: int, user_id: str, message_id: int, body: dict) -> dict
async def delete_message(self, channel_id: int, user_id: str, message_id: int) -> dict
# router
PATCH  /channels/{channel_id}/messages/{message_id}  body: MessageEdit{body: dict}  -> MessageOut
DELETE /channels/{channel_id}/messages/{message_id}                                  -> MessageOut
```

- [ ] **Step 1: Write failing tests.** Service-level: mock the repository. (a) `edit_message` calls `is_member`; if not member → raises `PermissionError`; (b) if repo returns `None` (not owner / not text / deleted) → raises a not-found/permission error (use the SAME exception type the existing service uses for "not allowed" — inspect `post_message`); (c) on success returns the row. Same three for `delete_message`. Run: `uv run pytest tests/test_chat_edit_delete.py -v` → FAIL.

- [ ] **Step 2: Implement service methods.** In `chat_service.py`, mirror `post_message`'s membership check (`if not await self.repo.is_member(...): raise <same error>`). Then call the repo method; if it returns `None`, raise the same error class the service uses elsewhere for forbidden/not-found (do not leak whether the row exists vs. caller-not-owner — one error). Return the dict.

- [ ] **Step 3: Add schema + routes.** In `chat_router.py`: add `class MessageEdit(BaseModel): body: dict[str, Any]`. Add:
```python
@router.patch("/channels/{channel_id}/messages/{message_id}", response_model=MessageOut)
async def edit_message(channel_id: int, message_id: int, payload: MessageEdit, auth: AuthDep):
    row = await service.edit_message(channel_id, auth.user_id, message_id, payload.body)
    return _to_message_out(row)   # reuse whatever the POST route uses to build MessageOut

@router.delete("/channels/{channel_id}/messages/{message_id}", response_model=MessageOut)
async def delete_message(channel_id: int, message_id: int, auth: AuthDep):
    row = await service.delete_message(channel_id, auth.user_id, message_id)
    return _to_message_out(row)
```
Map the service's forbidden/not-found error to `HTTPException(status_code=403 or 404)` using the SAME exception-to-HTTP mapping the existing routes use (inspect how `post_message` route maps `PermissionError`). Reuse the existing `MessageOut` builder. **Do not** trigger `_summon_runner` on edit/delete.

- [ ] **Step 4: Run tests.** `uv run pytest tests/test_chat_edit_delete.py -v` → PASS.

- [ ] **Step 5: Lint + commit.** black/isort/flake8 on changed files. Commit — `feat(chat): PATCH/DELETE message endpoints (membership + ownership gated)`.

---

## Task 3: Frontend service + realtime UPDATE subscription

**Files:** Modify `frontend/services/chatService.ts`, `frontend/hooks/useChannelRealtime.ts`.

**Interfaces (Produces):**
```typescript
chatService.editMessage(channelId: string, messageId: string, body: Record<string, unknown>): Promise<ChatMessage>
chatService.deleteMessage(channelId: string, messageId: string): Promise<ChatMessage>
// hook gains a second callback:
useChannelRealtime(channelId: string | null, onInsert: (m: ChatMessage) => void, onUpdate?: (m: ChatMessage) => void): void
```

- [ ] **Step 1: chatService methods.** Add `editMessage` → `PATCH /chat/channels/{channelId}/messages/{messageId}` with `{ body }`; `deleteMessage` → `DELETE /chat/channels/{channelId}/messages/{messageId}`. Mirror the existing `sendMessage` (auth headers, error handling, JSON parse, returns `ChatMessage`).

- [ ] **Step 2: Realtime UPDATE.** In `useChannelRealtime.ts`, add an `onUpdate` param (optional) with a stable ref like the existing `onInsert`. On the SAME supabase channel object, chain a second `.on('postgres_changes', { event: 'UPDATE', schema: 'public', table: 'channel_messages', filter: \`channel_id=eq.${channelId}\` }, payload => updateCbRef.current?.(payload.new as ChatMessage))` before `.subscribe()`. Keep the null-guards on `getSupabaseClient()`.

- [ ] **Step 3: Verify.** `cd frontend && npx tsc --noEmit` (no new errors) + `npm run build`. Commit — `feat(chat): chatService edit/delete + realtime UPDATE subscription`.

---

## Task 4: ChatPage — updateMessage + wire handlers + current user

**Files:** Modify `frontend/pages/ChatPage.tsx`.

**Interfaces (Consumes):** chatService.editMessage/deleteMessage, useChannelRealtime onUpdate (Task 3). **Produces:** passes `currentUserId`, `onEdit`, `onDelete` into `<MessageList>`.

- [ ] **Step 1: Current user id.** Derive `currentUserId` from the session (inspect how other pages read it — likely `getSupabaseClient().auth.getUser()` once in an effect into state, or an existing auth context/hook; reuse that pattern, do not invent a new auth source). Store in state.

- [ ] **Step 2: updateMessage helper.** Add:
```ts
const updateMessage = useCallback((m: ChatMessage) => {
  setMessages(prev => prev.map(x => x.id === m.id ? m : x));
}, []);
```
Pass `updateMessage` as the new `onUpdate` arg to `useChannelRealtime(activeId, appendMessage, updateMessage)`.

- [ ] **Step 3: Edit/delete handlers.**
```ts
const handleEditMessage = useCallback(async (messageId: string, text: string) => {
  if (!activeIdRef.current) return;
  try {
    const updated = await chatService.editMessage(activeIdRef.current, messageId, { text });
    updateMessage(updated);
  } catch (err) { console.error(err); addToast(t('chat.editError'), 'error'); }
}, [updateMessage, t]);

const handleDeleteMessage = useCallback(async (messageId: string) => {
  if (!activeIdRef.current) return;
  try {
    const updated = await chatService.deleteMessage(activeIdRef.current, messageId);
    updateMessage(updated);
  } catch (err) { console.error(err); addToast(t('chat.deleteError'), 'error'); }
}, [updateMessage, t]);
```
(Preserve `body` keys other than `text` if the message had them — for text messages body is just `{text, sender_name?}`; merge to keep `sender_name`: build `{ ...originalBody, text }`. Look up the original message from `messages` by id to get its body.)

- [ ] **Step 4: Pass down.** Add `currentUserId={currentUserId}`, `onEdit={handleEditMessage}`, `onDelete={handleDeleteMessage}` to `<MessageList>`.

- [ ] **Step 5: Verify.** `npx tsc --noEmit` + `npm run build`. Commit — `feat(chat): wire edit/delete handlers + realtime update in ChatPage`.

---

## Task 5: MessageList + MessageBubble — hover actions, inline edit, tombstone, i18n

**Files:** Modify `frontend/components/chat/MessageList.tsx`, `frontend/components/chat/MessageBubble.tsx`, `frontend/public/locales/{en,zh}.json`.

**Interfaces (Consumes):** `currentUserId`, `onEdit(messageId, text)`, `onDelete(messageId)` from ChatPage (Task 4).

- [ ] **Step 1: MessageList prop passthrough.** Extend `MessageListProps` with `currentUserId: string | null`, `onEdit: (id: string, text: string) => void`, `onDelete: (id: string) => void`. Pass each to the per-message `<MessageBubble>`.

- [ ] **Step 2: MessageBubble props + owner gate.** Extend `MessageBubbleProps` with `currentUserId`, `onEdit`, `onDelete`. Compute `const isOwn = message.sender_type === 'user' && message.sender_id === currentUserId; const isDeleted = !!message.deleted_at;`.

- [ ] **Step 3: Deleted tombstone.** When `isDeleted`, render a muted italic line `t('chat.deleted')` ("This message was deleted") instead of body/card/actions. Keep avatar + timestamp.

- [ ] **Step 4: Hover actions.** When `isOwn && !isDeleted`, render an edit (Pencil) + delete (Trash2 from lucide-react) action cluster that appears on hover (`group`/`group-hover:opacity-100`, matching the existing island hover pattern in the module). Edit enters inline-edit mode; delete calls `onDelete(message.id)` after a confirm (use `window.confirm(t('chat.confirmDelete'))` — simplest, no new modal this slice).

- [ ] **Step 5: Inline edit mode.** Local `const [editing, setEditing] = useState(false)` + `const [draft, setDraft] = useState('')`. Entering edit sets `draft` from `message.body.text`. Render a textarea (island-styled) with Save / Cancel; Save calls `onEdit(message.id, draft.trim())` then `setEditing(false)` (skip if unchanged/empty); Cancel resets. Enter saves, Shift+Enter newline, Esc cancels (mirror composer key handling).

- [ ] **Step 6: Edited marker.** When `message.edited_at && !isDeleted`, append a muted `· {t('chat.edited')}` ("edited") next to the timestamp.

- [ ] **Step 7: i18n.** Add to en.json + zh.json: `chat.edit` ("Edit"/"编辑"), `chat.delete` ("Delete"/"删除"), `chat.edited` ("edited"/"已编辑"), `chat.deleted` ("This message was deleted"/"此消息已删除"), `chat.save` ("Save"/"保存"), `chat.cancel` ("Cancel"/"取消"), `chat.confirmDelete` ("Delete this message?"/"删除这条消息？"), `chat.editError` ("Failed to edit message"/"编辑消息失败"), `chat.deleteError` ("Failed to delete message"/"删除消息失败"). Matching key sets; both valid JSON.

- [ ] **Step 8: Verify.** `npx tsc --noEmit` + `npm run build`. Commit — `feat(chat): message edit/delete UI (hover actions, inline edit, tombstone) + i18n`.

---

## Self-Review
**Spec coverage:** CHAT-MSG-03 (edit own message, "(edited)" marker, live propagation) + CHAT-MSG-04 (soft-delete own message, tombstone, live propagation). Ownership enforced in DB WHERE (defense-in-depth) + service membership gate.
**Deferrals:** editing media/task cards (text only); delete-for-everyone vs delete-for-me (only one mode: soft-delete visible as tombstone to all); admin/owner moderation delete of others' messages (separate — needs role gate); edit history. Confirm via `window.confirm` not a custom modal.
**Realtime note:** `channel_messages` already has REPLICA IDENTITY FULL + is in the publication (migration 322), so UPDATE payloads carry the full new row — no schema change needed.
**Verification:** backend pytest (mocked db_engine) + frontend build; true E2E after deploy (edit a message in one browser, see "(edited)" appear live in another; delete → tombstone live).

## Execution Handoff
Execute via superpowers:subagent-driven-development; final whole-branch review; then ship (backend+frontend → CI → merge; wait for ACR deploy before flipping anything).
