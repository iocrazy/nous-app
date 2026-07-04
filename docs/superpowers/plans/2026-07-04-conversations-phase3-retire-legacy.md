# Conversations Phase 3 — Repoint Stragglers, Retire Legacy Stores, Single Clean Store

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** End state = ONE store (`conversations`/`messages` + sidecars), zero routing, zero dual-track: repoint the four still-live features that silently regressed onto legacy tables, delete the entire compatibility layer (router/legacy store/legacy chat backend/frontend legacy path/flags), and DROP the 7 legacy tables. **User has confirmed (2026-07-04): old team-chat AND old 1:1 history are disposable — the DROP is intentional and irreversible.**

**Architecture:** Three waves, strictly ordered. **Wave 0 (repoints)** fixes live prod gaps NOW and must be merged+deployed before any deletion: chat-upload session-scope, /dream consolidation, agent broadcast, mention badges. **Wave 1 (code deletion)** removes the legacy team-chat backend, the 1:1 compatibility layer (RoutedAiStore/LegacyAiStore + fallbacks), the frontend legacy path, and retires flags `FEATURE_CONVERSATIONS`/`VITE_FEATURE_CONVERSATIONS`/`FEATURE_DIRECT_CONVERSATIONS` (**`FEATURE_GROUP_AGENT_MEMORY` stays**). **Wave 2 (DB)** drops FKs then the 7 tables + dead ORM models. Every deletion task ends with grep-zero verification + full suite.

**Tech Stack:** as Phase 1/2. Real-DB smokes via `INTEGRATION_DATABASE_URL`; conversations-first patterns follow Task-6/PR-#960 precedents.

## Global Constraints
- **Branch:** `feature/conversations-p3` off latest `origin/master`; plan committed first. Wave 0 may ship as its own early PR (`fix/`-prefixed) if the epic runs long — the repoints are prod fixes and must not wait.
- **Ordering is load-bearing:** Wave 0 merged + DEPLOYED (ACR success) before Wave 2's migration merges. Never reorder.
- **DROP is irreversible** — mig 333 destroys old team-chat + 1:1 history (user-confirmed). The migration file must carry a `-- USER-CONFIRMED DATA DROP (2026-07-04)` header comment.
- **Deletion hygiene:** every deleted module ends with repo-wide grep proving zero remaining imports/references (`grep -rn "<name>" backend/app frontend --include="*.py" --include="*.ts*"` → only docs/comments allowed). Full suite (`uv run pytest tests/ -q`) 0 failed per task; frontend `npx tsc --noEmit` scoped-clean + `npm run build` green per frontend task.
- **Contract freezes:** `/sessions/*` and `/conversations/*` API shapes unchanged. `ConversationsAiStore` becomes the service's direct default store.
- **Migration numbering:** next free is **333** (verify `ls supabase/migrations | grep -E '^33' | sort`; renumber if taken).
- **asyncpg DoD** + lint (black/isort/flake8; loguru f-strings) per task. Repo-wide governance test if LLM calls touched.
- **Known Wave-0 facts (controller-verified, cite in briefs):**
  - `chat_upload.py:46` — `SELECT team_id FROM public.ai_sessions WHERE id=:id` (1:1 upload scope; broken for new-store sessions).
  - `consolidate_agent_memory.py:66-114` — three queries joining `ai_sessions`/`ai_messages` (/dream blind to new sessions).
  - `chat_broadcast_repository.py:44-55` — candidates from `agent_channels ⋈ channels`; writes go to legacy (invisible since P1 go-live). Driven by `workflows/task_broadcast_scanner.py`.
  - `frontend/hooks/useMentionBadges.ts:33` — realtime `table: 'channel_members'`; new store updates `conversation_members` (badges dead).
  - `frontend/components/chat/CreateGroupModal.tsx` — still imports `chatService` (works only because flag-on backend still mounts legacy `/chat`; must move to `conversationService`).
- **DB deps to unhook before DROP (mig 333):** `agent_runs.session_id` FK → ai_sessions (mig 231:233, CASCADE — drop CONSTRAINT, keep column+data as plain BIGINT); `issues.ai_session_id` FK (mig 224 / 231:242, SET NULL — same treatment); any other constraint the migration discovers via `pg_constraint` against the 7 tables; remove `channel_messages`/`channel_members` from `supabase_realtime` publication (added in mig 319/322-era) before dropping; ORM models mapping doomed tables: `models/ai.py` (sessions/messages/memory classes), `models/reviews.py:275` FK ref, anything in `models/agents.py` — delete/edit so `Reflect prod & diff ORM models` CI stays green.

## File Structure (by wave)
- W0: `backend/app/services/library/chat_upload.py`, `backend/app/workflows/consolidate_agent_memory.py`, `backend/app/repositories/chat_broadcast_repository.py` (+ its scanner workflow), `frontend/hooks/useMentionBadges.ts`, `frontend/components/chat/CreateGroupModal.tsx`.
- W1 backend deletions: `api/chat_router.py`, `repositories/chat_repository.py`, `services/chat_service.py`, `services/chat/channel_agent_turn.py`, `services/ai/chat/legacy_ai_store.py`, `services/ai/chat/store_router.py`; edits: `services/ai/chat/ai_library_chat_service.py` (default store → ConversationsAiStore), `ai_library_chat_wiring.py` + `workflows/write_memory.py` (drop ai_sessions fallbacks), `api/__init__.py` (drop legacy mount + unconditional conversations mount), `core/config.py` (retire 2 backend flags).
- W1 frontend deletions: `services/chatService.ts`(+test), `hooks/useChannelRealtime.ts`; edits: `pages/ChatPage.tsx` (drop seam), `utils/featureFlags.ts` (drop `conversations()`), Vercel env var removal = ops note.
- W2: `supabase/migrations/333_retire_legacy_chat_tables.sql`; ORM model edits; final sweep doc.

---

## Task 1 (W0): chat_upload session-scope → conversations-first

**Files:** Modify `backend/app/services/library/chat_upload.py`; Test `backend/tests/test_chat_upload_scope.py` (extend or create).
- [ ] Failing test: fake `db_engine.fetch_one` — conversation id → `SELECT scope_id ... FROM public.conversations` used (aliased `AS team_id`), value returned; miss → legacy `ai_sessions` query (byte-unchanged); found-row-with-NULL is authoritative. Mirror PR #960's dual-lookup shape exactly.
- [ ] Implement (probe conversations first; comment: snowflake single-mint ⇒ id in exactly one table).
- [ ] Suite + lint + commit `fix(ai): chat upload scope resolves conversations sessions`.

## Task 2 (W0): /dream consolidation reads both stores

**Files:** Modify `backend/app/workflows/consolidate_agent_memory.py`; Test `backend/tests/test_consolidate_agent_memory_p3.py`.
- [ ] Read the three queries (`:66-114`). For each, add the conversations-equivalent (sessions ⇒ `conversations c JOIN conversation_ai_meta m ON m.conversation_id=c.id` where `c.type='direct_agent'`; messages ⇒ `messages` with `sender_type` role-mapping + `body->>'text'` content, `deleted_at IS NULL`) and UNION/merge results in Python (keep per-query LIMIT semantics; document that legacy arm dies in Wave 2 cleanup — leave a `# P3-W2: drop legacy arm` marker).
- [ ] Failing tests: fake engine asserts BOTH arms queried and merged; role/content mapping correct. Implement; suite; lint; commit `fix(ai): /dream consolidation sees conversations sessions`.

## Task 3 (W0): agent broadcast → conversations

**Files:** Modify `backend/app/repositories/chat_broadcast_repository.py` (+ scanner workflow if it names channel columns); Test extend its existing tests (#902/#911 era files — grep `broadcast` in tests/).
- [ ] Candidates query → `SELECT c.id, c.scope_id AS team_id, ... FROM public.conversation_members cm JOIN public.conversations c ON c.id=cm.conversation_id WHERE cm.member_type='agent' AND c.archived_at IS NULL AND c.type<>'direct_agent'` (group/public chats only; keep the existing dedup/watermark logic intact — READ the file fully first; #911 replaced array-binds with team_members JOIN, preserve that shape).
- [ ] Message write → `ConversationRepository.send_message(conversation_id=..., sender_id=None, sender_type='agent', type='text', body={'text': ...}, from_agent_id=...)` (atomic seq; replaces raw channel_messages insert). Watermark table/logic unchanged unless it FKs channels — check.
- [ ] Failing tests first (existing broadcast tests likely mock the repo — adapt SQL-shape asserts); real-DB smoke: seed a group conversation + agent member → scanner candidate query returns it → send lands in `messages`. Suite; lint; commit `fix(chat): agent broadcast targets conversations store`.

## Task 4 (W0): mention badges + CreateGroupModal frontend repoints

**Files:** Modify `frontend/hooks/useMentionBadges.ts`, `frontend/components/chat/CreateGroupModal.tsx`; tests: extend existing vitest files if present.
- [ ] `useMentionBadges`: realtime table `'channel_members'` → `'conversation_members'`; verify filter/payload field names against mig 327 (`conversation_id`, `user_id`, `mention_count`, `last_read_seq`) and adjust mapping; keep hook API identical.
- [ ] `CreateGroupModal`: swap `chatService` imports/calls to `conversationService` equivalents (same method names — P1 kept them interface-compatible; `createChannel` posts scope_id already).
- [ ] `npx tsc --noEmit` scoped-clean + `npm run build` green + vitest for touched specs; commit `fix(chat): mention badges + create-group on canonical store`.

**→ WAVE-0 GATE: merge these four (single PR ok), wait `Deploy Backend to ACR` success + Vercel deploy, prod smoke: broadcast candidate query returns conversations rows (SSH psql), badges table subscribed (code-level), THEN proceed.**

## Task 5 (W1): delete legacy team-chat backend + retire FEATURE_CONVERSATIONS

**Files:** Delete `api/chat_router.py`, `repositories/chat_repository.py`, `services/chat_service.py`, `services/chat/channel_agent_turn.py` + their test files; Modify `api/__init__.py` (remove legacy import/mount; mount conversation_router UNCONDITIONALLY), `core/config.py` (remove `FEATURE_CONVERSATIONS`), any straggler importers the grep finds (e.g. `services/chat/agent_broadcast.py` — repoint its imports to conversation equivalents; mention_parser/typing endpoints if they lived in chat_router — RELOCATE still-used endpoints (typing/presence, mention parse) into conversation_router BEFORE deleting, preserving paths the frontend calls — grep frontend for `/chat/` API calls first; anything frontend still calls must move, not die).
- [ ] Inventory first (grep `/chat/` in frontend services + `chat_router|chat_repository|chat_service|channel_agent_turn` in backend); relocate live endpoints; delete; grep-zero; full suite; lint; commit `refactor(chat): retire legacy team-chat backend + FEATURE_CONVERSATIONS`.

## Task 6 (W1): collapse the 1:1 compatibility layer + retire FEATURE_DIRECT_CONVERSATIONS

**Files:** Delete `services/ai/chat/legacy_ai_store.py`, `services/ai/chat/store_router.py` + `tests/test_legacy_ai_store.py`, `tests/test_store_router.py`; Modify `ai_library_chat_service.py` (default store = `ConversationsAiStore()`), `core/config.py` (remove flag), `ai_library_chat_wiring.py` + `workflows/write_memory.py` + Task-1/2's dual lookups (drop ai_sessions fallback arms + the `# P3-W2` markers), `message_store.py` docstring (single-store note), issue/other tests that referenced modes.
- [ ] Grep-zero for `LegacyAiStore|RoutedAiStore|store_router|FEATURE_DIRECT_CONVERSATIONS|store_kind == "legacy"`; adjust tests (mode-parametrized issue tests collapse to single-store); full suite; lint; commit `refactor(ai): single conversations store — compatibility layer retired`.

## Task 7 (W1): frontend legacy path deletion + retire VITE_FEATURE_CONVERSATIONS

**Files:** Delete `services/chatService.ts`, `services/chatService.test.ts`, `hooks/useChannelRealtime.ts`; Modify `pages/ChatPage.tsx` (svc = conversationService directly; single realtime hook; drop `featureConversations` const + `conversations()` call sites incl. canSave gates + handleAttachFiles guard), `utils/featureFlags.ts`(+test) remove `conversations()`.
- [ ] Grep-zero `chatService|useChannelRealtime|conversations()|VITE_FEATURE_CONVERSATIONS` (frontend, excluding conversationService); tsc scoped-clean; build; vitest; commit `refactor(chat): single conversations path on frontend`. Ops note in PR: delete `VITE_FEATURE_CONVERSATIONS` from Vercel (Preview+Production) after merge.

## Task 8 (W2): migration 333 — DROP the 7 tables + ORM cleanup

**Files:** Create `supabase/migrations/333_retire_legacy_chat_tables.sql`; Modify `models/ai.py`, `models/reviews.py`, `models/agents.py` (delete/detach doomed-table models + FK refs).
- [ ] Migration (verbatim skeleton; implementer completes the discovered-constraint list):
```sql
-- 333_retire_legacy_chat_tables.sql — Phase 3: retire legacy chat + 1:1 stores.
-- USER-CONFIRMED DATA DROP (2026-07-04): old team-chat and 1:1 history are
-- intentionally destroyed. Everything lives on conversations/messages now.
ALTER TABLE public.agent_runs DROP CONSTRAINT IF EXISTS agent_runs_session_id_fkey;
ALTER TABLE public.issues     DROP CONSTRAINT IF EXISTS issues_ai_session_id_fkey;
-- (implementer: enumerate any remaining FKs via pg_constraint against the 7
--  tables on the LOCAL db and drop them here explicitly)
ALTER PUBLICATION supabase_realtime DROP TABLE IF EXISTS public.channel_messages;
ALTER PUBLICATION supabase_realtime DROP TABLE IF EXISTS public.channel_members;
DROP TABLE IF EXISTS public.agent_channels;
DROP TABLE IF EXISTS public.channel_messages;
DROP TABLE IF EXISTS public.channel_members;
DROP TABLE IF EXISTS public.channels;
DROP TABLE IF EXISTS public.ai_session_memory;
DROP TABLE IF EXISTS public.ai_messages;
DROP TABLE IF EXISTS public.ai_sessions;
NOTIFY pgrst, 'reload schema';
```
  NOTE: `ALTER PUBLICATION ... DROP TABLE IF EXISTS` is INVALID SQL (memory bug #733) — implementer must use the guarded `DO $$` + `pg_publication_tables` check pattern from mig 329 instead.
- [ ] Apply locally (order: after W1 code is already merged in-branch); verify 7 tables gone + publication clean + `Reflect prod & diff ORM models`-style check passes locally (`uv run pytest tests/ -q` incl. ORM reflection tests).
- [ ] Commit `feat(db): drop legacy chat + ai session tables (mig 333)`.

## Task 9: DONE-GATE — full sweep + prod verification plan
- [ ] Repo-wide final grep for all 7 table names + all deleted module names (backend+frontend, excluding migrations history and docs) — zero live references.
- [ ] Full backend suite + frontend build/vitest — green.
- [ ] Post-merge ops checklist appended to this plan: wait `Run SQL Migration` success (mig 333 on prod — THE irreversible moment) → prod `/health` + team-chat + 1:1 browser sanity (send message each surface; badges update; broadcast scanner log next scheduled run) → remove `VITE_FEATURE_CONVERSATIONS` from Vercel + remove `FEATURE_CONVERSATIONS`/`FEATURE_DIRECT_CONVERSATIONS` lines from NAS `.env` (dead env vars; harmless but clean) → memory update.

## Self-Review
- Coverage: 4 live regressions repointed (W0) before any deletion; all inventoried consumers addressed (broadcast/badges/upload//dream/S7-S8-fallbacks/ORM models/reviews FK/publication members); flags retired except FEATURE_GROUP_AGENT_MEMORY; endpoints frontend still calls get RELOCATED not deleted (T5 inventory step).
- Ordering: W0 deploy-gated before W2 drop; FK/publication unhooked inside mig 333 before DROPs; ALTER PUBLICATION trap (#733) called out.
- Placeholders: none — every task names files, queries, verification; T8 skeleton is real SQL with one explicitly-scoped discovery step (constraint enumeration) that cannot be pre-written without the local db.
- Risk accepted: anything else still silently reading the 7 tables dies loudly at DROP (42P01) — the T9 grep + full suite is the net; prod error funnel watched post-merge.

## Execution Handoff
superpowers:subagent-driven-development; W0 as early PR once T1-4 pass review; final whole-branch review (Opus) before the W1+W2 PR. Merge order: W0 PR → deploy-verify → main PR (W1+W2+mig333). After prod migration succeeds: ops checklist (T9).
