# Unified Conversation + Message Architecture — Design Spec

**Status:** Approved design (2026-06-30). Next: per-phase implementation plans via writing-plans.

**Goal:** Collapse Nous's two parallel conversation systems (team chat `channels`/`channel_messages` + 1:1 AI chat `ai_sessions`/`ai_messages`) into ONE canonical timeline — `conversations` + `messages` + `conversation_members` — so human↔human, human↔agent, group, and task-derived conversations all share one store, one runner, one media tier. Retire the legacy tables. No history migration.

**Architecture (one line):** A conversation is a room; a message is a footprint in the room; an agent is just a member; everything that happens (text, image, system notice, status) is a typed message on one seq-ordered timeline; the rich 1:1 features (memory, compaction, run accounting) hang off the conversation as sidecars.

**Reference basis (battle-tested):** the model = **Mattermost** (Channels / Posts / ChannelMembers, threads via root) fused with the **OpenAI Assistants API** (thread / message / run / tool-call), with one idea from **Matrix** (everything is a typed event on one timeline) and one from **Rocket.Chat** (a bot/agent is just a member). Nous already mirrors both Mattermost (channels) and Assistants (`agent_runs`); the execution engine (`build_agent_runner_stack` / `AgentRunner` / `PromptComposer` / `RunRecorder`) is ALREADY shared by both surfaces — only the storage shells and the 1:1 memory wiring are forked.

---

## Locked Decisions

| # | Decision | Rationale |
|---|---|---|
| D1 | **Rebuild canonical `conversations`/`messages`/`conversation_members`** (don't evolve `channels`). | User wants the ideal big-tech naming; no-history makes a clean rebuild feasible. |
| D2 | **No history migration.** Drop legacy data; build clean. | Removes the riskiest/most expensive part (live data backfill + cutover). |
| D3 | **Phased delivery** (Phase 1 team chat → Phase 2 1:1 → Phase 3 retire). | Each phase independently shippable + rollbackable; bounded blast radius. |
| D4 | **Agent is a member** — fold `agent_channels` into `conversation_members` (`member_type='agent'`). | Rocket.Chat/Mattermost pattern; one membership table. |
| D5 | **Everything is a typed message** — system notices / membership / status / task cards are `messages` rows (`type`), not side tables. | Matrix "one timeline" model. |
| D6 | **Tool/run execution stays in `agent_runs`** (sidecar link), NOT a `messages.type='tool_call'`. | Assistants: a run step ≠ a message. Timeline shows text + optional task_card only. |
| D7 | **`direct_agent` = many agent threads per (user, agent)**, NO uniqueness constraint. | Matches current `ai_sessions` (ChatGPT-style "new chat" list), not a single DM. |
| D8 | **`conversation_members.member_id` split into two nullable FK columns** (`user_id`, `agent_id`) with a CHECK. | Keep FK integrity (agents live in `ai_agents`, users in `auth.users` — can't FK a single TEXT col to both). |
| D9 | **Ship the completed staged-media work (`d1f22150`, generated_media 3→1) to master independently FIRST**; the epic branches off master after. | It's a clean standalone win (−297 lines); the epic's `channel_id→conversation_id` rename builds on it. |
| D10 | **Phase 1 wipes existing team-chat prod data** (channels just shipped; mostly test data). Accepted per D2. | No-history directive. |

---

## Data Model (canonical)

```sql
conversations
  id            BIGINT PK (snowflake)
  type          TEXT NOT NULL          -- open TEXT; Phase 1/2 build: direct_agent | group | public
                                       --            reserved (not built yet): project_room | task_thread
  scope_id      BIGINT NOT NULL        -- owning team (1:1 uses the user's personal team)
  project_id    BIGINT NULL
  title         TEXT
  topic         TEXT NULL
  history_mode  TEXT
  last_seq      BIGINT NOT NULL DEFAULT 0
  created_by    UUID
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
  archived_at   TIMESTAMPTZ NULL

messages
  id              BIGINT PK (snowflake)
  conversation_id BIGINT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE
  seq             BIGINT NOT NULL        -- per-conversation monotonic (UPDATE conversations.last_seq+1 RETURNING)
  parent_id       BIGINT NULL            -- thread reply; MUST point to a root message only (two-level, Mattermost-style)
  sender_type     TEXT NOT NULL          -- user | agent | system
  sender_id       UUID NULL              -- auth uid for user; NULL for agent/system
  from_agent_id   UUID NULL              -- set on agent replies (anti self-summon loop)
  type            TEXT NOT NULL          -- text | image | file | media_card | task_card | system | status
                                         --   (NO tool_call — runs live in agent_runs, see D6)
  body            JSONB NOT NULL
  edited_at       TIMESTAMPTZ NULL
  deleted_at      TIMESTAMPTZ NULL
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now()

conversation_members                     -- people AND agents in one table (D4)
  conversation_id BIGINT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE
  member_type     TEXT NOT NULL          -- user | agent
  user_id         UUID NULL REFERENCES auth.users(id)        -- D8: dual FK
  agent_id        UUID NULL REFERENCES ai_agents(id)         -- D8: dual FK
  role            TEXT NOT NULL DEFAULT 'member'             -- owner | member
  last_read_seq   BIGINT NOT NULL DEFAULT 0
  mention_count   INT NOT NULL DEFAULT 0
  notify_level    TEXT
  added_by        UUID
  CHECK ((member_type='user' AND user_id IS NOT NULL AND agent_id IS NULL)
      OR (member_type='agent' AND agent_id IS NOT NULL AND user_id IS NULL))
  -- uniqueness: (conversation_id, member_type, COALESCE(user_id,agent_id))

message_attachments                      -- message → staged media (generated_media, already unified ✅)
  message_id          BIGINT NOT NULL REFERENCES messages(id) ON DELETE CASCADE
  generated_media_id  BIGINT NOT NULL
  ord                 INT NOT NULL DEFAULT 0

message_refs                             -- citations / quotes
  message_id BIGINT NOT NULL REFERENCES messages(id) ON DELETE CASCADE
  ref_type   TEXT NOT NULL               -- resource | media | message | issue
  ref_id     TEXT NOT NULL
```

**Phase 2 sidecars (hang off conversation_id; keep `messages` lean):**
```sql
conversation_memory     -- replaces ai_session_memory: compressed head summary / cached context
conversation_state      -- 1:1 runtime: plan-mode flag, compaction watermark, token cursor
-- agent_runs / agent_commitments: ADD nullable conversation_id + message_id columns (D6 — a LINK,
--   not a move; agent_runs remains the global run log also used by issue/broadcast/scheduled triggers).
```

---

## Access Patterns & Scale

The `messages` table is the hot, high-write, recency-biased table. Designed for it up front; physical partitioning is a documented future lever (YAGNI now).

- **Query = by conversation_id, time-desc, keyset by `seq`** (not created_at, not OFFSET):
  ```sql
  SELECT ... FROM messages
  WHERE conversation_id = :cid AND seq < :before_seq
  ORDER BY seq DESC LIMIT :n
  ```
  Index: `messages (conversation_id, seq DESC)`. One seek + sequential scan of N; stable under same-ms ties and clock skew because `seq` is monotonic + gap-free per conversation.
- **High write:**
  - Snowflake BIGINT PK → time-ordered insert locality (no random-UUID write amplification).
  - `seq` via `UPDATE conversations SET last_seq=last_seq+1 RETURNING` in the SAME txn as the insert — locks only the single conversation row (cross-conversation writes never contend; one conversation's messages are naturally serial).
  - **No FOR EACH ROW triggers on `messages`** (avoids the hot-row 57014 class we hit on flow-aggregate); aggregates/counters update statement-level or in the same insert.
  - Lean indexes only: `(conversation_id, seq DESC)` + `(conversation_id, parent_id)` for threads. Unread via `conversation_members.last_read_seq` (no extra messages index).
- **Hot/cold separation — two layers:**
  - *Architectural (now):* `conversation_memory` sidecar caches the compressed summary, so the agent reads "summary + recent tail" and **never full-scans cold history**. Cold messages stay out of every hot path.
  - *Physical (future lever, not built):* `messages` is shaped so declarative partitioning can be added without app changes — by `conversation_id` hash (write spread + conversation locality) or `created_at` range (monthly; old partitions compress/detach/cold-store). `conversations.archived_at` reserves cold-conversation archival. Start unpartitioned (a good index on a single table handles millions of rows); partition when volume demands.
- Ties into the existing [100k scale readiness] line.

---

## Phased Delivery

No history migration ⇒ no backfill, no dual-write. Each phase ships independently; rollback = flip flag (legacy tables retained until Phase 3 drop).

### Phase 1 — Conversations foundation + team chat cutover (incl. images)
- **Migration (327+):** create `conversations` / `messages` / `conversation_members` / `message_attachments` / `message_refs` + RLS + Realtime publication on `messages`.
- **Backend:** `ConversationRepository` + `ConversationService` (port team-chat logic to new tables; agent-as-member; system notices as `type='system'`). Router `/conversations/*`. Repoint the agent-summon path (`dispatch_summons` / `run_channel_agent_turn`) to the new tables.
- **Media:** rename `generated_media.channel_id` → `conversation_id` (FK→conversations). Build the chat-image feature (promote team/personal + frontend upload / inline render / lightbox / save-to-library) on the new tables here — the paused image T3–T5 land in Phase 1.
- **Frontend:** ChatPage + chatService + Realtime subscription move `channel_messages` → `messages`; types renamed. UI unchanged.
- **Drops:** none yet (legacy `channels*` kept dormant for rollback). Existing team-chat data is NOT migrated (D10).
- **Ship boundary:** team chat runs on the new store + image feature complete.

### Phase 2 — Fold 1:1 AI chat onto conversations (direct_agent + sidecars)
- **Model:** 1:1 = `conversation(type='direct_agent')` + members[user, agent]; many threads per pair (D7).
- **Sidecars:** `conversation_memory` (port `ai_session_memory`), `conversation_state` (plan-mode / compaction watermark / token cursor); add `conversation_id`+`message_id` to `agent_runs` + `agent_commitments`.
- **Backend:** rewrite `AILibraryChatService` to read/write `messages` (as a direct_agent conversation) instead of `ai_messages`, **preserving full parity** (compaction; Graphiti/Honcho/agent_memories recall; plan-mode; GenerateImage/Video; FinishIssue; per-message token cap; binary attachments). Drive against a written **parity checklist** + real-DB comparison (see Risks).
- **Media:** fold 1:1 attachments (the 3rd store, temp-`resources`) into staged `generated_media` (the previously-gated Task 6).
- **Frontend:** repoint `AIChatPanel` to `/conversations/*` (direct_agent); keep DM-style UI but share the MessageList component + the one message store.
- **Ship boundary:** 1:1 reaches feature parity on the new store + sidecars.

### Phase 3 — Retire legacy + cleanup
- **DROP:** `ai_sessions`, `ai_messages`, `ai_session_memory`, `channels`, `channel_messages`, `channel_members`, `agent_channels`.
- **Delete dead code:** `chat_upload.save_chat_temp_upload` + `temp_resource_sweeper` + legacy services.
- Final full-chain verification + docs.

---

## Error Handling

- **seq atomicity:** the `last_seq+1 RETURNING` and the insert share one transaction; failure rolls back both (no gap seq).
- **Agent summon:** background task logs-but-never-raises (failure never breaks the send HTTP response); agent replies carry `from_agent_id` to block self-summon loops.
- **asyncpg type traps (DoD for every DB endpoint):** coerce `sender_id` UUID→str and snowflake BIGINT→str at the boundary (JS precision); a NULL bind in a comparison needs `CAST(:p AS bigint)` (else AmbiguousParameterError). These are mandatory because mocked tests miss them (the team-chat epic shipped 7 such prod bugs).
- **Upload/promote:** membership → 403; non-image mime → 400; promote idempotent (already-promoted returns the existing id).

## Testing

- **Unit:** repo/service mocked — BUT mocks do NOT exercise real asyncpg types. Therefore:
- **Real-DB smoke / live E2E per phase, before "done"** (recipe: signup test user → SQL-confirm email → signin → drive API).
- **Frontend:** vitest + tsc + build.
- **One real logged-in browser UX pass per phase** (per `feedback_ui_early_visual_ux_pass`): names not UUIDs, own messages right-aligned, both themes legible, images inline/expand/save. Mocks can't catch these.
- Clean up prod test data after each round.

## Rollout / Rollback

- Each phase ships via subagent-driven-development (implementer + reviewer per task; final whole-branch review).
- **Flags gate reads:** `VITE_FEATURE_CONVERSATIONS` (frontend → new API/Realtime table) + backend `FEATURE_CONVERSATIONS`. No dual-write needed (no data migration) — clean per-surface switch.
- **Rollback** = flip flag back to legacy tables; legacy tables survive until Phase 3 DROP (caveat: messages written to new tables after the switch are lost on rollback — acceptable for the early, low-data state).
- **CI/deploy:** public→PR→private; backend waits for `Deploy Backend to ACR` success before flipping private; after adding columns NOTIFY pgrst reload + configure RLS (else Realtime/REST can't read).

---

## Risks (watch in the plans)

- **R1 — Realtime + member RLS on `messages`:** Supabase Realtime evaluates RLS per row per subscriber; a "is member of conversation" subquery on every message can be costly. The existing `channel_messages` already solved this — the plan MUST copy its proven RLS/Realtime shape, not reinvent it.
- **R2 — Phase 2 `AILibraryChatService` rewrite is the highest-risk task:** it is the largest service (compaction / recall / plan-mode / image tools / issue triggers / token caps). Mitigation: a written feature-parity checklist + real-DB comparison verification; do NOT consider it done on a green mocked suite.

## Out of Scope / YAGNI

- History/data migration (D2).
- `task_thread` / `project_room` FEATURES — `type` is open TEXT so they need no migration later, but no behavior is built now.
- Group/channel agent **memory** (the asymmetry fix) — the sidecar is conversation-keyed so it's possible, but enabling recall/harvest for group agents stays flag-dark (deferred; user said "next time").
- Physical partitioning (future lever).

---

## Sequencing

1. **Ship staged-media work to master independently** (`d1f22150`, generated_media 3→1) — D9.
2. **Epic branches off master.** Phase 1 → Phase 2 → Phase 3, each its own plan (writing-plans) + SDD execution + real-DB/visual verification + ship.
