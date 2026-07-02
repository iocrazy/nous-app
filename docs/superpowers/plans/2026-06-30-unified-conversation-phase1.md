# Unified Conversation — Phase 1 Implementation Plan (team chat on canonical tables + images)

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development. Steps use `- [ ]`.
> **Spec:** `docs/superpowers/specs/2026-06-30-unified-conversation-design.md` (read it first). This plan is **Phase 1 only**.

**Goal:** Stand up the canonical `conversations`/`messages`/`conversation_members`/`message_attachments`/`message_refs` tables and move **team chat** onto them (agent-as-member, typed events, keyset-by-seq), behind a flag, including the chat-image feature on the new store. 1:1 AI chat (Phase 2) and legacy-table drops (Phase 3) are out of scope.

**Architecture:** Mirror the proven team-chat stack (privileged `db_engine` + in-app membership checks; RLS only guards the direct frontend Realtime path; seq allocated app-side in-txn via `UPDATE conversations SET last_seq+1 RETURNING`). New `conversation_repository.py` / `conversation_service.py` / `/conversations/*` router are ports of `chat_repository.py` / `chat_service.py` / `chat_router.py` with the table/column renames + dual-FK membership + agent-as-member. Frontend repoints `chatService` + the realtime hook to the new tables/endpoints behind `VITE_FEATURE_CONVERSATIONS`.

**Tech Stack:** FastAPI + SQLAlchemy `db_engine` (privileged) + Postgres/Supabase; React 19 + Vite + Supabase Realtime + i18next.

## Global Constraints
- **Branch:** start the epic from a fresh branch off `master` (e.g. `feature/unified-conversations-p1`). The completed staged-media work (`d1f22150`, `generated_media` 3→1) is folded in here (its `generated_media.channel_id` is renamed to `conversation_id` in Task 1).
- **No history migration.** Legacy `channels`/`channel_messages`/`channel_members`/`agent_channels` + `ai_*` tables stay untouched and are NOT dropped in Phase 1 (Phase 3 drops them). Existing channel data is abandoned (per spec D2/D10).
- **Privileged-DB + in-app-auth** (copy the existing pattern): backend reads/writes via `db_engine` (RLS bypassed); the service layer enforces membership. RLS exists ONLY to protect the frontend Realtime/PostgREST read path.
- **`type` columns are open TEXT, NO CHECK** (`conversations.type`, `messages.type`) so future types need no migration (spec). Phase 1 only creates `type IN (direct_agent|group|public)` conversations and `text|image|media_card|task_card|system|status` messages — but the DB does not constrain them.
- **`messages.type` has NO `tool_call`** (runs live in `agent_runs`; spec D6).
- **`messages.parent_id` is two-level** (points to a root message only; enforced in the service, not the DB — spec D4).
- **`conversation_members` dual FK** (`user_id`→auth.users, `agent_id`→ai_agents) + CHECK exactly one set, keyed by `member_type` (spec D8). Agents are members (no `agent_channels`; spec D4).
- **seq** allocated app-side in the same txn as the insert (`UPDATE conversations SET last_seq=last_seq+1 RETURNING`), guarded by `UNIQUE (conversation_id, seq)`. No DB trigger/sequence.
- **Realtime** needs BOTH the publication AND `REPLICA IDENTITY FULL` (for UPDATE payloads on edit/delete).
- **asyncpg coercion (DoD every endpoint):** `sender_id` UUID→str (`MessageOut` field_validator), snowflake BIGINT→str at the API boundary, NULL bind in a comparison wrapped `CAST(:p AS bigint)`. Mocked tests miss these — run a real-DB smoke per DB task.
- **Flags:** backend `FEATURE_CONVERSATIONS: bool = Field(default=False)` in `config.py`; frontend `conversations()` in `frontend/utils/featureFlags.ts` (`VITE_FEATURE_CONVERSATIONS === 'true'`). Frontend reads go to the new API/Realtime only when on.
- **Migration numbering:** next free is **327** (max existing 326). Use 327/328/329.
- **Lint:** backend black+isort+flake8 on every .py; frontend tsc+build+vitest. Island UI, zero emoji, no `zinc-*`; i18n en+zh parity.
- **Verification:** per-task real-DB smoke + a real logged-in browser pass before Phase 1 ship (names not UUIDs, own-right/other-left, both themes, image inline/expand/save). Clean up prod test data.

## Column/Table Rename Map (legacy → canonical)
Used by the port tasks. Pure renames unless noted.

| Legacy | Canonical | Note |
|---|---|---|
| `channels` | `conversations` | |
| `channel_messages` | `messages` | |
| `channel_members` | `conversation_members` | now holds agents too (dual FK) |
| `agent_channels` | (removed) | agents become `conversation_members` rows (`member_type='agent'`, `agent_id`) |
| `channels.team_id` | `conversations.scope_id` | |
| `channels.last_message_seq` | `conversations.last_seq` | |
| `channels.is_archived` | `conversations.archived_at IS NOT NULL` | archived = timestamp, not bool |
| `channel_messages.content_type` | `messages.type` | |
| `channel_messages.reply_to_id` | `messages.parent_id` | |
| `channel_messages.from_bot_agent_id` | `messages.from_agent_id` | |
| `channel_members.user_id` (PK) | `conversation_members.user_id` (nullable, dual-FK) | member queries add `member_type='user' AND` |

## File Structure
- `supabase/migrations/327_conversations_tables.sql` — canonical tables + indexes + `generated_media.channel_id`→`conversation_id`.
- `supabase/migrations/328_conversations_rls.sql` — SECURITY DEFINER helpers + RLS policies (port of 321).
- `supabase/migrations/329_conversations_realtime.sql` — publication + REPLICA IDENTITY FULL (port of 322).
- `backend/app/repositories/conversation_repository.py` — port of `chat_repository.py`.
- `backend/app/services/conversation_service.py` — port of `chat_service.py`.
- `backend/app/services/chat/conversation_agent_turn.py` — port of `channel_agent_turn.py` (takes a conversation dict).
- `backend/app/schemas/conversation.py` — port of `chat.py`.
- `backend/app/api/conversation_router.py` — port of `chat_router.py` (prefix `/conversations`) + attachment upload/promote.
- `backend/app/core/config.py` — add `FEATURE_CONVERSATIONS`.
- `backend/app/api/__init__.py` — register the new router (flag-gated mount).
- `frontend/utils/featureFlags.ts` — add `conversations()`.
- `frontend/services/conversationService.ts` — port of `chatService.ts` (paths `/conversations/*`) + `uploadConversationImage` + `saveImageToLibrary`.
- `frontend/hooks/useConversationRealtime.ts` — port of `useChannelRealtime.ts` (subscribes to `messages`).
- `frontend/pages/ChatPage.tsx` — flag-switched to the new service/hook + `handleAttachFiles`.
- `frontend/components/chat/MessageBubble.tsx` + `ImageLightbox.tsx` — inline image + lightbox + save-to-library.
- `frontend/public/locales/{en,zh}.json` — `chat.image.*`.
- Tests alongside each.

---

## Task 1: Migration 327 — canonical tables + generated_media rename

**Files:** Create `supabase/migrations/327_conversations_tables.sql`. Reference: `320_chat_tables.sql` (structure), `326_chat_attachments.sql` (generated_media.channel_id added there).

- [ ] **Step 1: Write the migration.** Exact content:

```sql
-- 327_conversations_tables.sql — Unified Conversation epic Phase 1.
-- Canonical conversation timeline (rebuild; legacy channels* untouched, dropped in Phase 3).
-- Snowflake BIGINT PKs; teams.id BIGINT; auth.users.id + ai_agents.id are UUID.

CREATE TABLE IF NOT EXISTS public.conversations (
  id            BIGINT      PRIMARY KEY DEFAULT generate_snowflake_id(),
  type          TEXT        NOT NULL,                 -- open TEXT (no CHECK): direct_agent|group|public|...
  scope_id      BIGINT      NOT NULL REFERENCES public.teams(id) ON DELETE CASCADE,
  project_id    BIGINT,
  title         TEXT,
  topic         TEXT,
  history_mode  TEXT        NOT NULL DEFAULT 'shared' CHECK (history_mode IN ('shared','joined')),
  last_seq      BIGINT      NOT NULL DEFAULT 0,
  created_by    UUID        NOT NULL,
  archived_at   TIMESTAMPTZ,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_conversations_scope ON public.conversations(scope_id) WHERE archived_at IS NULL;

CREATE TABLE IF NOT EXISTS public.conversation_members (
  conversation_id BIGINT      NOT NULL REFERENCES public.conversations(id) ON DELETE CASCADE,
  member_type     TEXT        NOT NULL CHECK (member_type IN ('user','agent')),
  user_id         UUID        REFERENCES auth.users(id) ON DELETE CASCADE,
  agent_id        UUID        REFERENCES public.ai_agents(id) ON DELETE CASCADE,
  role            TEXT        NOT NULL DEFAULT 'member',
  last_read_seq   BIGINT      NOT NULL DEFAULT 0,
  mention_count   INTEGER     NOT NULL DEFAULT 0,
  notify_level    TEXT        NOT NULL DEFAULT 'all' CHECK (notify_level IN ('all','mentions','none')),
  open            BOOLEAN     NOT NULL DEFAULT true,
  added_by        UUID,
  joined_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT conversation_members_one_id CHECK (
    (member_type='user'  AND user_id  IS NOT NULL AND agent_id IS NULL) OR
    (member_type='agent' AND agent_id IS NOT NULL AND user_id  IS NULL)
  )
);
-- one membership per (conversation, member) regardless of type
CREATE UNIQUE INDEX IF NOT EXISTS uq_conversation_members
  ON public.conversation_members (conversation_id, member_type, COALESCE(user_id, agent_id));
CREATE INDEX IF NOT EXISTS idx_conversation_members_user_open
  ON public.conversation_members(user_id, open) WHERE member_type='user';
CREATE INDEX IF NOT EXISTS idx_conversation_members_agent
  ON public.conversation_members(agent_id) WHERE member_type='agent';

CREATE TABLE IF NOT EXISTS public.messages (
  id              BIGINT      PRIMARY KEY DEFAULT generate_snowflake_id(),
  conversation_id BIGINT      NOT NULL REFERENCES public.conversations(id) ON DELETE CASCADE,
  seq             BIGINT      NOT NULL,
  parent_id       BIGINT      REFERENCES public.messages(id) ON DELETE SET NULL,
  sender_type     TEXT        NOT NULL DEFAULT 'user' CHECK (sender_type IN ('user','agent','system')),
  sender_id       UUID,
  from_agent_id   UUID,
  type            TEXT        NOT NULL DEFAULT 'text',   -- open TEXT (no CHECK): text|image|media_card|task_card|system|status
  body            JSONB       NOT NULL DEFAULT '{}'::jsonb,
  edited_at       TIMESTAMPTZ,
  deleted_at      TIMESTAMPTZ,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (conversation_id, seq)
);
CREATE INDEX IF NOT EXISTS idx_messages_keyset ON public.messages(conversation_id, seq DESC);
CREATE INDEX IF NOT EXISTS idx_messages_parent ON public.messages(conversation_id, parent_id) WHERE parent_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS public.message_attachments (
  message_id          BIGINT  NOT NULL REFERENCES public.messages(id) ON DELETE CASCADE,
  generated_media_id  BIGINT  NOT NULL,
  ord                 INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (message_id, generated_media_id)
);

CREATE TABLE IF NOT EXISTS public.message_refs (
  message_id BIGINT NOT NULL REFERENCES public.messages(id) ON DELETE CASCADE,
  ref_type   TEXT   NOT NULL,                          -- resource|media|message|issue
  ref_id     TEXT   NOT NULL,
  PRIMARY KEY (message_id, ref_type, ref_id)
);

-- Fold the staged-media work (mig 326): chat uploads now reference a conversation, not a channel.
ALTER TABLE public.generated_media RENAME COLUMN channel_id TO conversation_id;
ALTER INDEX IF EXISTS idx_genmedia_channel RENAME TO idx_genmedia_conversation;
```

- [ ] **Step 2: Apply locally + verify columns.** Run: `psql -h 127.0.0.1 -p 54322 -U postgres -d postgres -f supabase/migrations/327_conversations_tables.sql`
  Then verify: `psql ... -c "SELECT column_name FROM information_schema.columns WHERE table_name='conversation_members' ORDER BY ordinal_position;"`
  Expected: includes `user_id`, `agent_id`, `member_type`. And `SELECT conversation_id FROM generated_media LIMIT 0;` succeeds (rename applied).
- [ ] **Step 3: Commit.** `git add supabase/migrations/327_conversations_tables.sql && git commit -m "feat(chat): canonical conversations/messages tables + generated_media conversation_id (mig 327)"`

---

## Task 2: Migration 328 — RLS (port of 321)

**Files:** Create `supabase/migrations/328_conversations_rls.sql`. Reference: `321_chat_rls.sql` verbatim pattern (helpers + SELECT-only authed policies + service_role ALL). R1: copy this proven shape exactly.

- [ ] **Step 1: Write the migration.** Exact content:

```sql
-- 328_conversations_rls.sql — RLS guarding the frontend Realtime/PostgREST path.
-- Backend uses db_engine (privileged) + in-app checks. Mirrors 321_chat_rls.sql.

CREATE OR REPLACE FUNCTION public.is_conversation_member(p_user UUID, p_conversation BIGINT)
RETURNS BOOLEAN LANGUAGE SQL SECURITY DEFINER STABLE
SET search_path = public, pg_temp
AS $$
  SELECT EXISTS (
    SELECT 1 FROM public.conversation_members
    WHERE conversation_id = p_conversation AND member_type='user' AND user_id = p_user
  );
$$;
GRANT EXECUTE ON FUNCTION public.is_conversation_member(UUID, BIGINT) TO authenticated;

CREATE OR REPLACE FUNCTION public.conversation_member_joined_at(p_user UUID, p_conversation BIGINT)
RETURNS TIMESTAMPTZ LANGUAGE SQL SECURITY DEFINER STABLE
SET search_path = public, pg_temp
AS $$
  SELECT joined_at FROM public.conversation_members
  WHERE conversation_id = p_conversation AND member_type='user' AND user_id = p_user;
$$;
GRANT EXECUTE ON FUNCTION public.conversation_member_joined_at(UUID, BIGINT) TO authenticated;

ALTER TABLE public.conversations        ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.conversation_members ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.messages             ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.message_attachments  ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.message_refs         ENABLE ROW LEVEL SECURITY;

CREATE POLICY conversations_select ON public.conversations
  FOR SELECT USING (
    (type = 'public' AND scope_id IN (SELECT public.get_user_team_ids((SELECT auth.uid()))))
    OR public.is_conversation_member((SELECT auth.uid()), id)
  );

CREATE POLICY conversation_members_select ON public.conversation_members
  FOR SELECT USING (
    public.is_conversation_member((SELECT auth.uid()), conversation_id)
  );

CREATE POLICY messages_select ON public.messages
  FOR SELECT USING (
    public.is_conversation_member((SELECT auth.uid()), conversation_id)
    AND EXISTS (
      SELECT 1 FROM public.conversations c
      WHERE c.id = messages.conversation_id
        AND (
          c.history_mode = 'shared'
          OR messages.created_at >=
               public.conversation_member_joined_at((SELECT auth.uid()), messages.conversation_id)
        )
    )
  );

CREATE POLICY message_attachments_select ON public.message_attachments
  FOR SELECT USING (
    EXISTS (SELECT 1 FROM public.messages m
            WHERE m.id = message_attachments.message_id
              AND public.is_conversation_member((SELECT auth.uid()), m.conversation_id))
  );

CREATE POLICY message_refs_select ON public.message_refs
  FOR SELECT USING (
    EXISTS (SELECT 1 FROM public.messages m
            WHERE m.id = message_refs.message_id
              AND public.is_conversation_member((SELECT auth.uid()), m.conversation_id))
  );

-- Service-role full access (backend privileged path).
CREATE POLICY conversations_service_all ON public.conversations FOR ALL
  USING (current_setting('request.jwt.claims', true)::jsonb->>'role' = 'service_role');
CREATE POLICY conversation_members_service_all ON public.conversation_members FOR ALL
  USING (current_setting('request.jwt.claims', true)::jsonb->>'role' = 'service_role');
CREATE POLICY messages_service_all ON public.messages FOR ALL
  USING (current_setting('request.jwt.claims', true)::jsonb->>'role' = 'service_role');
CREATE POLICY message_attachments_service_all ON public.message_attachments FOR ALL
  USING (current_setting('request.jwt.claims', true)::jsonb->>'role' = 'service_role');
CREATE POLICY message_refs_service_all ON public.message_refs FOR ALL
  USING (current_setting('request.jwt.claims', true)::jsonb->>'role' = 'service_role');
```

- [ ] **Step 2: Apply locally.** `psql ... -f supabase/migrations/328_conversations_rls.sql` (expect no error). Verify: `psql ... -c "SELECT polname FROM pg_policy WHERE polrelid='public.messages'::regclass;"` lists `messages_select` + `messages_service_all`.
- [ ] **Step 3: Commit.** `git commit -am "feat(chat): conversations RLS (port of 321) (mig 328)"`

---

## Task 3: Migration 329 — Realtime publication (port of 322)

**Files:** Create `supabase/migrations/329_conversations_realtime.sql`. Reference: `322_chat_realtime.sql`.

- [ ] **Step 1: Write the migration.** Exact content:

```sql
-- 329_conversations_realtime.sql — publish messages + conversation_members to supabase_realtime.
-- RLS (328) filters per subscriber. REPLICA IDENTITY FULL so UPDATE payloads carry full rows.
DO $$
BEGIN
  ALTER TABLE public.messages REPLICA IDENTITY FULL;
  IF NOT EXISTS (SELECT 1 FROM pg_publication_tables
                 WHERE pubname='supabase_realtime' AND schemaname='public' AND tablename='messages') THEN
    ALTER PUBLICATION supabase_realtime ADD TABLE public.messages;
    RAISE NOTICE 'messages added to supabase_realtime.';
  END IF;

  ALTER TABLE public.conversation_members REPLICA IDENTITY FULL;
  IF NOT EXISTS (SELECT 1 FROM pg_publication_tables
                 WHERE pubname='supabase_realtime' AND schemaname='public' AND tablename='conversation_members') THEN
    ALTER PUBLICATION supabase_realtime ADD TABLE public.conversation_members;
    RAISE NOTICE 'conversation_members added to supabase_realtime.';
  END IF;
END $$;
```

- [ ] **Step 2: Apply locally.** `psql ... -f supabase/migrations/329_conversations_realtime.sql`. Verify: `psql ... -c "SELECT tablename FROM pg_publication_tables WHERE pubname='supabase_realtime' AND tablename IN ('messages','conversation_members');"` returns both.
- [ ] **Step 3: Commit.** `git commit -am "feat(chat): conversations realtime publication (mig 329)"`

---

## Task 4: Backend — ConversationRepository (port of chat_repository.py)

**Files:** Create `backend/app/repositories/conversation_repository.py`; Test `backend/tests/test_conversation_repository.py`. Reference: copy `backend/app/repositories/chat_repository.py` and apply the Rename Map + the deltas below. Keep `_bigint`, the `db_engine` usage, the in-txn seq allocation, and the `get_*` singleton convention identical.

**Interfaces produced (the service + agent-turn consume these):**
- `create_conversation(*, creator_id, scope_id, type, name, history_mode, member_ids) -> dict` — inserts the conversation, then inserts the creator (role `owner`) + each member as `conversation_members(member_type='user', user_id=...)`.
- `add_members(*, conversation_id, user_ids) -> int`
- `is_member(*, conversation_id, user_id) -> bool` (query `member_type='user' AND user_id=:uid`)
- `is_team_member(*, team_id, user_id) -> bool` (unchanged — still `team_members`)
- `conversation_scope_id(*, conversation_id) -> int|None`
- `get_my_conversations(user_id) -> list[dict]` (join on `member_type='user'`, `last_seq`, `archived_at IS NULL`, `open`)
- `list_member_ids(conversation_id) -> list[str]` (user members only)
- `send_message(*, conversation_id, sender_id, sender_type, type, body, parent_id, from_agent_id=None) -> dict` (seq via `UPDATE conversations SET last_seq=last_seq+1 ... RETURNING`; advance sender read cursor when `sender_type='user'`)
- `list_messages(*, conversation_id, before_seq, limit) -> list[dict]` (keyset by seq; `CAST(:before AS bigint)`)
- `mark_read(*, conversation_id, user_id, last_read_seq) -> None`
- `add_agent_member(*, conversation_id, agent_id, added_by) -> None` (INSERT conversation_members `member_type='agent', agent_id=...` ON CONFLICT on the unique index DO NOTHING)
- `is_agent_member(*, conversation_id, agent_id) -> bool`
- `list_conversation_agent_ids(*, conversation_id) -> list[str]` (`member_type='agent'`)
- `recent_messages(*, conversation_id, limit=20) -> list[dict]`
- `increment_mentions(*, conversation_id, user_ids) -> None`
- `edit_message(conversation_id, message_id, sender_id, body) -> dict|None`
- `soft_delete_message(conversation_id, message_id, sender_id) -> dict|None`
- `get_conversation(*, conversation_id) -> dict|None` (returns `id, scope_id, type, history_mode, last_seq`)
- `add_attachments(*, message_id, generated_media_ids: list[int]) -> None` (NEW — INSERT into message_attachments with ord index)
- `get_conversation_repository() -> ConversationRepository` singleton

**Deltas vs a pure rename** (everything else is the Rename Map applied to the SQL in `chat_repository.py`):
1. **Membership inserts** use the dual-FK shape: `INSERT INTO conversation_members (conversation_id, member_type, user_id, role) VALUES (:cid, 'user', :uid, :role)`; `is_member`/`get_my_conversations`/`list_member_ids`/`mark_read`/`increment_mentions` all add `member_type='user' AND` to their WHERE on user_id.
2. **`add_agent_member`** replaces `add_agent_to_channel`: `INSERT INTO conversation_members (conversation_id, member_type, agent_id, added_by) VALUES (:cid, 'agent', :agent_id, :added_by) ON CONFLICT DO NOTHING` (relies on `uq_conversation_members`). `list_conversation_agent_ids`: `SELECT agent_id FROM conversation_members WHERE conversation_id=:cid AND member_type='agent'`.
3. **`send_message`** column renames: `content_type`→`type`, `reply_to_id`→`parent_id`, `from_bot_agent_id`→`from_agent_id`, `last_message_seq`→`last_seq`; RETURNING lists the renamed columns.
4. **`get_my_conversations`** join: `JOIN conversations c ON c.id = cm.conversation_id WHERE cm.member_type='user' AND cm.user_id=:uid AND c.archived_at IS NULL AND cm.open=true ORDER BY c.last_seq DESC`, unread = `GREATEST(c.last_seq - cm.last_read_seq, 0)`.
5. **`add_attachments`** (new): `for ord, gid in enumerate(generated_media_ids): INSERT INTO message_attachments (message_id, generated_media_id, ord) VALUES (:mid, :gid, :ord) ON CONFLICT DO NOTHING`.

- [ ] **Step 1: Write failing tests** `test_conversation_repository.py` (mock `db_engine` fetch/execute like the existing chat repo tests if any; otherwise assert SQL shape via a fake engine). Minimum: `send_message` allocates seq via the conversations UPDATE then inserts with `type`/`parent_id`/`from_agent_id`; `is_member` filters `member_type='user'`; `add_agent_member` inserts `member_type='agent'`; `list_conversation_agent_ids` filters `member_type='agent'`; `add_attachments` inserts one row per id with incrementing ord.
- [ ] **Step 2: Run tests, verify fail.** `cd backend && uv run pytest tests/test_conversation_repository.py -v` → fail (module missing).
- [ ] **Step 3: Implement** by copying `chat_repository.py` and applying the Rename Map + deltas above.
- [ ] **Step 4: Run tests, verify pass.** Same command → pass.
- [ ] **Step 5: Real-DB smoke** (against local Supabase): create a conversation, send 3 messages, assert seq=1,2,3 and `list_messages` returns them desc. Document the snippet in the test file as an integration test (skippable without DB).
- [ ] **Step 6: Lint + commit.** `uv run black . && uv run isort . && uv run flake8 app/repositories/conversation_repository.py tests/test_conversation_repository.py` ; `git commit -am "feat(chat): ConversationRepository (port + dual-FK members + attachments)"`

---

## Task 5: Backend — ConversationService (port of chat_service.py)

**Files:** Create `backend/app/services/conversation_service.py`; Test `backend/tests/test_conversation_service.py`. Reference: copy `backend/app/services/chat_service.py`; apply the Rename Map; keep the per-conversation semaphore (`_CONVERSATION_TURN_CAP=2`), `_require_member`, `agent_chat_caps` gating, mention fan-out, and `dispatch_summons` anti-loop guard identical in shape.

**Interfaces consumed:** `conversation_repository.get_conversation_repository()` (Task 4); `run_conversation_agent_turn` (Task 6); `get_agent_repository`, `agent_chat_caps`, `extract_agent_mentions` (unchanged imports).
**Interfaces produced:** `ConversationService` with `create_conversation`, `list_my_conversations`, `add_members`, `post_message`, `get_messages`, `mark_read`, `add_agent`, `edit_message`, `delete_message`, `dispatch_summons`, `_require_member`; `get_conversation_service()` singleton.

**Deltas vs pure rename:**
1. `dispatch_summons`: read agents via `repo.list_conversation_agent_ids` (now conversation_members); reply write uses `send_message(... sender_type="agent", type="text", body={"text": reply}, from_agent_id=agent["id"])`.
2. `add_agent`: gate via `agent_chat_caps`, then `repo.add_agent_member(...)`.
3. `post_message`: signature `(*, conversation_id, user_id, type, body, parent_id)`; mention fan-out unchanged (reads `body.mention_user_ids`).
4. `get_conversation(...)["scope_id"]` used where the old code used `channel["team_id"]` (e.g. `agent_chat_caps.allows_team(conversation["scope_id"])`).

- [ ] **Step 1: Write failing tests** (mock repo + a fake `run_conversation_agent_turn`): `post_message` requires membership (PermissionError when not member); `dispatch_summons` returns `[]` when `message["from_agent_id"]` set (anti-loop); `dispatch_summons` writes an agent reply via send_message with `from_agent_id` set; `add_agent` raises PermissionError when caps disabled.
- [ ] **Step 2: Run, verify fail.** `uv run pytest tests/test_conversation_service.py -v`.
- [ ] **Step 3: Implement** the port.
- [ ] **Step 4: Run, verify pass.**
- [ ] **Step 5: Lint + commit.** `git commit -am "feat(chat): ConversationService (port + agent-as-member dispatch)"`

---

## Task 6: Backend — conversation_agent_turn (port of channel_agent_turn.py)

**Files:** Create `backend/app/services/chat/conversation_agent_turn.py`; Test `backend/tests/test_conversation_agent_turn.py`. Reference: copy `channel_agent_turn.py`.

**Interface produced:** `run_conversation_agent_turn(*, agent_slug, summoner_user_id, conversation: dict) -> Optional[str]` (same gates 0–5; same `build_agent_runner_stack`/`PromptComposer`/`RunRecorder` wiring with `session_id=None`).
**Deltas:** `conversation["id"]`/`conversation["scope_id"]` replace `channel["id"]`/`channel["team_id"]`; history via `conversation_repository.recent_messages(conversation_id=...)`; `_render_body` reads `type` instead of `content_type`; `_map_message` uses `sender_type`; RunRecorder `team_id=int(conversation["scope_id"])`, `metadata={"conversation_id": str(conversation_id), "summoner": ...}`, `trigger="chat_summon"` unchanged; resource-fetch closure scoped to `(summoner_user_id, scope_id)`.

- [ ] **Step 1: Write failing test** — `_build_history` maps agent→assistant + renders `type='text'` body; `run_conversation_agent_turn` returns None when agent not found (gate 1) [mock `get_agent_repository`].
- [ ] **Step 2: Run, verify fail.**
- [ ] **Step 3: Implement** the port. Update the import in `conversation_service.py` to `from app.services.chat.conversation_agent_turn import run_conversation_agent_turn`.
- [ ] **Step 4: Run, verify pass.**
- [ ] **Step 5: Lint + commit.** `git commit -am "feat(chat): conversation_agent_turn (port; reads conversation_members history)"`

---

## Task 7: Backend — schemas + router + flag + registration

**Files:** Create `backend/app/schemas/conversation.py`, `backend/app/api/conversation_router.py`; Modify `backend/app/core/config.py` (add flag), `backend/app/api/__init__.py` (register). Test `backend/tests/test_conversation_router.py`. Reference: `schemas/chat.py`, `api/chat_router.py`.

**Schemas (port of chat.py with renames):** `ConversationCreate{type, scope_id, name, topic, history_mode, member_ids}`; `ConversationOut{id, scope_id, type, history_mode, name, topic, last_seq, unread, mention_count, created_at}`; `MessageCreate{type: Literal['text','media_card','task_card','image'] = 'text', body, parent_id}`; `MessageOut{id, conversation_id, seq, sender_id, sender_type, type, body, parent_id, edited_at, deleted_at, created_at}` with the `sender_id` UUID→str `field_validator` (copy verbatim); `MemberAdd`, `AgentAdd`, `MarkReadIn`.

**Router (prefix `/conversations`, port of chat_router):** the same endpoints renamed — `GET /conversations`, `POST /conversations`, `POST /conversations/{id}/members`, `GET /conversations/{id}/messages`, `POST /conversations/{id}/agents`, `POST /conversations/{id}/messages` (with the `_summon_runner` background task → `dispatch_summons`), `POST /conversations/{id}/read`, `PATCH /conversations/{id}/messages/{mid}`, `DELETE /conversations/{id}/messages/{mid}`. Map `PermissionError`→403, `ValueError`→400. (Attachment endpoints come in Task 9.)

- [ ] **Step 1: Add the flag.** In `backend/app/core/config.py`, in the feature-flags block, add:
```python
    FEATURE_CONVERSATIONS: bool = Field(
        default=False,
        description="Serve the unified /conversations API. Off (default) keeps "
        "only the legacy /chat API live.",
    )
```
- [ ] **Step 2: Write failing test** `test_conversation_router.py` (FastAPI TestClient with a mocked service): `POST /api/v1/conversations/{id}/messages` returns 200 with a MessageOut and schedules the summon background task; non-member → 403.
- [ ] **Step 3: Run, verify fail.**
- [ ] **Step 4: Implement** schemas + router (port). Register in `api/__init__.py`:
```python
from app.api.conversation_router import router as conversation_router
...
if settings.FEATURE_CONVERSATIONS:
    api_router.include_router(router=conversation_router, tags=["Conversations"])
```
  (Mirror the existing `chat_router` include at `__init__.py:144`; gate on the flag so it's dark by default.)
- [ ] **Step 5: Run, verify pass.**
- [ ] **Step 6: Real-DB smoke** (flag on, local): signup test user → SQL-confirm email → signin → `POST /conversations` → `POST .../messages` → `GET .../messages` returns it. Confirms asyncpg types (sender_id str, NULL before_seq CAST).
- [ ] **Step 7: Lint + commit.** `git commit -am "feat(chat): /conversations schemas + router + FEATURE_CONVERSATIONS flag"`

---

## Task 8: Frontend — service + realtime hook + ChatPage cutover (flag-gated, no images yet)

**Files:** Modify `frontend/utils/featureFlags.ts`; Create `frontend/services/conversationService.ts`, `frontend/hooks/useConversationRealtime.ts`; Modify `frontend/pages/ChatPage.tsx`. Reference: `chatService.ts`, `useChannelRealtime.ts`.

- [ ] **Step 1: Flag.** Add to `featureFlags.ts`:
```typescript
/** Unified conversations API + realtime (Phase 1). OFF → legacy /chat. */
export function conversations(): boolean {
  return import.meta.env.VITE_FEATURE_CONVERSATIONS === 'true';
}
```
- [ ] **Step 2: `conversationService.ts`** — copy `chatService.ts`; change all paths `/chat/...`→`/conversations/...`; rename `listChannels`→`listConversations` etc.; `toChannel`→`toConversation` (coerce `id`/`scope_id`/`last_seq` to String); `sendMessage(conversationId, body, type, parentId)` posts `{type, body, parent_id}`. Keep the `req<T>` + `getAuthHeaders` pattern verbatim. (Types: add a `Conversation` type mirroring `Channel` with `scope_id`/`last_seq`; or reuse `Channel` with renamed fields — keep it minimal.)
- [ ] **Step 3: `useConversationRealtime.ts`** — copy `useChannelRealtime.ts`; change the table from `channel_messages`→`messages` in both `.on('postgres_changes', { ... table: 'messages', filter: `conversation_id=eq.${conversationId}` }, ...)` handlers; channel name `conv-${conversationId}`.
- [ ] **Step 4: ChatPage flag switch.** In `ChatPage.tsx`, behind `conversations()`: use `conversationService` instead of `chatService` and `useConversationRealtime` instead of `useChannelRealtime`. Simplest seam: at the top, `const svc = conversations() ? conversationService : chatService;` and call `svc.*`; and call whichever hook via a small wrapper that picks by flag (or branch the hook call). Keep `memberNameById` (still `getTeamMembers`), `appendMessage` dedupe, `scheduleMarkRead`, `activeIdRef` unchanged.
- [ ] **Step 5: tsc + build.** `cd frontend && npx tsc --noEmit && npm run build`.
- [ ] **Step 6: Commit.** `git commit -am "feat(chat): frontend conversationService + realtime hook + flag-gated ChatPage cutover"`

---

## Task 9: Backend — chat image attachments on the new store (upload + promote + message_attachments)

**Files:** Modify `backend/app/api/conversation_router.py` (add upload + promote routes), `backend/app/services/chat/chat_attachment_service.py` (retarget to conversations), `backend/app/services/library/promote_generated_media_service.py` (target-scope promote). Test extend `test_conversation_router.py`. Reference: the existing `chat_attachment_service.save_chat_image` (already writes `generated_media` with `origin_kind='chat_upload'`), `generated_media_router.py:103` promote, the spec Task-3 promote design.

- [ ] **Step 1:** Retarget `save_chat_image` to conversations: signature `save_chat_image(*, conversation_id, user_id, file_bytes, filename, mime, conv_repo=None)`; resolve scope via `conversation_repository.get_conversation(...)["scope_id"]`; membership via `is_member`; `register_uploaded_media(origin=GenerationOrigin(kind="chat_upload", conversation_id=conversation_id), subdir="chat")`. (Rename `GenerationOrigin.channel_id`→`conversation_id` in `generated_media_service.py` + the INSERT column, matching the mig 327 rename.)
- [ ] **Step 2:** Generalize `PromoteGeneratedMediaService.promote(*, gen_id, user_id, target_scope_id)` (spec Task 3): `get_by_id`; read-auth — if `origin_kind=='chat_upload'` require conversation membership (`gen['conversation_id']`), else require scope access; write-auth require membership of `target_scope_id` or it's the user's personal team; copy file → `teams/{target_scope_id}/uploads/{rid}/v1/{filename}`; idempotent. Update the existing generations route to pass `target_scope_id=await _scope(auth)`.
- [ ] **Step 3:** Add routes to `conversation_router.py`:
  - `POST /conversations/{conversation_id}/attachments` (UploadFile) → `save_chat_image` → `{id, mime, file_size_bytes, url: f"/api/v1/generated-media/{id}/cover"}` (PermissionError→403, ValueError→400).
  - `POST /conversations/attachments/{attachment_id}/promote` body `{scope_id:int}` → `PromoteGeneratedMediaService().promote(gen_id=attachment_id, user_id, target_scope_id=scope_id)` → `{promoted_resource_id}`.
- [ ] **Step 4:** When a message is posted with `type='image'` body carrying `generated_media_id`, the router (or service `post_message`) calls `repo.add_attachments(message_id=msg['id'], generated_media_ids=[int(gid)])`. Wire this in `post_message` when `type=='image'` and `body.generated_media_id` present.
- [ ] **Step 5: Tests** — upload writes a `generated_media` row with `origin_kind='chat_upload'` + `conversation_id`; promote to a target scope copies file + creates resource/version/item, idempotent; non-member of target → 403.
- [ ] **Step 6: Lint + real-DB smoke + commit.** `git commit -am "feat(chat): conversation image upload + promote (team/personal) on staged store"`

---

## Task 10: Frontend — upload + send + inline render + lightbox + save-to-library

**Files:** Modify `frontend/services/conversationService.ts`, `frontend/pages/ChatPage.tsx`, `frontend/components/chat/MessageBubble.tsx`; Create `frontend/components/chat/ImageLightbox.tsx`; Modify `frontend/components/chat/MessageList.tsx`, `frontend/public/locales/{en,zh}.json`. Reference: the prior image plan (`2026-06-30-team-chat-image-upload.md` Tasks 4–5), the existing `Composer` `onAttachFiles` (T1 already done), `ChatAttachmentPicker.helpers` (`validateFileBatch`).

- [ ] **Step 1:** `conversationService`: `uploadConversationImage(conversationId, file): Promise<{id,url,mime}>` (multipart POST `/conversations/{id}/attachments`); `saveImageToLibrary(attachmentId, scopeId): Promise<{promoted_resource_id}>` (POST `/conversations/attachments/{attachmentId}/promote` `{scope_id:Number(scopeId)}`).
- [ ] **Step 2:** ChatPage `handleAttachFiles(files)` — guard `activeIdRef.current` + `selectedTeamId`; `validateFileBatch`; image-only (else toast `chat.image.onlyImages`); for each: `att = await conversationService.uploadConversationImage(activeIdRef.current!, file)`; `body={kind:'image', generated_media_id: att.id, image_url: att.url, alt: file.name}`; `msg = await svc.sendMessage(activeIdRef.current!, body, 'image')`; `appendMessage(msg)`; uploading indicator. Pass `onAttachFiles={handleAttachFiles}` to `<Composer>`.
- [ ] **Step 3:** `ImageLightbox.tsx` — `{src, alt?, onClose}`; `fixed inset-0 z-[60] bg-black/80`; centered `<img max-w-[92vw] max-h-[92vh] object-contain>`; backdrop/Esc/close → onClose. Island, zero emoji.
- [ ] **Step 4:** `MessageBubble` — `isImage = message.type==='image' && body.kind==='image' && typeof body.image_url==='string'`; render inline `<button><img src={body.image_url} ... max-w-[min(78%,360px)] max-h-[320px] rounded-[12px]/></button>` → open lightbox; hover "Save to library" → small team/personal popover → `onSaveImage`. Thread `onSaveImage`+`personalTeamId` through `MessageList`. (Note: `MessageBubble` keys off `message.type` now, not `content_type` — confirm the new MessageOut field name flows through `ChatMessage` type.)
- [ ] **Step 5:** `handleSaveImage(generatedMediaId, scope)` in ChatPage → `conversationService.saveImageToLibrary(id, scope==='team'?selectedTeamId:personalTeamId)` → toast `chat.image.saved`.
- [ ] **Step 6:** i18n `chat.image.*` (`uploading, uploadError, onlyImages, close, save, saveTeam, savePersonal, saved, saveError`) en+zh parity.
- [ ] **Step 7:** vitest (MessageBubble image branch renders `<img>`, opens lightbox) + tsc + build + JSON parse.
- [ ] **Step 8: Commit.** `git commit -am "feat(chat): image upload/send + inline render + lightbox + save-to-library"`

---

## Self-Review
- **Spec coverage:** canonical tables (T1) incl. dual-FK members, two-level parent, open-TEXT types, keyset index; RLS port (T2, R1); realtime (T3); backend repo/service/agent-turn/router ports (T4–T7) with agent-as-member + typed system messages + asyncpg coercion; flag gating (T7/T8); media fold incl. generated_media rename + promote team/personal (T9); frontend cutover + image feature (T8/T10). Phase 2 (1:1) + Phase 3 (drops) intentionally excluded.
- **Placeholder scan:** migrations are full SQL; ports specify the source file + Rename Map + explicit deltas + produced interfaces (an engineer can execute exactly); new code (RLS, message_attachments, image feature) is spelled out.
- **Type consistency:** `last_seq`/`scope_id`/`type`/`parent_id`/`from_agent_id`/`conversation_id` used consistently across T1→T10; `generated_media.channel_id`→`conversation_id` renamed in T1 and consumed in T9; `GenerationOrigin.channel_id`→`conversation_id` updated in T9 Step 1.
- **Risks:** R1 (realtime RLS) handled by copying 321/322 verbatim-shaped (T2/T3). seq hot-row is per-conversation (acceptable). The ChatPage flag seam (T8 Step 4) is the fiddliest — keep both services callable; verify the legacy path still works with the flag off.

## Execution Handoff
Execute via superpowers:subagent-driven-development (fresh implementer + reviewer per task; final whole-branch review). After T10: real logged-in browser pass (create conversation → send text + image via paste/drop/pick → inline → expand → save to team & personal → confirm in resource library; names not UUIDs; own-right/other-left; both themes) → ship (backend CI/ACR; frontend Vercel) with `FEATURE_CONVERSATIONS`/`VITE_FEATURE_CONVERSATIONS` flipped on → flip private → clean up test data. Then Phase 2.
