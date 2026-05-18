# TODOS.md Reap — 2026-05-17

Per Batch 2 #9 of `~/.gstack/projects/iocrazy-mediahub/ceo-plans/2026-05-17-post-pr290-hygiene-and-hotspots.md`.

20 entries audited (TODO-AI-001..019 + TODO-CLEANUP-001).

## Outcome

| Status | Count | Entries |
|--------|-------|---------|
| ✅ DONE | 3 | #009, #013 (already marked), #015 |
| ❌ SUPERSEDED | 1 | #006 |
| ✏️ PATH UPDATED | 8 | #001, #002, #004, #005, #007, #015, #017, #018 |
| ⏸️ STILL VALID | 16 | rest |

3 of 19 originally `pending` AI entries became DONE/SUPERSEDED → kept 16 live. Plus TODO-CLEANUP-001 unchanged.

## DONE — TODO-AI-009: Compactor iterative re-discovery

**Code evidence**: `backend/app/services/ai/llm/llm_compactor.py:249-318` — `_safe_split_index()` already implements the iterative loop the TODO asks for. The `while candidate > 0:` outer loop re-discovers orphans each pass; the inner `while new_candidate >= 0:` walks back to the assistant message owning the orphan tool_call_id. Comments on lines 254-261 explicitly call out the "moving back may expose new orphans" case.

This was likely fixed during M1/M1.B harness work without anyone closing the TODO. Closed now.

## DONE — TODO-AI-015: Delegate cycle detection beyond depth

**Code evidence**: `backend/app/services/workforce/delegate_tool.py:181-195` — `_detect_cycle(target_agent_id=...)` is invoked before every Delegate; if the target's agent_id already appears in the run's ancestor chain (walked via `parent_run_id`), Delegate returns an error with `cycle_run_id` set. The docstring on line 7 now reads "Rejects self-dispatch and cycles (parent chain check)".

Closed.

## SUPERSEDED — TODO-AI-006: memory_tasks asyncio loop + idempotency

**Reason**: `backend/app/tasks/memory_tasks.py` was deleted in commit `1e87274c feat(dbos): D7 phase 3b — physical Celery cleanup` along with the rest of the Celery task layer. Memory writes are now driven by `memory_harvester` PostToolUse hook (`backend/app/services/infra/hooks/memory_harvester.py`) inline within the agent runner — no per-task `asyncio.run()` overhead, no concurrent-second double-write window because writes are sequential within a turn.

The original concern (Celery task spinning up a fresh asyncio loop per call) no longer applies because the call site no longer exists. Marked SUPERSEDED.

## Path updates (no semantic change)

After the 2026-04-23 `refactor(services): organize 63 flat files into 8 sub-packages` (commit `0940d84d`), these paths moved:

| TODO | Old reference | New path |
|------|---------------|----------|
| #001 | `backend/app/services/hooks/cost_auditor.py` | `backend/app/services/infra/hooks/cost_auditor.py` |
| #002 | `backend/app/services/llm_compactor.py` | `backend/app/services/ai/llm/llm_compactor.py` |
| #004 | `retriever.py:130` | `backend/app/services/ai/memory/retriever.py` |
| #005 | `backend/app/services/agent_runner.py:78` | `backend/app/services/ai/runner/agent_runner.py` (+ BudgetGuard now at `services/infra/hooks/budget_guard.py`) |
| #007 | `llm_retry_middleware.py:204`, `llm_fallback_chain.py:101` | both under `backend/app/services/ai/llm/` |
| #017 | `app/services/workforce/inbox_processor.py:217-244` | `:233-258` (re-confirmed) |
| #018 | `app/repositories/agent_workforce_repository.py:73-101` | `:144-180` |

Re-verified at audit time — all functions exist, semantics unchanged from when the TODO was written.

## STILL VALID — verified untouched

- **#001** agent_run_events QPS monitor: zero code change required; pure operational reminder.
- **#002** Compaction typing indicator: backend side is fine, frontend SSE work hasn't started.
- **#003** Realtime SLA fallback: workforce shipped (PRs #175, #208 etc.), but no 5s polling fallback wired.
- **#004** Memory recall Redis cache key: still uses `user_input_hash`; rephrase still misses.
- **#005** BudgetGuard predictive: verified `budget_guard.py:50-57` still checks `accumulated_cost_cents >= budget_cents` AFTER the call — the budget-busting call has already been paid for before the abort fires.
- **#007** LLM retry+fallback global deadline: no `total_deadline_seconds` ceiling added.
- **#008** Memory empty-result cache TTL: `retriever.py:147` still calls `_cache_set(cache_key, [])` with the full 300s TTL.
- **#010** pgvector ivfflat→hnsw: `migrations/156:43-45` still ivfflat lists=100; agent_memories table size not yet at threshold.
- **#011** agent_memories.user_id FK: `migrations/156:9` still plain UUID, no `REFERENCES auth.users ON DELETE CASCADE`.
- **#012** Advisory lock per-session: `liveness_scanner.py:62` still calls `pg_try_advisory_lock` via Supabase REST RPC — same architectural issue.
- **#014** Inbox dedup_key TTL after processed: partial index in `migrations/159` still scoped `WHERE dedup_key IS NOT NULL AND status IN ('unread', 'reading')`.
- **#016** force_terminate requeue + race fence: `state_machine.py:194-ish force_terminate` exists; no `requeue_task` call inside.
- **#017** Inbox starvation: `inbox_processor._agents_with_unread_messages` still `.select("recipient_agent_id").limit(500)` + Python `set()`.
- **#018** upsert_worker stomp: `agent_workforce_repository.py:144-180` — `upsert_worker` always overwrites `state`, `state_changed_at` etc.; no split between register vs update.
- **#019** Realtime missed-event backfill: `outbox_dispatcher._dispatch_to_user` marks delivered on Realtime insert, no ACK-on-frontend pattern.
- **TODO-CLEANUP-001**: scheduled_recovery.py `recover_stale_orchestrator_locks_workflow` still registered + firing hourly. Just created yesterday, no change.

## Re-grouping by gating

For Batch 3 hotspot prioritization and the upcoming AI Library `/plan-ceo-review` session:

### Gated on M2 workforce reactivation (8 entries)

#003, #005, #006 (closed), #012, #014, #015 (closed), #016, #017, #018, #019 — all touch workforce/dispatch/state-machine code. Reactivate as a batch when AI Library main line opens.

### Independent housekeeping (7 entries)

#001 monitoring, #002 UX, #004 cache key, #007 deadline, #008 cache TTL, #010 ivfflat, #011 FK CASCADE, TODO-CLEANUP-001 scheduled_recovery → none gated on bigger work; can be picked off individually.

## Procedure used

```bash
# For each TODO entry:
#  1. Grep for the file/function referenced
#  2. If file moved: update path
#  3. If gone: check git log --all -- <path> for deletion commit + SUPERSEDED
#  4. If found: inspect current implementation against TODO criterion
#  5. Classify DONE / SUPERSEDED / STILL VALID
#  6. Edit TODOS.md in place
```
