# Conversations Phase 2 — 1:1 direct_agent Fold (storage-adapter strangler)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fold the 1:1 AI chat's storage (`ai_sessions`/`ai_messages`/`ai_session_memory`) onto the canonical `conversations`/`messages` store via a `MessageStore` adapter — **service/turn logic and the `/sessions/*` API contract stay untouched, the frontend does not change** — gated by a three-mode flag with shadow-compare before cutover.

**Architecture:** Extract every storage call `AILibraryChatService` makes (the 16-op inventory in the parity checklist §1) behind a `MessageStore` Protocol. `LegacyAiStore` wraps today's exact calls (pure extraction, zero behavior change). `ConversationsAiStore` implements the same interface on `conversations` + `messages` + a new `conversation_ai_meta` sidecar, REUSING `ConversationRepository` (atomic seq alloc, `get_conversation`) wherever possible. A store ROUTER dispatches per-session: ids found in `ai_sessions` → legacy; else → conversations (no data migration, per D2 — old sessions live out their lives on the old store; new sessions land on the new store when the flag is on). Sidecars repoint: `ai_session_memory` keys accept conversation ids (FK dropped), `agent_runs` uses the existing `conversation_id` column (mig 331) for new-store turns, and the two raw-SQL scope lookups (S7 project / S8 team) become store-aware.

**Tech Stack:** FastAPI + supabase-py (PostgREST) + SQLAlchemy `db_engine` (asyncpg); existing `ConversationRepository`; pytest + real-DB smokes (`INTEGRATION_DATABASE_URL`).

## Global Constraints

- **Branch:** `feature/conversations-p2` off latest `origin/master`. Commit this plan as the first commit.
- **DONE-GATE:** `docs/superpowers/specs/2026-07-03-phase2-parity-checklist.md` — all 62 behavior items must pass on the new store (final task produces the signed-off copy). A green mocked suite is NOT done.
- **NO REWRITE of `AILibraryChatService`.** Tasks 2's refactor is mechanical extraction: every storage call moves behind the adapter with argument-identical semantics; the service's control flow, error handling, and hooks are byte-preserved. Any diff beyond call-site substitution is a review defect.
- **`/sessions/*` API contract frozen:** request/response shapes unchanged, except the one planned widening — `MessageOut.id` becomes numeric-string-or-UUID-string (checklist §3.1). No other schema drift.
- **No data migration (D2/D10):** zero backfill; `ai_*` rows stay where they are; the router serves both stores concurrently forever (until Phase 3 retires legacy after old sessions age out or a later explicit migration).
- **Flag:** `FEATURE_DIRECT_CONVERSATIONS: str = Field(default="off")`, values `off | shadow | on`. `off` = pure legacy (default; merging this plan is behavior-neutral). `shadow` = legacy authoritative + best-effort mirror writes to the new store + read-diff logging. `on` = NEW sessions created on the conversations store; existing legacy sessions keep routing to legacy.
- **Personal DM scope (locked, spec + checklist §3.8):** `scope_id` = the user's personal team, resolved via `_resolve_personal_team_id` (`app/services/library/chat_upload.py`); a session created WITH `team_id` uses that team. A NULL resolution (user has no personal team) must fail loud at create (ValueError → 400), never silently mis-scope.
- **Second caller (checklist §2 FinishIssue):** `issue_agent_executor` drives `run_session_turn` with `trigger='issue_dispatch'` — every task that touches the turn path must keep the issue path green (its tests run in the regression gate).
- **Concurrency invariant (checklist risk #4):** the S6 read-modify-write counter is only safe under the per-user `user_slot` gate; the new store's counter bump and seq allocation must not add cross-user contention or break one-final-assistant-row-per-turn streaming semantics.
- **Migration numbering:** next free is **332** (verify with `ls supabase/migrations | grep -E '^33' | sort` before writing; renumber if a parallel PR took it).
- **asyncpg DoD:** `_bigint()`/`int()` coercion on every BIGINT bind; NULL comparison binds wrapped `CAST(... AS bigint)`; real-DB smoke per DB task (`INTEGRATION_DATABASE_URL=postgresql://postgres:postgres@127.0.0.1:54322/postgres`; local DB has conversations tables 327-331 applied; `ai_sessions`/`ai_messages` may need mig 121/138/187/231 applied locally first — apply on demand like Phase 1.5 Task 2 did).
- **Repo-wide governance test:** any task adding an LLM call runs `tests/test_run_recorder_coverage.py`.
- **Lint:** `cd backend && uv run black <files> && uv run isort <files> && uv run flake8 <files>` per commit. Loguru f-strings only.
- **Joined-mode REST gate (final-review carryover):** Task 4 adds the `history_mode='joined'` cutoff to `ConversationRepository.list_messages` — required before any non-`shared` conversation ships.

## File Structure

- `supabase/migrations/332_direct_agent_sidecars.sql` — `conversation_ai_meta` sidecar + drop `ai_session_memory_session_id_fkey`.
- `backend/app/services/ai/chat/message_store.py` — NEW: `MessageStore` Protocol + shared row-shape helpers.
- `backend/app/services/ai/chat/legacy_ai_store.py` — NEW: extraction of today's 16 ops.
- `backend/app/services/ai/chat/conversations_ai_store.py` — NEW: the canonical-store implementation.
- `backend/app/services/ai/chat/store_router.py` — NEW: per-session dispatch + shadow mirror/diff.
- `backend/app/services/ai/chat/ai_library_chat_service.py` — MODIFY: storage calls → `self._store.*` (mechanical).
- `backend/app/services/ai/chat/ai_library_chat_wiring.py` + `backend/app/workflows/write_memory.py` — MODIFY: S7/S8 store-aware lookups.
- `backend/app/api/ai_library_router.py` — MODIFY: `MessageOut.id` widening only.
- `backend/app/core/config.py` — MODIFY: add the flag.
- Tests alongside each; final sign-off doc `docs/superpowers/specs/2026-07-03-phase2-parity-checklist.md` (checkboxes ticked with evidence links).

---

## Task 1: Migration 332 — conversation_ai_meta sidecar + memory FK unlock

**Files:** Create `supabase/migrations/332_direct_agent_sidecars.sql`.

**Interfaces produced:** table `conversation_ai_meta` (consumed by Tasks 3/5); `ai_session_memory.session_id` accepts conversation ids (consumed by Task 6).

- [ ] **Step 1: Verify numbering.** `ls supabase/migrations | grep -E '^33' | sort` → expect max 331; renumber this file if taken.
- [ ] **Step 2: Write the migration.** Exact content:

```sql
-- 332_direct_agent_sidecars.sql — Phase 2: 1:1 direct_agent fold.
-- (1) conversation_ai_meta: session-scoped fields ai_sessions carries that the
--     shared conversations schema deliberately lacks (parity checklist §3.5/3.10/3.11).
--     One row per direct_agent conversation. Backend-only (service-role RLS).
-- (2) ai_session_memory.session_id: drop the FK to ai_sessions so the SAME
--     BIGINT key can hold a conversations.id for new-store sessions (§3.6).
--     Rows remain keyed one-per-session; integrity is app-enforced post-fold.

CREATE TABLE IF NOT EXISTS public.conversation_ai_meta (
  conversation_id BIGINT PRIMARY KEY
                  REFERENCES public.conversations(id) ON DELETE CASCADE,
  agent_slug      TEXT        NOT NULL,
  agent_id        UUID,
  total_tokens    BIGINT      NOT NULL DEFAULT 0,
  message_count   INTEGER     NOT NULL DEFAULT 0,
  context_type    TEXT,
  context_id      TEXT,
  updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_conv_ai_meta_slug
  ON public.conversation_ai_meta (agent_slug);

ALTER TABLE public.conversation_ai_meta ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS conversation_ai_meta_service_all ON public.conversation_ai_meta;
CREATE POLICY conversation_ai_meta_service_all ON public.conversation_ai_meta
  FOR ALL USING (
    current_setting('request.jwt.claims', true)::jsonb->>'role' = 'service_role'
  );

ALTER TABLE public.ai_session_memory
  DROP CONSTRAINT IF EXISTS ai_session_memory_session_id_fkey;

COMMENT ON TABLE public.conversation_ai_meta IS
  'AI-session decoration for direct_agent conversations (agent binding, token/'
  'message counters, client grouping hints). Phase 2 sidecar; one row per '
  'direct_agent conversation. Backend-only (service-role RLS).';
```

- [ ] **Step 3: Apply locally + verify.** `PGPASSWORD=postgres psql -h 127.0.0.1 -p 54322 -U postgres -d postgres -v ON_ERROR_STOP=1 -f supabase/migrations/332_direct_agent_sidecars.sql`. (If `ai_session_memory` is missing locally, first apply `supabase/migrations/187_*.sql` — local snapshot drift is known; report what you applied.) Verify: column list of `conversation_ai_meta` matches; `SELECT conname FROM pg_constraint WHERE conname='ai_session_memory_session_id_fkey';` returns empty.
- [ ] **Step 4: Commit.** `git add supabase/migrations/332_direct_agent_sidecars.sql && git commit -m "feat(ai): conversation_ai_meta sidecar + unlock ai_session_memory key (mig 332)"`

---

## Task 2: MessageStore Protocol + LegacyAiStore extraction (the strangler seam)

**Files:** Create `backend/app/services/ai/chat/message_store.py`, `backend/app/services/ai/chat/legacy_ai_store.py`; Modify `backend/app/services/ai/chat/ai_library_chat_service.py`; Test `backend/tests/test_legacy_ai_store.py`.

**Interfaces produced (both stores implement; Task 3/4/5 consume):**

```python
class MessageStore(Protocol):
    async def create_session(self, *, user_id: str, agent_slug: str, agent_id: str,
                             title: str, project_id: Optional[int], team_id: Optional[int],
                             context_type: Optional[str], context_id: Optional[str]) -> dict: ...
    async def list_sessions(self, *, user_id: str, agent_slug: Optional[str],
                            project_id: Optional[int], limit: int) -> list[dict]: ...
    async def get_session(self, *, session_id: int) -> Optional[dict]: ...   # ownership stays in service
    async def rename_session(self, *, session_id: int, title: str) -> dict: ...
    async def soft_delete_session(self, *, session_id: int) -> None: ...
    async def bump_counters(self, *, session_id: int, add_tokens: int, add_messages: int) -> None: ...
    async def get_messages(self, *, session_id: int, limit: int = 200) -> list[dict]: ...
    async def append_user_message(self, *, session_id: int, user_id: str, content: str) -> dict: ...
    async def append_assistant_message(self, *, session_id: int, agent_id: Optional[str],
                                       content: str, prompt_tokens: int, completion_tokens: int,
                                       metadata: dict) -> dict: ...
```

Row-shape contract: every method returns dicts shaped EXACTLY like today's `ai_sessions`/`ai_messages` rows (keys `id, user_id, agent_slug, title, status, total_tokens, message_count, project_id, team_id, context_type, context_id, created_at, updated_at` / `id, session_id, role, content, agent_id, prompt_tokens, completion_tokens, metadata_json, created_at`) — the service and router serializers must not need to change. The NEW store adapts its rows INTO this shape (Task 3/4).

- [ ] **Step 1: Read the inventory.** Parity checklist §1a/1b rows S1-S6, M1-M3 give the exact call sites and column sets — this task moves those nine ops (S7/S8 and SM1-3 are outside the service and move in Task 6).
- [ ] **Step 2: Write failing tests** `test_legacy_ai_store.py`: mock `get_async_supabase_admin` with a capture client (copy the harness style from an existing supabase-mocked test); assert per op that table/filters/payload match the checklist inventory exactly (e.g. `create_session` inserts `status='active', total_tokens=0, message_count=0` and omits optional cols when None (S1); `list_sessions` applies `.neq(status,'deleted')` + `updated_at desc` (S2); `append_assistant_message` writes `metadata_json` (M3)). Run → FAIL.
- [ ] **Step 3: Implement `legacy_ai_store.py`** by MOVING the code of each call site verbatim from `ai_library_chat_service.py` into the corresponding method (keep every column literal, every `.eq/.neq/.order/.limit` identical).
- [ ] **Step 4: Refactor the service** — replace each moved block with `await self._store.<method>(...)`; `AILibraryChatService.__init__` gains `store: Optional[MessageStore] = None` defaulting to `LegacyAiStore()`. NOTHING else changes (ownership checks, error mapping, hooks stay in the service).
- [ ] **Step 5: Regression gate.** `cd backend && uv run pytest tests/ -k "ai_library or session or issue_agent" -q` → the ENTIRE existing 1:1-chat + issue-executor suite passes unchanged (this proves the extraction was mechanical). Then the new store tests: `uv run pytest tests/test_legacy_ai_store.py -v` → pass.
- [ ] **Step 6: Lint + commit.** `git commit -m "refactor(ai): extract MessageStore seam + LegacyAiStore (zero behavior change)"`

---

## Task 3: ConversationsAiStore — session lifecycle

**Files:** Create `backend/app/services/ai/chat/conversations_ai_store.py`; Test `backend/tests/test_conversations_ai_store.py`.

**Interfaces consumed:** `get_conversation_repository()` (create/get + `db_engine` style), `conversation_ai_meta` (Task 1), `_resolve_personal_team_id` (`app.services.library.chat_upload`).
**Interfaces produced:** `ConversationsAiStore` implementing the session half of `MessageStore`, returning LEGACY-SHAPED dicts (Task 2 contract).

Key mappings (from checklist §3):
- `create_session`: resolve scope = `team_id or await _resolve_personal_team_id(user_id)`; None → `raise ValueError("no personal team")`. Insert `conversations(type='direct_agent', scope_id=scope, project_id=project_id, title=title, created_by=user_id)` + `conversation_members` rows `[{'user', user_id, role 'owner'}, {'agent', agent_id}]` (reuse repo SQL patterns; agent row via `add_agent_member`) + `conversation_ai_meta(conversation_id, agent_slug, agent_id, context_type, context_id)`. Return the legacy-shaped dict (`id=conv.id, status='active', total_tokens=0, message_count=0, team_id=scope, ...`).
- `list_sessions`: `SELECT c.*, m.* FROM conversations c JOIN conversation_ai_meta m ON m.conversation_id=c.id JOIN conversation_members cm ON cm.conversation_id=c.id AND cm.member_type='user' AND cm.user_id=:uid WHERE c.type='direct_agent' AND c.archived_at IS NULL [AND m.agent_slug=:slug] [AND c.project_id=:pid] ORDER BY m.updated_at DESC LIMIT :n` — legacy-shaped rows (`status='active'`, `team_id=c.scope_id`, `updated_at=m.updated_at`).
- `get_session`: conversation + meta joined; `user_id` = the sole `member_type='user'` member's id (needed for the service's ownership check); archived → treat as deleted (return None so the service 404s, matching `status='deleted'` filtering).
- `rename_session`: `UPDATE conversations SET title=:t WHERE id=:cid` + touch `conversation_ai_meta.updated_at`.
- `soft_delete_session`: `UPDATE conversations SET archived_at=now() WHERE id=:cid`.
- `bump_counters`: `UPDATE conversation_ai_meta SET total_tokens=total_tokens+:t, message_count=message_count+:m, updated_at=now() WHERE conversation_id=:cid` — **atomic increment in SQL** (safe upgrade over the legacy read-modify-write; the service still calls it once per turn under `user_slot`).

- [ ] **Step 1: Write failing tests** (fake-engine SQL-shape harness, copy `test_conversation_repository.py`'s): create inserts conversation + 2 members + meta and resolves personal team when team_id None; ValueError when personal team unresolvable; list filters `type='direct_agent'`, `archived_at IS NULL`, member join, slug/project filters, `m.updated_at DESC`; get returns None for archived; bump uses `total_tokens = total_tokens + :t` (atomic SQL, not read-modify-write); every returned dict carries the legacy keys (`status`, `team_id`, `message_count`, ...). Run → FAIL.
- [ ] **Step 2: Implement** the session half (message methods `raise NotImplementedError` until Task 4).
- [ ] **Step 3: Tests pass.**
- [ ] **Step 4: Real-DB smoke** (INTEGRATION_DATABASE_URL-gated, reuse the Phase 1.5 smoke fixture pattern): create → appears in list → rename → get shows title + user_id → soft-delete → absent from list, get → None. Run for real; paste output.
- [ ] **Step 5: Lint + commit.** `git commit -m "feat(ai): ConversationsAiStore session lifecycle on canonical store"`

---

## Task 4: ConversationsAiStore — messages (+ MessageOut widening + joined-gate carryover)

**Files:** Modify `backend/app/services/ai/chat/conversations_ai_store.py`, `backend/app/api/ai_library_router.py` (MessageOut only), `backend/app/repositories/conversation_repository.py` (joined-gate); Test extend `backend/tests/test_conversations_ai_store.py` + `backend/tests/test_conversation_repository.py`.

Key mappings:
- `append_user_message`: reuse `ConversationRepository.send_message(conversation_id, sender_id=user_id, sender_type='user', type='text', body={'text': content})` (atomic seq alloc + read-cursor advance already proven) → adapt the returned row to legacy shape (`role='user'`, `content=body['text']`, `session_id=conversation_id`).
- `append_assistant_message`: `send_message(..., sender_id=None, sender_type='agent', type='text', from_agent_id=agent_id, body={'text': content, 'meta': {'agent_id': ..., 'prompt_tokens': ..., 'completion_tokens': ..., **metadata}})` — decoration folds into `body.meta` (checklist §3.4; run_id/tool_calls/awaiting_approval MUST survive the round-trip).
- `get_messages`: `SELECT ... WHERE conversation_id=:cid AND deleted_at IS NULL ORDER BY seq ASC LIMIT :n` → map each row to legacy shape: `role = 'agent'→'assistant' / 'user'→'user' / 'system'→'system'` (from `sender_type`), `content = body['text']`, `agent_id/prompt_tokens/completion_tokens/metadata_json` unpacked from `body.meta` (absent keys → None/{}), `id = str(row.id)`, `session_id = str(conversation_id)`, `created_at` passthrough.
- **MessageOut widening (§3.1):** change `MessageOut.id: UUID` → `id: str` with a `mode="before"` validator `str(v)` (mirrors the shipped `session_id` fix). Grep the frontend for `message.id` consumers (list keys only expected) — note findings in the report.
- **Joined-gate carryover:** add to `ConversationRepository.list_messages` the `history_mode='joined'` cutoff matching the RLS in mig 328 (`messages.created_at >= conversation_member_joined_at(user)` when `history_mode='joined'`) — needs the caller's user_id: add optional `for_user_id: Optional[str] = None` param; when provided AND the conversation is `joined`-mode, apply the cutoff via a join on `conversation_members`. `ConversationService.get_messages` passes the caller's id. (direct_agent conversations are created `history_mode='shared'`, so this doesn't bite Phase 2 — it closes the Phase-1 final-review carryover before non-shared modes exist.)

- [ ] **Step 1: Write failing tests:** role/content/meta round-trip both directions (append assistant with `metadata={'run_id':'r1','tool_calls':[...]}` → get_messages returns `metadata_json` containing them); ordering by seq ASC; `MessageOut(id=<int>)` validates to str; list_messages joined-gate SQL applies cutoff only when `for_user_id` given + mode joined. Run → FAIL.
- [ ] **Step 2: Implement.** **Step 3: Tests pass.**
- [ ] **Step 4: Real-DB smoke:** append user + assistant (with decoration) → get_messages returns both, chronological, decoration intact; counters via Task 3's bump; **the same smoke calls the real `AILibraryChatService.get_session_detail`-equivalent path with the store injected** to prove serializer compatibility end-to-end.
- [ ] **Step 5: Regression gate:** full `-k "ai_library or session or issue_agent"` suite still green.
- [ ] **Step 6: Lint + commit.** `git commit -m "feat(ai): ConversationsAiStore messages + MessageOut widening + joined-mode gate"`

---

## Task 5: Store router + flag + shadow-compare

**Files:** Create `backend/app/services/ai/chat/store_router.py`; Modify `backend/app/core/config.py`, `backend/app/services/ai/chat/ai_library_chat_service.py` (default store = router); Test `backend/tests/test_store_router.py`.

**Flag** (config.py, feature-flags block):

```python
    FEATURE_DIRECT_CONVERSATIONS: str = Field(
        default="off",
        description="1:1 chat storage routing: 'off' = legacy ai_* only; "
        "'shadow' = legacy authoritative + mirror writes to the conversations "
        "store with read-diff logging; 'on' = new sessions on the "
        "conversations store (existing legacy sessions keep serving from ai_*).",
    )
```

**Router semantics** (`RoutedAiStore(MessageStore)`, wrapping both stores):
- `create_session`: `on` → new store; `off`/`shadow` → legacy (shadow additionally mirrors the create into the new store best-effort, logging failures — never raising).
- Per-session ops: resolve which store owns `session_id` — probe legacy first (`legacy.get_session`); hit → legacy, miss → new store. Cache the resolution per-call (a plain dict arg-through, NOT a global — process-lifetime caches leak; the router is constructed per service call chain, keep it stateless beyond an instance-local `dict`).
- `shadow` reads: serve legacy; fire-and-forget `_shadow_diff(op, legacy_result, new_result)` that logs `[p2-shadow] MISMATCH op=... session=... field=...` (compare the legacy-shaped dicts key-by-key on a stable subset: role/content/title/status/counters). Never raises; sampling guard `_SHADOW_SAMPLE = 1.0` constant for later tuning.
- All new-store writes in shadow mode are best-effort try/except-log.

- [ ] **Step 1: Failing tests:** off → all ops hit legacy only; on + fresh create → new store, and subsequent ops on that id route to the new store while a legacy id still routes legacy (dual-serving); shadow → create hits BOTH (legacy result returned), a new-store mirror failure is swallowed, a read mismatch logs `[p2-shadow] MISMATCH`. Run → FAIL.
- [ ] **Step 2: Implement router; wire `AILibraryChatService` default store = `RoutedAiStore()`.**
- [ ] **Step 3: Tests pass + regression gate** (`-k "ai_library or session or issue_agent"`) with flag default `off`.
- [ ] **Step 4: Lint + commit.** `git commit -m "feat(ai): store router + FEATURE_DIRECT_CONVERSATIONS (off|shadow|on) + shadow diff"`

---

## Task 6: Sidecar repoints — session memory, RunRecorder, scope lookups

**Files:** Modify `backend/app/services/ai/chat/ai_library_chat_service.py` (RunRecorder kwargs), `backend/app/services/ai/chat/ai_library_chat_wiring.py` (S7), `backend/app/workflows/write_memory.py` (S8); Test extend `backend/tests/test_conversations_ai_store.py` + targeted tests.

- **Session memory (SM1-3):** keyed by BIGINT session id; after mig 332's FK drop, conversation ids key the SAME table via the SAME `SessionMemoryRepository` — no code change needed for the happy path. ADD a test proving `session_memory_repository.upsert/load` round-trips with a conversations id (real-DB smoke; the FK would have rejected it before mig 332).
- **RunRecorder:** the service currently passes `session_id=session_id, team_id=session.team_id, ...`. Change to store-aware: when the session is new-store (expose `store_kind` on the row dicts: `'legacy' | 'conversations'` — added by each store, ignored by serializers), pass `conversation_id=int(session_id), session_id=None` (mig 231 FK on `agent_runs.session_id` would reject a conversation id; `conversation_id` column exists from mig 331). Legacy sessions keep `session_id=...` exactly as today.
- **S7 (project recall scoping, wiring `_resolve_session_project`):** replace the raw `FROM ai_sessions` lookup with: try `SELECT project_id FROM public.conversations WHERE id=:sid`; if no row, fall back `SELECT project_id FROM public.ai_sessions WHERE id=:sid`. (Conversations-first is cheap and correct — ids are globally unique snowflakes across both tables... they are NOT guaranteed distinct sets; both generated by `generate_snowflake_id()` so collisions are impossible in practice — a snowflake is minted once. State this in a comment.)
- **S8 (Honcho team workspace, `write_memory._resolve_team_workspace`):** same dual lookup, but conversations column is **`scope_id`** (NOT `team_id` — checklist risk #3): `SELECT scope_id AS team_id FROM public.conversations WHERE id=:sid` → fallback legacy.

- [ ] **Step 1: Failing tests:** RunRecorder receives `conversation_id` + `session_id=None` for a `store_kind='conversations'` session and the legacy kwargs for legacy (capture via the recorder-payload harness from `test_run_recorder_conversation_id.py`); S7/S8 resolvers return the conversations value when present and fall back to ai_sessions (fake-engine, assert both SQL texts incl. `scope_id AS team_id`). Run → FAIL.
- [ ] **Step 2: Implement.** **Step 3: Tests pass + real-DB smoke** (session-memory round-trip on a conversation id).
- [ ] **Step 4: Regression gate + lint + commit.** `git commit -m "feat(ai): store-aware sidecars — session memory, agent_runs link, recall scope lookups"`

---

## Task 7: Issue-executor path + full-suite hardening

**Files:** Test `backend/tests/test_issue_agent_executor_p2.py` (NEW; reuse existing issue-executor test harness).

The second caller must be provably unaffected in all three flag modes (checklist §2 FinishIssue block):
- [ ] **Step 1: Tests:** with flag `off` / `shadow` / `on` (monkeypatched), `issue_agent_executor`'s session creation + `run_session_turn(trigger='issue_dispatch')` complete with the FinishIssue tool injected and outcome extracted — assert the store each mode lands on (off/shadow → legacy authoritative; on → new store) and that `trigger` reaches `agent_runs` kwargs unchanged.
- [ ] **Step 2: Full backend suite once** (`uv run pytest tests/ -q`) — catches repo-wide governance tests (RunRecorder coverage) and anything the scoped `-k` gates missed. All green.
- [ ] **Step 3: Lint + commit.** `git commit -m "test(ai): issue-executor parity across store modes + full-suite gate"`

---

## Task 8: DONE-GATE — checklist sign-off + live E2E + ship prep

**Files:** Modify `docs/superpowers/specs/2026-07-03-phase2-parity-checklist.md` (tick items with evidence); scratch E2E scripts.

- [ ] **Step 1: Real-DB/E2E sweep.** With `FEATURE_DIRECT_CONVERSATIONS=on` against the local DB: drive the REAL router+service through — create session (lands on conversations store, personal-team scope) → non-streaming chat turn (mock the LLM adapter at the boundary, NOT the store) → verify: user+assistant rows in `messages` with decoration in `body.meta`; counters bumped in `conversation_ai_meta`; `agent_runs` row carries `conversation_id`; session list order floats; rename/soft-delete; get_session_detail returns legacy-shaped JSON; streaming path persists one final assistant row.
- [ ] **Step 2: Shadow-mode drill.** Flag=`shadow`: run the same flow; legacy row authoritative; mirror rows appear in conversations store; introduce one forced mismatch (monkeypatch) and confirm `[p2-shadow] MISMATCH` logs without breaking the turn.
- [ ] **Step 3: Tick the 62 items** in the parity checklist with a one-line evidence pointer each (test name / smoke output). Items that are structurally N/A on the new store (e.g. §3.7 store-asymmetric enrichments) get an explicit `N/A:` note, not a silent skip. Commit the ticked checklist.
- [ ] **Step 4: Ship prep note** (in the PR body): rollout = merge flag-dark (`off`) → prod bake → flip `shadow` on prod for ≥1 week of real traffic diff-logging → review `[p2-shadow] MISMATCH` funnel → flip `on`. Live browser UX pass on the 1:1 AI chat panel happens at the `shadow→on` flip (the hard gate that caught #945/#948 in Phase 1). Rollback = flag back to `off`/`shadow` (new-store sessions become invisible until re-flipped — acceptable early).
- [ ] **Step 5: Commit + hand off to finishing-a-development-branch.**

---

## Self-Review

- **Spec coverage:** all 16 storage ops behind the seam (T2 legacy, T3/T4 new); 11 mismatches each have a landing (3.1→T4 widening, 3.2→T4 seq, 3.3/3.4→T4 mapping, 3.5/3.10/3.11→T1 meta sidecar, 3.6→T1 FK drop + T6 repoints, 3.8/3.9→T3 personal-team + members, 3.7→T8 N/A note); risks #1-5 → T3 (scope), T6+T8 (memory/recall), T4+T8 (streaming/ordering), T4 (decoration); 62-item checklist = T8 done-gate; second caller = T7; joined-gate carryover = T4.
- **Placeholder scan:** Protocol + flag + migration are full text; mapping rules are column-precise; test contracts name concrete assertions. Tasks tell the implementer which checklist rows are their source of truth rather than duplicating the checklist.
- **Type consistency:** `MessageStore` method names/kwargs identical across T2 (def) / T3-T4 (impl) / T5 (router); `store_kind` produced by stores (T3/T4) consumed by T6; `conversation_ai_meta` columns in T1 match T3's SQL and T5's ordering; flag values `off|shadow|on` consistent T5/T7/T8.
- **Risks accepted:** snowflake-id disjointness across `ai_sessions`/`conversations` (same generator, single mint — collision impossible; documented in T6); shadow mirror writes create parallel new-store rows that are discarded on rollback (acceptable, no user data loss — legacy stays authoritative).

## Execution Handoff

Execute via superpowers:subagent-driven-development (fresh implementer + reviewer per task; Opus whole-branch final review). Merge flag-dark. Rollout per T8 Step 4 (off → shadow bake → UX pass → on). Phase 3 (retire `ai_*` + `channels*`) only after `on` has baked and old-store traffic has decayed.
