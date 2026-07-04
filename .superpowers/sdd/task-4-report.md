# Task 4 Report: ConversationRepository

## Status: DONE

## Commit Hash
`e91ed53a`

## Files Produced
- `backend/app/repositories/conversation_repository.py` (262 lines)
- `backend/tests/test_conversation_repository.py` (472 lines)

## Method List (Public Interface)

| Method | Signature |
|--------|-----------|
| `create_conversation` | `(*, creator_id, scope_id, type, name, history_mode, member_ids) -> dict` |
| `add_members` | `(*, conversation_id, user_ids) -> int` |
| `is_member` | `(*, conversation_id, user_id) -> bool` |
| `is_team_member` | `(*, team_id, user_id) -> bool` |
| `conversation_scope_id` | `(*, conversation_id) -> int|None` |
| `get_my_conversations` | `(user_id) -> list[dict]` |
| `list_member_ids` | `(conversation_id) -> list[str]` |
| `send_message` | `(*, conversation_id, sender_id, sender_type, type, body, parent_id, from_agent_id=None) -> dict` |
| `list_messages` | `(*, conversation_id, before_seq, limit) -> list[dict]` |
| `mark_read` | `(*, conversation_id, user_id, last_read_seq) -> None` |
| `add_agent_member` | `(*, conversation_id, agent_id, added_by) -> None` |
| `is_agent_member` | `(*, conversation_id, agent_id) -> bool` |
| `list_conversation_agent_ids` | `(*, conversation_id) -> list[str]` |
| `recent_messages` | `(*, conversation_id, limit=20) -> list[dict]` |
| `increment_mentions` | `(*, conversation_id, user_ids) -> None` |
| `edit_message` | `(conversation_id, message_id, sender_id, body) -> dict|None` |
| `soft_delete_message` | `(conversation_id, message_id, sender_id) -> dict|None` |
| `get_conversation` | `(*, conversation_id) -> dict|None` |
| `add_attachments` | `(*, message_id, generated_media_ids: list[int]) -> None` |
| `get_conversation_repository` | `() -> ConversationRepository` singleton |

## Mocked Test Run

```
uv run pytest tests/test_conversation_repository.py -v -m "not integration"
11 passed, 1 deselected in 0.42s
```

Tests covered:
1. `test_send_message_seq_via_update_conversations` — UPDATE conversations first, INSERT uses type/parent_id/from_agent_id
2. `test_send_message_advances_sender_read_cursor_for_user` — 3rd execute updates conversation_members
3. `test_send_message_no_read_cursor_advance_for_agent` — only 2 execute calls when agent
4. `test_is_member_filters_member_type_user` — SQL has member_type='user'
5. `test_add_agent_member_inserts_member_type_agent` — SQL has member_type='agent', targets conversation_members not agent_channels
6. `test_list_conversation_agent_ids_filters_member_type_agent` — SQL has member_type='agent'
7. `test_add_attachments_inserts_one_row_per_id_with_ord` — 3 execute calls, ord=0/1/2
8. `test_add_attachments_empty_list_is_noop` — 0 execute calls
9. `test_create_conversation_inserts_title_column_not_name` — INSERT SQL has `title`; returned dict has `name` key
10. `test_is_agent_member_filters_member_type_agent` — SQL has member_type='agent'
11. `test_get_conversation_repository_returns_singleton` — r1 is r2

## Real-DB Smoke Run

```
INTEGRATION_DATABASE_URL="postgresql://postgres:postgres@127.0.0.1:54322/postgres" \
  uv run pytest tests/test_conversation_repository.py -v -m integration
1 passed, 11 deselected in 0.68s
```

`test_smoke_send_3_messages_seq_1_2_3` passed against local asyncpg (127.0.0.1:54322):
- Created conversation with scope_id=285274231427073, creator_id='ca5e636f-6e60-414c-be54-110acf8c45c7'
- Sent 3 messages → seq=[1, 2, 3] ✓
- `list_messages(before_seq=None)` → [3, 2, 1] desc ✓
- `list_messages(before_seq=3)` → [2, 1] keyset pagination ✓
- `is_member` returned True ✓
- Conversation cleaned up via DELETE on exit

## name/title Bridge

INSERT uses column `title`, bind parameter `:name`; RETURNING aliases `title AS name`:
```sql
INSERT INTO public.conversations (scope_id, type, history_mode, title, created_by)
VALUES (:scope_id, :type, :history_mode, :name, :creator)
RETURNING id, scope_id, type, history_mode, title AS name, topic, last_seq, created_at
```
Returned dict always has key `name`, never `title`. All SELECT/RETURNING also alias `title AS name`.

## CAST(:before AS bigint) Wrap

In `list_messages`:
```sql
AND (CAST(:before AS bigint) IS NULL OR seq < CAST(:before AS bigint))
```
Prevents asyncpg AmbiguousParameterError when `before_seq=None`, proven by the real-DB smoke test.

## Lint Result

- black: 1 file reformatted (test file), 1 file left unchanged
- isort: 1 file fixed (test file)
- flake8: clean (no output)

## Task 4 Review Fixup (2026-06-30) — commit 4bf0ecc2

### Changes applied

**Fix 1 — Test hardening** (`backend/tests/test_conversation_repository.py`):
`test_send_message_advances_sender_read_cursor_for_user` now asserts `assert "'user'" in third_sql`
in addition to the existing `assert "member_type" in third_sql`. This closes the regression gap where
a future bug filtering `member_type='agent'` on the read-cursor UPDATE would have passed silently.

**Fix 2 — Lint/clarity** (`backend/app/repositories/conversation_repository.py`):
In `add_attachments`, the loop variable `ord` (shadowing the Python built-in) was renamed to `ord_val`.
The DB column name `:ord` in the SQL is unchanged; only the Python variable changed.

### Test run

```
uv run pytest tests/test_conversation_repository.py -v
11 passed, 1 skipped in 0.42s
```

(Smoke test skipped — no `INTEGRATION_DATABASE_URL`. All 11 SQL-shape unit tests passed including
the strengthened `'user'` assertion.)

### Lint result

```
uv run black ... && uv run isort ... && uv run flake8 ...
```
- black: 2 files left unchanged
- isort: no changes
- flake8: clean (no output, `ord` shadow warning gone)

### Commit

`4bf0ecc2` — `test(chat): harden member_type assertion + rename ord shadow (Task 4 review)`

## Key Transformation Decisions

1. **Dual-FK membership**: All user-membership WHERE clauses include `member_type = 'user' AND user_id = :uid`. Agent membership uses `member_type = 'agent' AND agent_id = :agent_id`.
2. **add_agent_member**: Replaced `agent_channels` table with `conversation_members (member_type='agent', agent_id, added_by)`. ON CONFLICT DO NOTHING relies on `uq_conversation_members` unique index.
3. **role scalar**: Creator inserted with `role='owner'`, others `role='member'` (no `CAST(... AS text[])` needed — it's a plain TEXT column).
4. **add_attachments**: All INSERTs in a single `eng.begin()` transaction with `enumerate(generated_media_ids)` for monotonic `ord`.
5. **_bigint coercion**: Applied to all BIGINT binds (conversation_id, message_id, generated_media_id, scope_id). `ord` is INTEGER — no coercion.
