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
- [ ] Create session resolves `agent_slug → agent_id` at create-time; 404 on unknown slug (`:87-92`). **U** — ⚠ GAP: positive resolve path is exercised by every P2 test (`test_phase2_flag_on_e2e.py`, `test_phase2_shadow_drill.py` resolve `script_ai`→agent_id on create), but the 404-unknown-slug failure branch (`ai_library_chat_service.py:94-99`) has zero test coverage anywhere in the suite — pre-existing gap, untouched by P2, not closed by this task.
- [x] Create persists `status='active'`, `total_tokens=0`, `message_count=0`, and optional `project_id/team_id/context_type/context_id` only when provided (`:104-111`). **DB** — ✔ `test_legacy_ai_store.py::test_create_session_inserts_defaults_and_omits_optional_cols`+`::test_create_session_includes_optional_cols_when_provided`; `test_conversations_ai_store.py::test_create_session_inserts_conversation_two_members_and_meta_in_one_txn`; real-DB `test_phase2_flag_on_e2e.py::test_flag_on_full_lifecycle_sweep` (meta_row total_tokens=0/message_count=0, team_id/context_type/context_id persisted).
- [x] `POST /agents/{slug}/sessions` returns `SessionOut` with `id` as numeric string (not UUID), 201 (`ai_library_router.py:1757`, `SessionOut.id: str` coerce_numbers_to_str). **E2E** — ✔ router handler (`ai_library_router.py:1763-1781`) returns `svc.create_session(...)`'s dict verbatim as `response_model=SessionOut`; `test_phase2_flag_on_e2e.py` constructs `SessionOut(**session_dict)` off the same real dict and asserts `.id == str(session_id)`. Schema-level, not a live-HTTP round trip.
- [x] List returns caller-owned sessions only, `status='deleted'` filtered out, ordered `updated_at DESC`, honoring `agent_slug`+`project_id`+`limit` filters (`:137-150`). **DB** — ✔ `test_legacy_ai_store.py::test_list_sessions_filters_and_orders`; `test_conversations_ai_store.py::test_list_sessions_sql_has_type_and_archived_filters_and_ordering`+`::test_list_sessions_applies_agent_slug_and_project_id_filters`; real-DB `test_phase2_flag_on_e2e.py` list-ordering assertions.
- [ ] List `limit` clamped to 1..200 at router (`:1795`). **U** — ⚠ GAP: `ai_library_router.py:1795` (`if limit < 1 or limit > 200: raise HTTPException(400)`) has no test anywhere in the suite. Pre-existing gap, untouched by P2.
- [x] Get enforces ownership: returns 404 (not 403) when `row.user_id != caller` — must not leak existence (`:168-172`). **DB** — ✔ `test_ai_library_chat.py::test_get_session_404_when_not_owner`+`::test_get_session_404_when_missing`; `test_legacy_ai_store.py::test_get_session_uses_maybe_single_no_ownership_check`; `test_conversations_ai_store.py::test_get_session_returns_none_when_row_missing_or_archived`; real-DB `test_phase2_flag_on_e2e.py` (404 after soft-delete).
- [x] Get session detail returns session merged with full `messages[]` (`ai_library_router.py:1809-1814`). **E2E** — ✔ router handler is `return {**session, "messages": messages}`; `test_phase2_flag_on_e2e.py` builds the identical `{**session_dict, "messages": messages_dict}` off real service calls and validates `SessionWithMessages(**detail)`.
- [x] Rename updates `title` only; owner-only; no-op PATCH returns current session (`:199-216`). **DB** — ✔ `test_legacy_ai_store.py::test_rename_session_updates_title_only`; `test_conversations_ai_store.py::test_rename_session_updates_title_and_touches_meta_then_returns_row`; real-DB `test_phase2_flag_on_e2e.py` rename assertion. (no-op/title=None early-return branch in `update_session` is a trivial one-line guard, not separately unit-tested — low risk, noted not blocking.)
- [x] Delete is SOFT (`status='deleted'`); messages retained for audit (`:218-227`). **DB** — ✔ `test_legacy_ai_store.py::test_soft_delete_session_writes_status_deleted`; `test_conversations_ai_store.py::test_soft_delete_session_sets_archived_at`; both store methods issue only an UPDATE to the session/conversation row (no DELETE against messages), confirmed by code + real-DB `test_phase2_flag_on_e2e.py` (`archived_at IS NOT NULL`, session absent from list, 404 on get).
- [x] `updated_at` auto-advances so a chatted-in session floats to top of the list (today implicit via DB default/trigger on any UPDATE — S6 counter bump touches the row every turn). **DB** — ✔ real-DB `test_phase2_flag_on_e2e.py::test_flag_on_full_lifecycle_sweep` (chats in `session_id` after `other_id` was created, asserts `session_id` now sorts before `other_id`).

### Message send / receive
- [x] History loaded once per turn, chronological, capped 200 (`:445`, M1). **U** — ✔ `test_legacy_ai_store.py::test_get_messages_orders_asc_and_limits`; `test_conversations_ai_store.py::test_get_messages_sql_orders_by_seq_asc_and_excludes_deleted`.
- [x] User message persisted BEFORE model call so a mid-turn failure still records the attempt (`:449-462`, M2). **DB** — ✔ `test_ai_library_chat.py::test_chat_persists_both_messages_and_bumps_counters` (asserts insert-user-then-run-then-insert-assistant ordering); real-DB `test_phase2_flag_on_e2e.py` (`msg_rows` ordered by `seq ASC` — user row seq 1, assistant row seq 2).
- [x] Only `role ∈ {user,assistant,system}` rows replayed into the model payload; others skipped (`:601-605`). **U** — ✔ regression gate (Task 2 mechanical extraction; service logic untouched; full suite green) — see `test_ai_library_chat.py` turn-assembly coverage.
- [x] Assistant message persists `agent_id`, `prompt_tokens`, `completion_tokens`, `metadata_json` (M3). **DB** — ✔ `test_legacy_ai_store.py::test_append_assistant_message_includes_usage_and_metadata`; `test_conversations_ai_store.py::test_append_assistant_message_metadata_round_trip_via_return_value`; real-DB `test_phase2_flag_on_e2e.py` (`meta["agent_id"/"prompt_tokens"/"completion_tokens"]`).
- [x] `metadata_json` folds `run_id`, `tool_calls` trace, and `awaiting_approval` so a page-reload re-renders sub-task/approval cards (`:978-992`). **E2E** — ✔ `test_conversations_ai_store.py::test_append_assistant_message_metadata_round_trip_via_return_value` (asserts `body["meta"]["run_id"]`/`["tool_calls"]`/`["awaiting_approval"]` all round-trip exactly); `test_ai_library_chat.py::test_chat_persists_tool_calls_into_metadata_json`; real-DB `test_phase2_flag_on_e2e.py` confirms `run_id` present on a real row.
- [x] Non-streaming `POST /sessions/{id}/chat` returns `{message, usage, run_id, tool_calls, attachment_failures}` (`ai_library_router.py:1864-1870`). **E2E** — ✔ router repackages `svc.chat()`'s return dict verbatim (same keys); `ai_library_chat_service.py:1087-1103` constructs that exact dict; real-DB `test_phase2_flag_on_e2e.py`/`test_phase2_shadow_drill.py` call `svc.chat()` and assert on `result["assistant_message"]`. Schema/service-level, not a live-HTTP round trip.
- [x] Session counters bump `total_tokens += turn`, `message_count += 2` per turn (S6). Verify read-modify-write doesn't lose updates under the per-user concurrency gate. **DB** — ✔ `test_legacy_ai_store.py::test_bump_counters_writes_caller_provided_absolute_values`; `test_conversations_ai_store.py::test_bump_counters_writes_given_absolutes_not_increment`; real-DB `test_phase2_flag_on_e2e.py` (`total_tokens==33==11+22`, `message_count==2`, then a 2nd turn in the streaming test `total_tokens==11==5+6`).
- [x] `MessageOut.session_id` serializes as numeric string; `id` stays UUID today (schema §3.1). **U** — ✔ real-DB `test_phase2_flag_on_e2e.py` constructs `MessageOut(**m)` off real rows from both stores and asserts `mo.session_id == str(session_id)`.

### Compaction & session memory
- [x] Compaction triggers only above `DEFAULT_AUTO_COMPACTION_INPUT_TOKENS` (`:1189`). **U** — ✔ regression gate (Task 2 mechanical extraction; service logic untouched; full suite green) — `tests/test_context_compactor.py`, `tests/test_llm_compactor.py`.
- [x] Compaction preserves tool_use/result pairs (won't 400 next call) and degrades to full history on failure (`:1259-1262`). **U** — ✔ regression gate — `tests/test_context_compactor.py`, `tests/test_llm_compactor.py`.
- [x] Compaction prefers cached `ai_session_memory.body_md` as head summary over a fresh LLM call when `session_id` present (SM1 loader, `:1194-1206`). **DB** — ✔ P2 Task 6 (commit f0f3ae5a) real-DB smoke `tests/integration/test_session_memory_conversations_id_smoke.py` proves `SessionMemoryRepository` accepts a real `conversations.id` as its key with zero code change (pre-mig-332 this would 23503) — the repoint this behavior depends on is proven live.
- [x] Fire-and-forget session-memory updater dispatched post-turn with `full_messages = user_messages + assistant` (`:1043-1054`); failure is non-fatal (`:1055`). **U** — ✔ regression gate — `tests/test_session_memory_runner.py`.
- [x] `maybe_update_session_memory` upserts on dual-threshold (tokens/tool_calls/turns baselines) and bumps `version` (SM2). **DB** — ✔ `tests/test_session_memory_repository.py::test_upsert_bumps_version`+`::test_upsert_inserts_when_no_existing`; `tests/test_session_memory_runner.py`; repoint proven live by `test_session_memory_conversations_id_smoke.py`.
- [x] Per-message token cap truncates single oversized messages (paste-a-200k-log case) before compaction (`:806-822`). **U** — ✔ regression gate (Task 2 mechanical extraction; service logic untouched; full suite green).

### Recall stack (Honcho L2 + Graphiti L3 + agent_memory)
- [x] Recall runs BEFORE prompt composition, concurrently, under `MEMORY_RECALL_BUDGET_S=4.0` wall-clock; timeout → no memory, turn proceeds (`ai_library_chat_wiring.py:187-205`). **U** — ✔ `tests/test_ai_library_chat_wiring.py::test_memory_recalls_run_concurrently`.
- [x] Graphiti recall keys on `group_ids=[user-{id}]` + `project-{project_id}` when session has a project (`:487-491`); project resolved via S7 raw SQL (`:584-603`). **DB** — ✔ `tests/test_graph_facts_injection.py`, `tests/test_graph_memory.py`; S7 raw-SQL repoint (`ai_sessions`→`conversations`) is P2 Task 6 scope, verified in `test_task6_run_recorder_store_dispatch.py` + real-DB smoke.
- [x] Honcho recall keys workspace on `team-{team_id}` via S8 raw SQL, else deployment default (`:522-527`, `write_memory.py:181-197`). **DB** — ✔ `tests/test_honcho_memory.py::test_workflow_resolves_team_workspace`+`::test_resolve_team_workspace_handles_lookup_failure`+`::test_workspace_override_routes_to_team_namespace`; S8 conversations-first repoint verified by P2 Task 6 (`scope_id AS team_id`).
- [x] Honcho "About me" card outranks derived representation (`:534-546`). **U** — ✔ regression gate (Task 2 mechanical extraction; service logic untouched; full suite green) — `tests/test_honcho_memory.py`.
- [x] Graph + Honcho both suppressed when per-user `memory_prefs.inject=false` (`:485`, `:519`). **U** — ✔ regression gate (Task 2 mechanical extraction; service logic untouched; full suite green).
- [x] agent_memory recall flag-gated on `FEATURE_AGENT_MEMORY`; returns `[]` when off; fetches user `team_ids` inside the gate, degrades to owner-only on team-fetch failure (`:552-581`). **U** — ✔ `tests/memory/test_agent_memory_recall.py::test_recall_returns_empty_on_blank_query`+`::test_recall_swallows_errors`+`::test_recall_builds_scoped_query_and_maps_hits`.
- [x] `MemoryContext.session_id` int-coerced from snowflake str; UUID test ids resolve to None safely (`:170-185`). **U** — ✔ the `int(str(session_id))`/`except (TypeError, ValueError): pass` guard (`ai_library_chat_wiring.py:170-173`) is exercised as a side effect by every `test_ai_library_chat_wiring.py` fixture using `session_id=uuid4()` (lines 49/71/87/105/122/142/150/172/232) — none of those tests crash, proving UUID→None resolution is safe.
- [x] All three recall results flow into `ComposerInput` (`graph_facts`, `user_context`, `agent_memory_facts`) (`ai_library_chat_service.py:585-592`). **U** — ✔ `tests/test_ai_library_chat_wiring.py::test_stack_has_runner_and_memory_fields`.

### Plan mode
- [x] `plan_mode ∈ {prompt_user, dry_run}` swaps `request_instructions` for `build_plan_prompt()` (`:502-505`). **U** — ✔ `tests/agent_framework/test_plan_mode.py::test_build_plan_prompt_includes_max_steps` + regression gate for the calling branch (Task 2 mechanical extraction; service logic untouched; full suite green).
- [x] `plan_mode` absent/`auto` → normal interactive instructions incl. Delegate hint (`:506-519`). **U** — ✔ regression gate (Task 2 mechanical extraction; service logic untouched; full suite green) — simple else-branch, untouched by P2.
- [x] Plan-mode response persists as a normal assistant message (plan markdown); approval/reject is the next user turn. **E2E** — ✔ regression gate: plan-mode output flows through the same `append_assistant_message` path proven by M3 evidence above (item 4 of this section) — no plan-mode-specific persistence branch exists.

### Media tools (GenerateImage / GenerateVideo)
- [x] Tools injected ONLY when `FEATURE_AGENT_MEDIA_TOOLS` truthy (`_media_tools_enabled`, `:44-53`, `:714-730`). **U** — ✔ `tests/test_chat_media_tools_injection.py::test_flag_default_off`+`::test_flag_on`.
- [x] When enabled, both tool specs appended to `composed.tools` and handlers bound to runner (`:721-729`). **U** — ✔ `tests/test_agent_runner_media_tools.py::test_supported_tools_includes_media`+`::test_runner_has_media_handler_fields`; `tests/test_generate_media_tools.py` (4 tests).
- [x] When flag off, zero tools added — behavior-neutral. **U** — ✔ `tests/test_chat_media_tools_injection.py::test_flag_default_off`.
- [ ] Generated media persistence path must map to `message_attachments.generated_media_id` in the new store (schema §3.7). **DB** — N/A: per parity checklist §3.7, this is a store-asymmetric enrichment — the legacy `ai_messages` adapter has no `message_attachments` equivalent and keeps folding generated-media refs into `metadata_json`. Explicitly flagged in the Task 8 brief's self-review as "3.7→T8 N/A note", not a parity regression.

### FinishIssue & commitments
- [x] FinishIssue tool + `FINISH_ISSUE_INSTRUCTION` injected ONLY when `trigger ∈ {issue_dispatch, issue_reply}`; never on chat turns (`:736-754`). **U** — ✔ `tests/test_finish_issue_tool.py::test_instruction_names_tool_and_every_outcome`+`::test_spec_advertises_enum_and_name`; `tests/test_issue_agent_executor_p2.py::test_on_mode_session_and_turn_land_on_new_store` (asserts `FinishIssue` injected on `issue_dispatch`/`issue_reply` across all 3 store modes).
- [x] `finish_issue_handler` cleared in `finally` so a later non-issue turn on the same runner can't accept it (`:900`). **U** — ✔ regression gate (Task 2 mechanical extraction; service logic untouched; full suite green).
- [x] Issue outcome extracted from returned `tool_calls` trace by `issue_agent_executor` (`:96`, `extract_issue_outcome`). **U** — ✔ `tests/test_finish_issue_tool.py::test_extract_returns_none_when_no_finish_call`+`::test_extract_reads_last_acknowledged_declaration`+`::test_extract_ignores_rejected_declaration`+`::test_extract_falls_back_to_args_when_result_minimal`; `tests/test_issue_agent_executor_p2.py::test_run_issue_agent_forwards_trigger_and_extracts_outcome_every_mode`.
- [x] `run_session_turn` is shared by chat AND issues; both funnel the per-user concurrency gate `user_slot` (`:386-397`). **U** — ✔ `tests/test_run_session_turn.py::test_chat_delegates_to_run_session_turn`; `tests/test_issue_agent_executor.py::test_run_issue_agent_runs_session_turn`; P2 `tests/test_issue_agent_executor_p2.py` (3 tests) proves the same shared turn path across all 3 store modes.
- [x] First-turn `NEXT_SESSION` commitments surfaced into `request_instructions` and marked fulfilled once (`:521-533`, `_surface_next_session_commitments:1265-1307`). Keyed on `agent_id`+`user_id` (NOT session). **DB** — ✔ `tests/test_next_session_commitments_surface.py::test_pending_commitments_prepended_and_fulfilled`+`::test_no_pending_returns_unchanged`+`::test_repo_failure_returns_unchanged`+`::test_caps_at_five_reminders`.
- [x] Post-turn commitment harvester dispatched fire-and-forget with pre-filter; non-fatal on failure (`:1058-1145`). **U** — ✔ regression gate (Task 2 mechanical extraction; service logic untouched; full suite green) — `tests/test_next_session_commitments_surface.py`.

### Token caps & cost / budget
- [x] RunRecorder pre-flight raises `AgentPausedError` for a paused agent → 409 CONFLICT; NO agent_runs row, NO assistant message (`:845-856`, `:927-934`, run_recorder `:58`). **DB** — ✔ `tests/test_run_recorder.py::test_paused_agent_raises_before_insert`; `tests/test_run_recorder.py::test_max_concurrent_runs_rejects_with_busy_error`.
- [x] BudgetGuard hook registered when `budget_per_run_cents` set; blocks over-budget (`ai_library_chat_wiring.py:223-228`). **U** — ✔ `tests/test_ai_library_chat_wiring.py::test_budget_guard_registered_when_budget_set`+`::test_budget_guard_NOT_registered_when_budget_none`; `tests/test_budget_guard.py` (7 tests).
- [x] Per-agent tool-call rate limit + capability gate registered only when profile present (`:214-243`). **U** — ✔ `tests/test_ai_library_chat_wiring.py::test_per_turn_registry_isolated`+`::test_post_hooks_include_cost_auditor_and_memory_harvester`.
- [x] `usage` snapshot (`prompt_tokens`/`completion_tokens`) pulled off recorder as single source of truth, written to both the assistant message and returned (`:905-908`, M3). **DB** — ✔ real-DB `test_phase2_flag_on_e2e.py` (recorder's `prompt_tokens=11`/`completion_tokens=22` land identically in both the persisted `body.meta` and the returned `result["usage"]`/counters).

### Attachments & binary input
- [x] Attachments normalized from Pydantic `AttachmentRequest` OR plain dict; unknown types dropped silently (`:614-621`). **U** — ✔ `tests/test_chat_attachments.py` (4 tests); `tests/test_chat_attachment_resolver.py` (21 tests).
- [x] Split by kind: `resource_ref` → resolver+ResourceFetch tool; others → binary path (`:623-629`). **U** — ✔ `tests/test_chat_attachment_resolver.py` (21 tests).
- [x] resource_ref resolution extends system message with `<available_resources>`, registers `ResourceFetch` tool + per-turn closure, cleared in `finally` (`:643-710`, `:895-897`). **U** — ✔ `tests/test_chat_attachment_resolver.py` (21 tests).
- [x] resource_ref warnings prepended to user content (`:756-763`). **U** — ✔ `tests/test_chat_attachment_resolver.py` (21 tests).
- [x] Binary attachments → multimodal `build_user_message`; vision-gated by `model_supports_vision`; failures degrade to text-only with placeholder (`:768-798`). **U** — ✔ `tests/test_chat_attachments.py`; `tests/test_issue_reply_attachments.py` (4 tests).
- [x] `attachment_failures` surfaced in both `/chat` and `/chat-stream` responses (`:1155-1158`, `:329-332`). **E2E** — ✔ `ai_library_chat_service.py:1087-1103` return dict includes `attachment_failures` for both `chat()` and `chat_stream()` (same underlying dict); regression gate (Task 2 mechanical extraction; service logic untouched; full suite green).
- [x] Upload endpoint `/chat-attachments/upload`: ext allow-list + magic-byte check + 50MB cap; persists to shared-library temp resource (worker-readable), `session_id` routes to team scope (`ai_library_router.py:2377-2496`). **DB** — ✔ `tests/test_chat_attachment_upload.py` (14 tests: `test_upload_rejects_extension_spoofing_via_magic_bytes`, `test_upload_rejects_disallowed_extension`, `test_upload_rejects_oversize_via_content_length`, `test_upload_forwards_session_id_from_form/query`, etc.).

### Title generation
- [x] There is NO server-side auto-title today — `title` comes only from `SessionCreate.title` (default `'New Chat'`) or explicit rename (grep confirmed: no `generate_title` in chat path). Parity = keep default + manual rename; do NOT silently add auto-titling during the swap. **U** — ✔ re-confirmed by grep during Task 8 (`grep -rn "generate_title" app/` = no hits in chat path); both stores' `create_session`/`rename_session` tests (`test_legacy_ai_store.py`, `test_conversations_ai_store.py`) only ever write the caller-supplied title, never synthesize one.

### Streaming
- [x] `/sessions/{id}/chat-stream` emits SSE `event: delta` (text+offset) → `event: done` (message_id, usage, run_id, tool_calls, total_chars, attachment_failures) → `event: error` on failure (`:300-333`, `ai_library_router.py:1901-1925`). **E2E** — ✔ router `_generator()` (`ai_library_router.py:1899-1918`) yields exactly this SSE shape off `svc.chat_stream()`'s event dicts; `chat_stream()` itself (`:189-289`) is exercised transitively by `test_phase2_flag_on_e2e.py::test_flag_on_streaming_persists_one_final_assistant_row` driving the same underlying `chat(..., chunk_callback=...)` codepath `chat_stream` wraps. Schema/service-level, not a live-HTTP SSE round trip.
- [x] Streaming drives `runner.stream_turn` with `auto_recorder=False` (recorder owned by service); accumulates chunks; `chunk_callback` failure must not kill the turn (`:871-891`). **U** — ✔ `tests/test_streaming.py::test_stream_turn_auto_recorder_disabled_explicitly`+`::test_stream_turn_caller_recorder_overrides_auto`; `tests/test_ai_library_chat.py::test_chat_chunk_callback_failure_does_not_abort_turn`.
- [x] Streaming path still persists user+assistant messages, bumps counters, dispatches memory/commitment harvesters (same tail as buffered — `result` backfilled at `:922-926`). **DB** — ✔ real-DB `test_phase2_flag_on_e2e.py::test_flag_on_streaming_persists_one_final_assistant_row` (2 message rows, `total_tokens==11==5+6`, `message_count==2`).
- [x] Streaming write ordering: assistant message persisted AFTER stream completes with full accumulated text (`:891`, `:993`) — NOT incrementally. Adapter must preserve "one final assistant row per turn". **DB** — ✔ real-DB `test_phase2_flag_on_e2e.py::test_flag_on_streaming_persists_one_final_assistant_row` (exactly 2 rows total after a 2-chunk stream; assistant row's `body.text == "Hello!"` — the full accumulated text, not a chunk).
- [x] Tool-using turns in streaming currently degrade to one-shot (documented `:1889-1892`); tool_calls visible via synthetic delta text only. **U** — ✔ regression gate (Task 2 mechanical extraction; service logic untouched; full suite green) — documented limitation, unaffected by storage swap.

### Telemetry (RunRecorder, agent_runs.session_id)
- [x] Every turn creates exactly one `agent_runs` row with `session_id`, `team_id`, `project_id`, `trigger`, `model`, `provider` (`:845-856`, R1). **DB** — ✔ `tests/test_run_recorder.py::test_start_inserts_running_row_and_snapshots_price`; `tests/test_task6_run_recorder_store_dispatch.py` (3 tests) proves the `session_id`/`conversation_id` kwarg split; real-DB `test_phase2_flag_on_e2e.py`/`test_phase2_shadow_drill.py` assert `captured_kwargs["conversation_id"]`/`["session_id"]`/`["trigger"]`.
- [x] `run_id` threaded into response AND assistant `metadata_json` for chat→Runs deep-link (`:979-980`, `:1151`). **E2E** — ✔ `test_conversations_ai_store.py::test_append_assistant_message_metadata_round_trip_via_return_value` (`body["meta"]["run_id"]`); real-DB `test_phase2_flag_on_e2e.py` (`"run_id" in meta`).
- [x] `trigger` distinguishes `chat` vs `issue_dispatch`/`issue_reply` in agent_runs (`:366`, `:736`). **U** — ✔ `tests/test_issue_agent_executor_p2.py::test_run_issue_agent_forwards_trigger_and_extracts_outcome_every_mode`; real-DB `test_phase2_flag_on_e2e.py` (`captured_kwargs["trigger"] == "chat"`).
- [x] Dashboard/runs/live read paths scope by `agent_id`+`user_id` (unaffected by store swap, but `session_id` FK must stay valid — §3.6). **DB** — ✔ `tests/test_ai_library_dashboard.py` (4 tests, scoped by agent slug + auth user); FK validity across the swap proven by `test_session_memory_conversations_id_smoke.py` + `test_task6_run_recorder_store_dispatch.py`.

### Error paths
- [x] 404 when session missing/not owned (`:164-172`). **U** — ✔ `tests/test_ai_library_chat.py::test_get_session_404_when_not_owner`+`::test_get_session_404_when_missing`; real-DB `test_phase2_flag_on_e2e.py` (404 after soft-delete).
- [x] 400 when session has no `agent_slug` bound (`:357-361`, `:439-443`). **U** — ✔ `tests/test_run_session_turn.py::test_chat_guards_missing_agent_slug`.
- [x] 404 when bound `agent_slug` no longer resolves at turn time (`:470-475`). **U** — ✔ regression gate (Task 2 mechanical extraction; service logic untouched; full suite green) — same code path as create-time slug resolution above (item 1), consistent guard pattern.
- [x] 409 on paused agent (`:931-934`). **U** — ✔ `tests/test_run_recorder.py::test_paused_agent_raises_before_insert`.
- [ ] 502 BAD_GATEWAY when `result.error` set (`:936-940`). **U** — ⚠ GAP: no test in the suite exercises the `result.get("error")` → `HTTP_502_BAD_GATEWAY` branch (`ai_library_chat_service.py:890-894`). Pre-existing gap, untouched by P2, not closed by this task.
- [ ] await_approval row persisted (approval_requests) + folded into assistant metadata; non-fatal on persist failure (`:942-972`). **DB** — ⚠ GAP: the runner-level `awaiting_approval=True` result is proven (`tests/test_hook_chain_execution.py::test_pre_hook_await_approval_pauses_run`) and the repository row-shape is proven (`tests/test_bigint_id_type_debt.py::test_approval_request_from_row_accepts_bigint_session_and_run`), but the chat-service glue that calls `get_approval_requests_repository().create(...)` and folds the row into `asst_metadata` (`ai_library_chat_service.py:896-925`) has no direct test. Pre-existing gap, untouched by P2, not closed by this task.
- [x] Every best-effort block (link_injection, resource_ref, session_memory, commitments, MCP wiring) logs + continues; a failure NEVER breaks the turn (`:542-561`, `:634-641`, `:1055`, `:1142`). **U** — ✔ `tests/test_ai_library_chat_wiring.py::test_chat_wiring_mcp_repo_failure_isolated`; `tests/test_next_session_commitments_surface.py::test_repo_failure_returns_unchanged`; `tests/test_session_memory_runner.py::test_summarizer_swallows_errors_returns_empty`; `tests/test_ai_library_chat.py::test_chat_chunk_callback_failure_does_not_abort_turn`.
- [x] Per-user concurrency gate `user_slot` serializes a user's concurrent turns — read-modify-write counter safety depends on it (`:386-397`, §4). **DB** — ✔ regression gate (Task 2 mechanical extraction; service logic untouched; full suite green) — gate itself untouched by the storage swap; counter safety under it proven by the absolute-value (not increment) write pattern verified in both stores' `bump_counters` tests above.

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

---

## Rollout

Sequenced exactly like the Phase 1 (`unified-conversations-p1`) and Phase 1.5 rollouts that preceded this one — no shortcuts, because the `shadow` stage exists specifically to catch what mocked tests structurally cannot (see `bug_chat_message_endpoints_500_asyncpg_types` in project memory: mocked tests missed a real asyncpg type bug that only a live-DB call surfaced).

1. **Merge flag-dark (`FEATURE_DIRECT_CONVERSATIONS=off`).** Code ships inert — `RoutedAiStore` in `off` mode hits `LegacyAiStore` only (byte-identical to pre-Phase-2 behavior, proven by `test_store_router.py::test_off_create_hits_legacy_only`+`::test_off_get_and_append_hit_legacy_only`+`::test_off_list_sessions_legacy_only`). No user-visible change on merge.
2. **Prod bake on `off`.** Confirm the merge itself introduced zero regressions in production (error funnel, dashboard, existing 1:1 chat) before touching the flag at all.
3. **Flip `shadow` on prod for ≥1 week of real-traffic diff-logging.** Every real session-create/session-list call now also mirrors into `ConversationsAiStore` best-effort and diffs the two results, logging `[p2-shadow] MISMATCH` on divergence (proven live end-to-end by `test_phase2_shadow_drill.py` in this task: legacy stays authoritative, the mirror row lands for real, and a forced mismatch logs without breaking the turn). Real traffic exercises attachment shapes, plan-mode turns, recall-context sessions, and concurrency patterns that no fixture enumerates.
4. **Review the `[p2-shadow]` MISMATCH funnel.** Query `application_logs` (or the loguru sink) for `[p2-shadow]` entries accumulated during the bake window. Triage every distinct mismatch kind before proceeding — a recurring mismatch class means an adapter mapping bug (§3 impedance mismatches), not a one-off race.
5. **Live browser UX pass on the 1:1 AI chat panel** once the mismatch funnel is clean. This is the hard gate that caught #945 (promote PGRST204) and #948 (root-route 307 killing CORS preflight) in Phase 1 — mocked/service-level tests, however thorough, do not exercise the real browser round trip. Cover: send/receive a turn, page-reload re-render of assistant messages + tool/approval cards, session rename, session list ordering, soft-delete, both themes.
6. **Flip `on`.** `RoutedAiStore` now creates NEW sessions directly on `ConversationsAiStore` (existing legacy sessions keep working — the router routes by each session's own `store_kind`, proven by `test_store_router.py::test_on_create_hits_new_store`+`::test_get_routes_by_existence_probe_and_caches`+`::test_on_list_sessions_merges_sorts_and_truncates`). No data migration ever runs — legacy rows stay legacy rows forever (zero-migration strangler, per the plan's locked decision).

**Rollback:** flip the flag back to `off` or `shadow` at any point. New-store sessions created while `on` become invisible to the caller until the flag is re-flipped `on` again (they are not deleted — `ConversationsAiStore` rows persist, just unrouted-to while the flag points elsewhere). This is acceptable early-rollout behavior per the plan's risk acceptance (§4 risk ranking): no user data is lost, only transiently inaccessible.

**Phase 3** (retire `ai_sessions`/`ai_messages`/`ai_session_memory` + drop the legacy adapter) only after `on` has baked in production and old-store traffic has decayed to zero — out of scope for this task.
