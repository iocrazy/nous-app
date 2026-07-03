# Phase 2 Parity Checklist — folding 1:1 AI chat onto `conversations`/`messages`

**Status:** DONE-GATE spec. Read before writing the Phase 2 storage-adapter plan.
**Scope:** strangle `AILibraryChatService` storage (`ai_sessions` / `ai_messages` / `ai_session_memory`) onto the canonical `conversations` / `messages` store. Service/turn logic stays untouched; only the storage layer swaps. Every behavior below must demonstrably survive the swap.

**Evidence base (read in full):**
- `backend/app/services/ai/chat/ai_library_chat_service.py` (turn engine + all session/message storage calls)
- `backend/app/services/ai/chat/ai_library_chat_wiring.py` (recall stack + session→project/team lookups)
- `backend/app/api/ai_library_router.py` (`/sessions/*` + chat endpoint contracts)
- `backend/app/repositories/session_memory_repository.py` (ORM, `ai_session_memory`)
- `backend/app/services/ai/runner/run_recorder.py` (agent_runs telemetry, `session_id` FK)
- `backend/app/services/issues/issue_agent_executor.py` (2nd caller of `run_session_turn`)
- schema: `supabase/migrations/121,138,187,231_ai_sessions_snowflake.sql`; target: `327_conversations_tables.sql`

---

## 1. Storage surface inventory

This is the exact `MessageStore` adapter interface Phase 2 must implement twice (legacy `ai_*` + new `conversations`/`messages`). Every row is a real read/write the service issues today. All go through `get_async_supabase_admin()` (PostgREST) EXCEPT `ai_session_memory` (SQLAlchemy ORM) and two raw-SQL session lookups.

### 1a. `ai_sessions` (table)

| # | Op | Call site | Columns / filters | Returns |
|---|----|-----------|-------------------|---------|
| S1 | INSERT | `ai_library_chat_service.py:95-113` | writes `user_id, agent_id, agent_slug, title, status='active', total_tokens=0, message_count=0` + optional `project_id, team_id, context_type, context_id` | inserted row dict (`resp.data[0]`) |
| S2 | SELECT list | `:137-150` | `select("*").eq(user_id).neq(status,'deleted').order(updated_at desc).limit(n)` + optional `.eq(agent_slug)`, `.eq(project_id)` | `list[dict]` newest-updated first |
| S3 | SELECT one | `:156-162` | `select("*").eq(id).maybe_single()`; ownership enforced in Python via `row.user_id == user_id` else 404 (`:168`) | single row dict or 404 |
| S4 | UPDATE title | `:206-211` | `update({title}).eq(id)` | updated row or refetch |
| S5 | UPDATE soft-delete | `:222-227` | `update({status:'deleted'}).eq(id)` | none |
| S6 | UPDATE counters | `:1017-1027` | `update({total_tokens: prior+turn, message_count: prior+2}).eq(id)` — read-modify-write off the in-memory `session` snapshot loaded at turn start (`:437`) | none |
| S7 | SELECT project_id | `ai_library_chat_wiring.py:596-599` | raw SQL `SELECT project_id FROM public.ai_sessions WHERE id=:sid` (int bind) — recall group scoping | `project_id` or None |
| S8 | SELECT team_id | `workflows/write_memory.py:192` (`_resolve_team_workspace`) | raw SQL `SELECT team_id FROM public.ai_sessions WHERE id=:sid` — Honcho workspace `team-{id}` | `team_id` or None |

### 1b. `ai_messages` (table)

| # | Op | Call site | Columns / filters | Returns |
|---|----|-----------|-------------------|---------|
| M1 | SELECT history | `:181-188` | `select("*").eq(session_id).order(created_at asc).limit(200)` — invoked once per turn at `:445` and by GET session detail | `list[dict]` chronological |
| M2 | INSERT user | `:452-462` | writes `session_id, role='user', content` ONLY (no tokens, no agent_id, no metadata). Persisted BEFORE the model call (`:449` comment) | user row dict |
| M3 | INSERT assistant | `:993-1007` | writes `session_id, role='assistant', content, agent_id, prompt_tokens, completion_tokens, metadata_json` where `metadata_json = {run_id?, tool_calls?, awaiting_approval?}` (`:978-992`) | assistant row dict |

Message schema (mig 121/138): `id UUID PK`, `session_id → ai_sessions.id (BIGINT, ON DELETE CASCADE)`, `role CHECK IN (system,user,assistant)`, `content TEXT NOT NULL`, `agent_id UUID`, `skill_id UUID` (unused by chat), `metadata_json JSONB`, `prompt_tokens/completion_tokens INT`, `created_at TIMESTAMPTZ`. Ordering key = `created_at` (NOT a monotonic seq).

### 1c. `ai_session_memory` (table, via `SessionMemoryRepository` — SQLAlchemy ORM)

| # | Op | Call site | Key / payload | Returns |
|---|----|-----------|---------------|---------|
| SM1 | load | `session_memory_repository.py:114-129`; callers `ai_library_chat_service.py:1202-1204` (compaction loader) | `WHERE session_id=:bigint` | `SessionMemoryRow` or None |
| SM2 | upsert | `:131-176`; caller = `session_memory_runner.maybe_update_session_memory` dispatched fire-and-forget at `ai_library_chat_service.py:1046` | ON CONFLICT `session_id` DO UPDATE all non-PK cols; bumps `version`; writes `body_md, sections_json, tokens/tool_calls/turns_at_last_update, last_updated_at` | row |
| SM3 | delete | `:178-192` | `WHERE session_id=:bigint` | bool |

Schema (mig 187): `session_id BIGINT PK → ai_sessions.id (ON DELETE CASCADE)`, `body_md TEXT`, `sections_json JSONB`, `version INT`, `last_updated_at`, `tokens_at_last_update`, `tool_calls_at_last_update`, `turns_at_last_update INT`. One row per session. `_bigint()` coerces str→int at the bind boundary (asyncpg int8 codec strict — `:67-73`).

### 1d. `agent_runs` (telemetry sidecar — NOT part of the message store, but carries the session FK)

| # | Op | Call site | Note |
|---|----|-----------|------|
| R1 | INSERT | `run_recorder.py:542,562` | payload includes `session_id = str(self.session_id)` (FK, ON DELETE SET NULL, mig 231). RunRecorder is entered at `ai_library_chat_service.py:845-856` with `session_id=session_id, team_id=session.team_id, project_id=session.project_id` |
| R2 | UPDATE finalize | `:628-634` | writes tokens/cost/status on `__aexit__` |

`agent_runs.session_id` is the ONLY place chat turns tie telemetry back to the thread. Read paths (`/agents/{slug}/runs`, `/dashboard`, `/runs/live`, `admin/telemetry`) scope by `agent_id`+`user_id`, NOT by `session_id` — so they don't break on the swap, but the stored `session_id` value must remain resolvable/consistent (see mismatch §3.6).

**Adapter interface implied (minimum):** `create_session`, `list_sessions(user_id, agent_slug?, project_id?, limit)`, `get_session(id)`+ownership, `rename_session`, `soft_delete_session`, `bump_counters(id, tokens, +2)`, `get_messages(session_id, limit) chronological`, `append_message(role, content, [agent_id, tokens, metadata])`, `resolve_session_project(id)`, `resolve_session_team(id)`, plus session-memory load/upsert/delete keyed by session id.

---

## 2. Behavior parity checklist

Each item = one testable behavior. Verify tier: **U**=unit, **DB**=real-DB smoke, **E2E**=live signup→signin→API (per MEMORY `bug_chat_message_endpoints_500_asyncpg_types` — mocked tests miss asyncpg type bugs; DB endpoints need a real-DB smoke before "done").

### Session lifecycle
- [ ] Create session resolves `agent_slug → agent_id` at create-time; 404 on unknown slug (`:87-92`). **U**
- [ ] Create persists `status='active'`, `total_tokens=0`, `message_count=0`, and optional `project_id/team_id/context_type/context_id` only when provided (`:104-111`). **DB**
- [ ] `POST /agents/{slug}/sessions` returns `SessionOut` with `id` as numeric string (not UUID), 201 (`ai_library_router.py:1757`, `SessionOut.id: str` coerce_numbers_to_str). **E2E**
- [ ] List returns caller-owned sessions only, `status='deleted'` filtered out, ordered `updated_at DESC`, honoring `agent_slug`+`project_id`+`limit` filters (`:137-150`). **DB**
- [ ] List `limit` clamped to 1..200 at router (`:1795`). **U**
- [ ] Get enforces ownership: returns 404 (not 403) when `row.user_id != caller` — must not leak existence (`:168-172`). **DB**
- [ ] Get session detail returns session merged with full `messages[]` (`ai_library_router.py:1809-1814`). **E2E**
- [ ] Rename updates `title` only; owner-only; no-op PATCH returns current session (`:199-216`). **DB**
- [ ] Delete is SOFT (`status='deleted'`); messages retained for audit (`:218-227`). **DB**
- [ ] `updated_at` auto-advances so a chatted-in session floats to top of the list (today implicit via DB default/trigger on any UPDATE — S6 counter bump touches the row every turn). **DB**

### Message send / receive
- [ ] History loaded once per turn, chronological, capped 200 (`:445`, M1). **U**
- [ ] User message persisted BEFORE model call so a mid-turn failure still records the attempt (`:449-462`, M2). **DB**
- [ ] Only `role ∈ {user,assistant,system}` rows replayed into the model payload; others skipped (`:601-605`). **U**
- [ ] Assistant message persists `agent_id`, `prompt_tokens`, `completion_tokens`, `metadata_json` (M3). **DB**
- [ ] `metadata_json` folds `run_id`, `tool_calls` trace, and `awaiting_approval` so a page-reload re-renders sub-task/approval cards (`:978-992`). **E2E**
- [ ] Non-streaming `POST /sessions/{id}/chat` returns `{message, usage, run_id, tool_calls, attachment_failures}` (`ai_library_router.py:1864-1870`). **E2E**
- [ ] Session counters bump `total_tokens += turn`, `message_count += 2` per turn (S6). Verify read-modify-write doesn't lose updates under the per-user concurrency gate. **DB**
- [ ] `MessageOut.session_id` serializes as numeric string; `id` stays UUID today (schema §3.1). **U**

### Compaction & session memory
- [ ] Compaction triggers only above `DEFAULT_AUTO_COMPACTION_INPUT_TOKENS` (`:1189`). **U**
- [ ] Compaction preserves tool_use/result pairs (won't 400 next call) and degrades to full history on failure (`:1259-1262`). **U**
- [ ] Compaction prefers cached `ai_session_memory.body_md` as head summary over a fresh LLM call when `session_id` present (SM1 loader, `:1194-1206`). **DB**
- [ ] Fire-and-forget session-memory updater dispatched post-turn with `full_messages = user_messages + assistant` (`:1043-1054`); failure is non-fatal (`:1055`). **U**
- [ ] `maybe_update_session_memory` upserts on dual-threshold (tokens/tool_calls/turns baselines) and bumps `version` (SM2). **DB**
- [ ] Per-message token cap truncates single oversized messages (paste-a-200k-log case) before compaction (`:806-822`). **U**

### Recall stack (Honcho L2 + Graphiti L3 + agent_memory)
- [ ] Recall runs BEFORE prompt composition, concurrently, under `MEMORY_RECALL_BUDGET_S=4.0` wall-clock; timeout → no memory, turn proceeds (`ai_library_chat_wiring.py:187-205`). **U**
- [ ] Graphiti recall keys on `group_ids=[user-{id}]` + `project-{project_id}` when session has a project (`:487-491`); project resolved via S7 raw SQL (`:584-603`). **DB**
- [ ] Honcho recall keys workspace on `team-{team_id}` via S8 raw SQL, else deployment default (`:522-527`, `write_memory.py:181-197`). **DB**
- [ ] Honcho "About me" card outranks derived representation (`:534-546`). **U**
- [ ] Graph + Honcho both suppressed when per-user `memory_prefs.inject=false` (`:485`, `:519`). **U**
- [ ] agent_memory recall flag-gated on `FEATURE_AGENT_MEMORY`; returns `[]` when off; fetches user `team_ids` inside the gate, degrades to owner-only on team-fetch failure (`:552-581`). **U**
- [ ] `MemoryContext.session_id` int-coerced from snowflake str; UUID test ids resolve to None safely (`:170-185`). **U**
- [ ] All three recall results flow into `ComposerInput` (`graph_facts`, `user_context`, `agent_memory_facts`) (`ai_library_chat_service.py:585-592`). **U**

### Plan mode
- [ ] `plan_mode ∈ {prompt_user, dry_run}` swaps `request_instructions` for `build_plan_prompt()` (`:502-505`). **U**
- [ ] `plan_mode` absent/`auto` → normal interactive instructions incl. Delegate hint (`:506-519`). **U**
- [ ] Plan-mode response persists as a normal assistant message (plan markdown); approval/reject is the next user turn. **E2E**

### Media tools (GenerateImage / GenerateVideo)
- [ ] Tools injected ONLY when `FEATURE_AGENT_MEDIA_TOOLS` truthy (`_media_tools_enabled`, `:44-53`, `:714-730`). **U**
- [ ] When enabled, both tool specs appended to `composed.tools` and handlers bound to runner (`:721-729`). **U**
- [ ] When flag off, zero tools added — behavior-neutral. **U**
- [ ] Generated media persistence path must map to `message_attachments.generated_media_id` in the new store (schema §3.7). **DB**

### FinishIssue & commitments
- [ ] FinishIssue tool + `FINISH_ISSUE_INSTRUCTION` injected ONLY when `trigger ∈ {issue_dispatch, issue_reply}`; never on chat turns (`:736-754`). **U**
- [ ] `finish_issue_handler` cleared in `finally` so a later non-issue turn on the same runner can't accept it (`:900`). **U**
- [ ] Issue outcome extracted from returned `tool_calls` trace by `issue_agent_executor` (`:96`, `extract_issue_outcome`). **U**
- [ ] `run_session_turn` is shared by chat AND issues; both funnel the per-user concurrency gate `user_slot` (`:386-397`). **U**
- [ ] First-turn `NEXT_SESSION` commitments surfaced into `request_instructions` and marked fulfilled once (`:521-533`, `_surface_next_session_commitments:1265-1307`). Keyed on `agent_id`+`user_id` (NOT session). **DB**
- [ ] Post-turn commitment harvester dispatched fire-and-forget with pre-filter; non-fatal on failure (`:1058-1145`). **U**

### Token caps & cost / budget
- [ ] RunRecorder pre-flight raises `AgentPausedError` for a paused agent → 409 CONFLICT; NO agent_runs row, NO assistant message (`:845-856`, `:927-934`, run_recorder `:58`). **DB**
- [ ] BudgetGuard hook registered when `budget_per_run_cents` set; blocks over-budget (`ai_library_chat_wiring.py:223-228`). **U**
- [ ] Per-agent tool-call rate limit + capability gate registered only when profile present (`:214-243`). **U**
- [ ] `usage` snapshot (`prompt_tokens`/`completion_tokens`) pulled off recorder as single source of truth, written to both the assistant message and returned (`:905-908`, M3). **DB**

### Attachments & binary input
- [ ] Attachments normalized from Pydantic `AttachmentRequest` OR plain dict; unknown types dropped silently (`:614-621`). **U**
- [ ] Split by kind: `resource_ref` → resolver+ResourceFetch tool; others → binary path (`:623-629`). **U**
- [ ] resource_ref resolution extends system message with `<available_resources>`, registers `ResourceFetch` tool + per-turn closure, cleared in `finally` (`:643-710`, `:895-897`). **U**
- [ ] resource_ref warnings prepended to user content (`:756-763`). **U**
- [ ] Binary attachments → multimodal `build_user_message`; vision-gated by `model_supports_vision`; failures degrade to text-only with placeholder (`:768-798`). **U**
- [ ] `attachment_failures` surfaced in both `/chat` and `/chat-stream` responses (`:1155-1158`, `:329-332`). **E2E**
- [ ] Upload endpoint `/chat-attachments/upload`: ext allow-list + magic-byte check + 50MB cap; persists to shared-library temp resource (worker-readable), `session_id` routes to team scope (`ai_library_router.py:2377-2496`). **DB**

### Title generation
- [ ] There is NO server-side auto-title today — `title` comes only from `SessionCreate.title` (default `'New Chat'`) or explicit rename (grep confirmed: no `generate_title` in chat path). Parity = keep default + manual rename; do NOT silently add auto-titling during the swap. **U**

### Streaming
- [ ] `/sessions/{id}/chat-stream` emits SSE `event: delta` (text+offset) → `event: done` (message_id, usage, run_id, tool_calls, total_chars, attachment_failures) → `event: error` on failure (`:300-333`, `ai_library_router.py:1901-1925`). **E2E**
- [ ] Streaming drives `runner.stream_turn` with `auto_recorder=False` (recorder owned by service); accumulates chunks; `chunk_callback` failure must not kill the turn (`:871-891`). **U**
- [ ] Streaming path still persists user+assistant messages, bumps counters, dispatches memory/commitment harvesters (same tail as buffered — `result` backfilled at `:922-926`). **DB**
- [ ] Streaming write ordering: assistant message persisted AFTER stream completes with full accumulated text (`:891`, `:993`) — NOT incrementally. Adapter must preserve "one final assistant row per turn". **DB**
- [ ] Tool-using turns in streaming currently degrade to one-shot (documented `:1889-1892`); tool_calls visible via synthetic delta text only. **U**

### Telemetry (RunRecorder, agent_runs.session_id)
- [ ] Every turn creates exactly one `agent_runs` row with `session_id`, `team_id`, `project_id`, `trigger`, `model`, `provider` (`:845-856`, R1). **DB**
- [ ] `run_id` threaded into response AND assistant `metadata_json` for chat→Runs deep-link (`:979-980`, `:1151`). **E2E**
- [ ] `trigger` distinguishes `chat` vs `issue_dispatch`/`issue_reply` in agent_runs (`:366`, `:736`). **U**
- [ ] Dashboard/runs/live read paths scope by `agent_id`+`user_id` (unaffected by store swap, but `session_id` FK must stay valid — §3.6). **DB**

### Error paths
- [ ] 404 when session missing/not owned (`:164-172`). **U**
- [ ] 400 when session has no `agent_slug` bound (`:357-361`, `:439-443`). **U**
- [ ] 404 when bound `agent_slug` no longer resolves at turn time (`:470-475`). **U**
- [ ] 409 on paused agent (`:931-934`). **U**
- [ ] 502 BAD_GATEWAY when `result.error` set (`:936-940`). **U**
- [ ] await_approval row persisted (approval_requests) + folded into assistant metadata; non-fatal on persist failure (`:942-972`). **DB**
- [ ] Every best-effort block (link_injection, resource_ref, session_memory, commitments, MCP wiring) logs + continues; a failure NEVER breaks the turn (`:542-561`, `:634-641`, `:1055`, `:1142`). **U**
- [ ] Per-user concurrency gate `user_slot` serializes a user's concurrent turns — read-modify-write counter safety depends on it (`:386-397`, §4). **DB**

---

## 3. Impedance mismatches (1:1 model → `conversations`/`messages`)

For each: the mapping decision the Phase 2 plan MUST make. Options + recommendation.

**3.1 — `ai_messages.id` is UUID; `messages.id` is BIGINT snowflake.**
`MessageOut.id: UUID` (schema). Options: (a) keep dual ids, map UUID↔bigint; (b) migrate `MessageOut.id` to str+coerce like `session_id` already is. → **Rec (b):** widen `MessageOut.id` to numeric-string (mirror the `session_id` fix already shipped); UUID typing will 422 on real `messages` rows.

**3.2 — Ordering key: `ai_messages.created_at` vs `messages.seq`.**
Service orders history by `created_at ASC` (M1); `messages` has a monotonic `(conversation_id, seq)` unique. → **Rec:** order by `seq` in the new adapter (stronger than timestamp; avoids same-ms tie ambiguity). Adapter must allocate `seq = conversations.last_seq+1` on append (the conversations store already does this for team chat).

**3.3 — `ai_messages.role` (system/user/assistant) vs `messages.sender_type` (user/agent/system) + `type`.**
Chat writes `role='assistant'`; conversations model an assistant as `sender_type='agent'` + `from_agent_id`. → **Rec:** adapter maps `role='assistant' → sender_type='agent', from_agent_id=agent_id`; `role='user' → sender_type='user', sender_id=user_id`. Content goes in `body JSONB` (`{text: ...}`) not a `content TEXT` column — the service's `content` string must round-trip through `body`.

**3.4 — Per-message decoration columns with no `messages` equivalent.**
`ai_messages` carries `agent_id, prompt_tokens, completion_tokens, metadata_json` (M3). `messages` has only `body JSONB`. → **Rec:** fold `{agent_id, prompt_tokens, completion_tokens, run_id, tool_calls, awaiting_approval}` into `messages.body` (or a `meta` key). Page-reload rendering of sub-task/approval cards (§2) depends on this surviving — do NOT drop it.

**3.5 — Session-scoped sidecar fields → conversation state.**
`ai_sessions` carries `total_tokens, message_count, agent_slug, context_type, context_id, status` — `conversations` has none of these (it has `last_seq, archived_at, topic, history_mode`). Options: (a) sidecar table `conversation_state`/`conversation_ai_meta`; (b) stuff into a jsonb column. → **Rec (a):** a `conversation_ai_meta(conversation_id PK, agent_slug, total_tokens, context_type, context_id, status, ...)` sidecar — keeps counters (S6) and list-filtering (S2 by `agent_slug`) working without polluting the shared conversations schema. `status='deleted'` soft-delete → map to `conversations.archived_at` (§3.9).

**3.6 — `ai_session_memory.session_id` PK + `agent_runs.session_id` FK are BIGINT → `ai_sessions.id`.**
After the swap, "session id" becomes `conversations.id` (also BIGINT snowflake — same type, good). → **Rec:** repoint `ai_session_memory` (SM1-3) and `RunRecorder.session_id` (R1) at `conversations.id`. Since both are already BIGINT snowflake, this is an FK re-target, not a type change — but the `_bigint()` coercion and the two raw-SQL lookups (S7 project, S8 team) must switch `FROM ai_sessions` → `FROM conversations` (columns `project_id` exists on conversations; team is `scope_id`, NOT `team_id` — see §3.8).

**3.7 — GenerateImage/Video output has no attachment link in `ai_messages`.**
`messages` has `message_attachments(message_id, generated_media_id)` + `message_refs`. → **Rec:** on the new store, media-tool outputs link via `message_attachments`; resource_ref @-mentions via `message_refs(ref_type='resource')`. Legacy adapter has nowhere to put these (keeps folding into `metadata_json`), so this capability is store-asymmetric — flag it as "new-store-only enrichment", not a parity regression.

**3.8 — `ai_sessions.team_id` (BIGINT, nullable) vs `conversations.scope_id` (BIGINT, NOT NULL → teams).**
Honcho workspace + RunRecorder team tagging read `session.team_id` (S8, `:850`). Conversations REQUIRE a team scope (`scope_id NOT NULL`). A 1:1 agent DM today can have NULL team_id (personal chat). → **Rec:** decide the "personal DM" scope — either a per-user personal team, or relax to allow a sentinel. This is the sharpest structural mismatch: **the legacy model allows teamless sessions; the target model does not.** Phase 2 plan MUST pick a home for personal 1:1 chats before migrating.

**3.9 — `direct_agent` conversation creation requires 2 members; `create_session` creates 0.**
`create_session` writes one `ai_sessions` row, no membership rows. A `conversations` direct_agent needs `conversation_members` = `[{user}, {agent}]` (schema §327). → **Rec:** adapter's `create_session` becomes: insert conversation `type='direct_agent', scope_id=<team>, created_by=user` + two `conversation_members` rows. Soft-delete (S5 `status='deleted'`) → set `archived_at` and/or `conversation_members.open=false`.

**3.10 — `context_type`/`context_id` free-form grouping hints.**
Used by clients to group sessions ('script','storyboard'); no conversations equivalent. → **Rec:** carry in the `conversation_ai_meta` sidecar (§3.5) — cheap, preserves S2 semantics.

**3.11 — `updated_at`-ordered session list vs `last_seq`/`created_at` conversations.**
S2 orders by `ai_sessions.updated_at DESC` (bumped every turn via S6). `conversations` has no `updated_at`. → **Rec:** order by `last_seq` movement or add `conversation_ai_meta.updated_at`; ensure "just-chatted floats to top" (§2 lifecycle) survives.

---

## 4. Risk ranking — top 5 riskiest swaps

1. **Personal/teamless sessions vs `scope_id NOT NULL` (§3.8/3.9).** Highest. The legacy 1:1 chat allows NULL team; the target store structurally forbids a teamless conversation and requires membership rows. Getting this wrong either blocks personal chat entirely or silently mis-scopes every DM into the wrong team (data-leak class). Must be decided before any migration row moves.

2. **Compaction head-summary correctness (§2 compaction, SM1).** Compaction feeds the model's context. If the `ai_session_memory`↔conversation repoint or the message ordering (§3.2) is off, compaction either drops the wrong head, breaks tool_use/result pairing (→ hard 400 next call), or serves a stale/foreign session's memory. Silent context corruption is hard to detect in tests (degrades gracefully by design, §2), so a real-DB smoke comparing compacted token counts before/after is mandatory.

3. **Recall context keys (§2 recall, S7/S8).** Graphiti keys on `project-{id}`, Honcho on `team-{id}` — both via raw `SELECT ... FROM ai_sessions`. If these lookups aren't repointed to `conversations` (project_id exists; team is `scope_id` not `team_id`), recall silently returns the wrong user's/team's memories or none — a cross-tenant leak or a quality regression that no unit test with mocked recall will catch.

4. **Streaming write ordering (§2 streaming).** Today exactly one assistant row is persisted AFTER the stream fully accumulates (`:891`→`:993`), and counters bump once (S6). If the new adapter writes incrementally or the per-user gate (§4/`user_slot`) interacts badly with `seq` allocation, you get duplicate/partial assistant rows or a `(conversation_id, seq)` unique violation mid-stream. The read-modify-write counter (S6) is only safe under the concurrency gate.

5. **Per-message decoration loss (§3.4) → telemetry deep-link + card re-render.** `metadata_json` (run_id, tool_calls, awaiting_approval) and per-message token columns have no native `messages` home. If they don't cleanly fold into `body`, a page-reload stops rendering sub-task/approval cards and chat→Runs deep-linking breaks (`run_id` orphaned). Low blast radius but easy to miss because the send-path response still carries the data — only the persisted re-fetch regresses.
