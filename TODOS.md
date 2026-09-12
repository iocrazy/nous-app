# TODOS

跨 milestone 跟踪未完成项。每条必须有 What / Why / Pros / Cons / Context / Depends on。

---

## TODO-AI-001: agent_run_events 高 QPS 监控（M3+）

**What**: M3+ 监控 `agent_run_events` 表 INSERT QPS。如果 > 50/s，考虑改 batch INSERT 或 partition by `created_at`。

**Why**: M1.A CostAuditor hook 每个 tool call 写一行 `agent_run_events`。一个 chat 跑 5 iteration → 5 行/run。10 用户并发 → 50 行/秒 INSERT。当前 PG 单行写没问题，但用户量增长后是潜在瓶颈。

**Pros**: 提前发现性能拐点，避免事故。

**Cons**: 现阶段不需要任何代码改动，只是监控提醒。

**Context**: 来自 plan-eng-review 2026-04-25 outside voice 提示。`agent_run_events` 表设计在 M1.A migration 里。CostAuditor hook 实现见 `backend/app/services/infra/hooks/cost_auditor.py`（path updated 2026-05-17 reap — moved during services 8-subpackage refactor）。

**Depends on**: M1.A 完成 + M3 启动。

**Status**: pending
**Owner**: heygo
**Created**: 2026-04-25

---

## TODO-AI-002: Compaction 延迟 UX（M3）

**What**: M3 做 Workforce UI 时同期加 "压缩中..." typing indicator，给 sonnet sidecall 5-15s 延迟提供视觉反馈。

**Why**: M1.A Compaction 触发时阻塞调用辅助 LLM 生成摘要。Qwen-Turbo 摘要 100k token 估计 5-15s。用户长对话第 21 轮发消息后等 15s 才看到 typing indicator，会以为系统挂了。

**Pros**: 大幅改善长会话用户体验。

**Cons**: 需要 streaming 协议支持，要改前端 SSE 处理逻辑。

**Context**: 来自 plan-eng-review 2026-04-25 outside voice 提示。Compaction 实现见 `backend/app/services/ai/llm/llm_compactor.py`（path updated 2026-05-17 reap）。

**Depends on**: M1.A Compaction 完成 + M3 启动。

**Status**: pending
**Owner**: heygo
**Created**: 2026-04-25

---

## TODO-AI-003: Realtime SLA 兜底策略（M2）

**What**: M2 worker 实现时必须明确 Supabase Realtime 是 best-effort，inbox 的 SLA 靠 5s 短轮询保底。如果总 channel 数 > 200 考虑 partition / 降级订阅策略。

**Why**: Outside voice 指出 Supabase Realtime 在 200-500 channel 就开始抖（不是文档上的 10k 上限）。4 个 agent × N 用户 = 大量 channel。1000 用户就 4000 channel，远超抖动阈值。**Realtime 不能当 SLA 主路径**。

**Pros**: 避免 M3 上线后用户报告"通知不及时"无法定位。

**Cons**: 短轮询是固定 5s 延迟下限。

**Context**: 来自 plan-eng-review 2026-04-25 outside voice 提示。Message bus 设计在 M2 design doc。`backend/app/services/workforce/inbox_processor.py`（path updated 2026-05-17 reap — workforce M2 已合 master via PRs #175/#208/etc）。

**Depends on**: M2 启动。

**Status**: pending
**Owner**: heygo
**Created**: 2026-04-25

---

## TODO-AI-004: Memory recall Redis 缓存 key 策略（M1.B → M2 review）

**What**: M1.B Memory recall 用 Redis 缓存。当前设计 key 含 `user_input_hash`，可能 cache miss 多。M2 review 时根据实际命中率数据决定是否改用 `semantic_topic_hash`（需要额外 LLM 调用提取 topic）。

**Why**: 同 session 内不同问题用同 hash 不命中 → cache miss 率高 → sonnet 二次筛选成本不降。但用 topic hash 又需要 LLM 调用，反而更贵。需要实际数据驱动决策。

**Pros**: 数据驱动，不预设过度优化。

**Cons**: M1.B 上线后要监控命中率指标。

**Context**: 来自 plan-eng-review 2026-04-25 Issue 4.4。

**Depends on**: M1.B Memory v1 上线 + 收集命中率数据 1 个月。

**Status**: pending
**Owner**: heygo
**Created**: 2026-04-25

---

## TODO-AI-005: BudgetGuard predictive check (M2)

**What**: BudgetGuard checks accumulated cost AFTER each LLM call, so the guard fires AFTER the budget-busting call has been paid for. Add predictive check: estimate next call cost, abort BEFORE adapter.call if estimate would exceed budget.

**Why**: At Qwen-Max prices, overshooting by one call = 5-10¢ on a 50¢ budget = 10-20% overshoot.

**Context**: Adversarial review #1. `backend/app/services/ai/runner/agent_runner.py` adapter.call runs before BudgetGuard hook. BudgetGuard at `backend/app/services/infra/hooks/budget_guard.py:50-57` still checks `accumulated_cost_cents >= budget_cents` after the call (re-verified 2026-05-17 reap).

**Status**: pending — M2

---

## TODO-AI-006: Memory writer task — async loop + idempotency (M2)

**What**: write_memory_task uses `asyncio.run()` per Celery task; no idempotency key. Risk: ~100ms loop creation overhead per task; concurrent tool calls in same second can double-write same facts.

**Context**: Adversarial review #15, #16. `tasks/memory_tasks.py:51-55`.

**Status**: ❌ SUPERSEDED — verified 2026-05-17. `backend/app/tasks/memory_tasks.py` was deleted by commit `1e87274c feat(dbos): D7 phase 3b — physical Celery cleanup`. Memory writes are now driven by `memory_harvester` PostToolUse hook inline within the agent runner — no per-task asyncio.run() overhead, no concurrent-second double-write window. Original concern no longer applies. See `docs/cleanup/2026-05-17-todos-reap.md`.

---

## TODO-AI-007: LLM retry+FALLBACK global deadline (M2)

**What**: Worst case retry+fallback can spin 7-15 min before final fail. Cancel polling depends on Supabase round-trip which may itself be the cause of failures being retried. Add total_deadline_seconds ceiling.

**Context**: Adversarial review #9. `backend/app/services/ai/llm/llm_retry_middleware.py`, `backend/app/services/ai/llm/llm_fallback_chain.py` (paths updated 2026-05-17 reap).

**Status**: pending — M2

---

## TODO-AI-008: Memory cache empty-result short TTL (M1.B follow-up)

**What**: Empty recall results cached for full 5min TTL. User rephrases 3s later, still no recall. Use 30s TTL for empty results.

**Context**: Adversarial review #14. `backend/app/services/ai/memory/retriever.py:147` — re-verified 2026-05-17 reap, empty result still cached with `DEFAULT_CACHE_TTL_S = 300`.

**Status**: pending

---

## TODO-AI-009: Compactor edge cases — interleaved system msgs (M2)

**What**: Compaction algo may miss orphans when multi-call assistant replies are split across boundary by interleaved system messages. Need iterative re-discovery loop.

**Context**: Adversarial review #4, #5. `backend/app/services/ai/llm/llm_compactor.py:249-318::_safe_split_index` (path updated 2026-05-17 reap).

**Status**: ✅ DONE — verified 2026-05-17. `_safe_split_index()` already implements the iterative re-discovery: outer `while candidate > 0:` re-checks orphans each pass; inner `while new_candidate >= 0:` walks back to the assistant message owning the orphan tool_call_id. Likely fixed during M1.B without closing the TODO. See `docs/cleanup/2026-05-17-todos-reap.md`.

---

## TODO-AI-010: pgvector ivfflat → hnsw (when table grows)

**What**: ivfflat with lists=100 is worse than seq scan for < few thousand rows. Migrate to hnsw or auto-tune lists.

**Context**: Adversarial review P2. `migrations/156_agent_memories_m1b.sql:43-45`.

**Status**: pending — when agent_memories > 10k rows

---

## TODO-AI-011: agent_memories.user_id FK to auth.users CASCADE (M2)

**What**: agent_memories.user_id is plain UUID with no FK. Stale memories outlive deleted users.

**Context**: Adversarial review P2. `migrations/156_agent_memories_m1b.sql:9`.

**Status**: pending — M2

---

## TODO-AI-012: PostgREST advisory lock per-session limitation (M2/M3 — P0)

**What**: `try_advisory_lock` / `advisory_unlock` called via supabase-py REST RPC are per-DB-session, but each REST call uses a (potentially) different PostgREST connection. The lock is acquired in session A and the unlock RPC may route to session B, leaving session A's lock held until the connection is recycled. Affects:
- `agent_runs_sweeper.SWEEPER_LOCK_KEY` (inherited from M1)
- `agent_workforce_tasks.{INBOX,OUTBOX}_LOCK_KEY` (M2)
- `WorkerStateMachine._hold_lock` per-agent locks (M2)

**Why**: Concurrent Celery beat workers can both believe they hold the lock and run ticks in parallel. Per-agent state transitions can race. The CAS guards on inbox/task claims (`status='unread'`, `lifecycle_status='queued'`) provide actual mutual exclusion at the row level, so the practical blast radius is bounded — but state_changed_at can flap and audit history can record racing transitions.

**Pros**: Fixing this hardens the entire dispatch/state-machine foundation across M1+M2.

**Cons**: Requires replacing the REST-RPC-based lock with either (a) pinned psycopg sessions per Celery worker process, (b) `pg_advisory_xact_lock` inside SQL functions that do the whole transition atomically, or (c) row-based locks (`SELECT ... FOR UPDATE` on agent_workers) — each is non-trivial.

**Context**: Adversarial review (Claude subagent, 2026-04-25) CRITICAL #1. Affects `app/tasks/agent_runs_sweeper.py:48-70` and `app/services/workforce/state_machine.py:_hold_lock`.

**Status**: ✅ RESOLVED at current scale (2026-05-31) — the broken PostgREST advisory-lock RPC was removed, not patched. The three call sites now use exclusion primitives that don't depend on per-session RPC routing:
- **Sweepers** (`app/workflows/agent_runs_sweeper.py`, `workflow_health_sweeper.py`) → ported to DBOS `@scheduled` workflows. DBOS derives a deterministic `workflow_id` (`sched-<name>-<iso>`) per tick and the system DB rejects duplicate ids, so only one worker runs each tick. The advisory-lock indirection is dropped entirely.
- **Per-agent state transitions** → the workforce runs as a single in-process scheduler (M3); `AgentWorkerPool` (`app/services/workforce/worker_pool.py:79`) holds a per-agent `asyncio.Lock` that serialises transitions for the same agent. `state_machine.py:23-40` documents the rationale.
- **Row-level CAS guards** (`status='unread'` / `lifecycle_status='queued'`) remain as the durable mutual-exclusion backstop.

**Residual (future-only)**: if/when the backend is scaled to **multiple FastAPI worker processes**, in-process `asyncio.Lock` no longer suffices. The documented fix at that point is a distributed primitive (Redis lock or `SELECT … FOR UPDATE`) — explicitly NOT the broken PostgREST RPC (see `state_machine.py:38-40`). Not a live concern at the current single-process deployment.

---

## TODO-AI-013: cascade_cancel_run trigger fan-out guard (M2 follow-up)

**What**: `cascade_cancel_run` fires AFTER UPDATE of `cancel_requested` and itself UPDATEs all children to `cancel_requested=true`, which RE-fires the trigger on each child. For a tree of N running children this is N nested executions of the same WHERE-scan. Worst case quadratic locking on `agent_runs`.

**Fix**: add `IF pg_trigger_depth() > 1 THEN RETURN NEW; END IF;` early-return, OR fold the cascade into a single recursive CTE statement.

**Context**: Adversarial review CRITICAL #2. `migrations/159_workforce_schema_m2.sql:228-239`.

**Status**: ✅ DONE — `migrations/160_cascade_cancel_depth_guard.sql` applied 2026-04-25. Function rewrites with `IF pg_trigger_depth() > 1 THEN RETURN NEW;` early-return; one outer UPDATE hits all children in a single statement.

---

## TODO-AI-014: Inbox dedup_key TTL after processed (M2)

**What**: Unique index on `agent_inbox(recipient_agent_id, dedup_key)` is partial — `WHERE status IN ('unread', 'reading')`. Once processed, the dedup expires. A flapping LLM that retries `Delegate(... dedup_key=X)` after the previous one finished will create duplicates instead of being deduped.

**Fix**: widen the partial WHERE to exclude only `expired`, OR stamp a TTL window in code (e.g. dedup_key valid for 5 min after creation), OR rate-limit Delegate at the tool level.

**Context**: Adversarial review HIGH #5. `migrations/159_workforce_schema_m2.sql:95-97`.

**Status**: pending — M2 follow-up

---

## TODO-AI-015: Delegate cycle detection beyond depth (M3)

**What**: Cycle protection is currently depth-only (`agent_depth >= 3`). A→B→A→B→A hits depth=4 and aborts, but A→B→A within 3 levels is permitted and could ping-pong harmlessly forever as long as the chain length stays ≤3.

**Fix**: walk `agent_runs.parent_run_id` and reject if `target_agent_id` already appears in the chain. Requires storing the agent chain on agent_runs (column or recursive CTE on lookup).

**Context**: Adversarial review HIGH #4. `app/services/workforce/delegate_tool.py:execute`. Module docstring updated to acknowledge depth-only protection.

**Status**: ✅ DONE — verified 2026-05-17. `backend/app/services/workforce/delegate_tool.py:181-195::_detect_cycle(target_agent_id=...)` walks parent chain via `parent_run_id`; rejects if target's agent_id appears in ancestor chain with `cycle_run_id` returned. Module docstring now reads "Rejects self-dispatch and cycles (parent chain check)" (line 7). See `docs/cleanup/2026-05-17-todos-reap.md`.

---

## TODO-AI-016: force_terminate must requeue in-flight task + race fence (M2 follow-up)

**What**: `WorkerStateMachine.force_terminate` skips the per-agent advisory lock by design (sweeper path — worker is presumed dead). But it does NOT call `requeue_task` for the worker's `current_task_id`, so the in-flight task is orphaned in `assigned`/`in_progress` state. Also TOCTOU vs a still-live worker: `read state='working'` → sweeper writes 'terminated' → live worker writes 'idle' overwrites.

**Fix**: in `force_terminate`, look up `current_task_id` and call `repo.requeue_task` if non-null. Add a CAS fence on the UPDATE: `WHERE worker_pid = <expected> AND heartbeat_at < <stale_before>`.

**Context**: Adversarial review HIGH #7. `app/services/workforce/state_machine.py:268-294`.

**Status**: pending — M2 follow-up

---

## TODO-AI-017: Inbox starvation under one-agent backlog (M2 follow-up)

**What**: `_agents_with_unread_messages` reads up to 500 inbox rows and dedupes recipients in Python. If one agent has 500+ backlogged messages, every other agent disappears from the tick.

**Fix**: replace with SQL `SELECT DISTINCT recipient_agent_id FROM agent_inbox WHERE status='unread'` via RPC (PostgREST `?select=...&limit=500` does NOT dedupe server-side).

**Context**: Adversarial review MEDIUM. `backend/app/services/workforce/inbox_processor.py:233-258::_agents_with_unread_messages` (line range updated 2026-05-17 reap — still .select("recipient_agent_id").limit(500) + Python set()).

**Status**: pending — fix when first observed in production telemetry

---

## TODO-AI-018: upsert_worker stomps state + wipes audit (M2 follow-up)

**What**: `repo.upsert_worker` always overwrites `state`, `worker_pid`, `worker_hostname`, `state_changed_at`. If called outside worker-startup (e.g. SM first-touch path), it stomps a `terminated` or `paused` agent back to `idle`.

**Fix**: split into `register_worker` (insert-only with `ON CONFLICT DO NOTHING`) vs `update_worker_state` (the existing one); only bump `state_changed_at` when state actually changes.

**Context**: Adversarial review MEDIUM. `backend/app/repositories/agent_workforce_repository.py:144-180::upsert_worker` (line range updated 2026-05-17 reap — still always stomps state / state_changed_at / worker_pid / worker_hostname).

**Status**: pending — M2 follow-up

---

## TODO-AI-019: Realtime missed-event backfill (M2 follow-up)

**What**: `_dispatch_to_user` relies on Supabase Realtime firing on the original outbox INSERT. If the user is offline / not subscribed at insert time, the message is lost (dispatcher marks delivered, frontend never sees it).

**Fix**: keep `delivered=false` until the frontend explicitly ACKs, OR have the frontend on connect query `agent_outbox WHERE recipient_user_id=... AND created_at > <last_seen>` to backfill.

**Context**: Adversarial review MEDIUM. `app/services/workforce/outbox_dispatcher.py:76-84`.

**Status**: pending — M2 follow-up before public launch

---

## TODO-CLEANUP-001: Delete recover_stale_orchestrator_locks workflow (D3d residue)

**What**: Remove the transitional `recover_stale_orchestrator_locks_workflow` from `backend/app/workflows/scheduled_recovery.py` and its scheduled cron registration.

**Why**: Workflow was D3d's "release dedup locks" job for the legacy orchestrator dedup model that PR-D7 replaced with DBOS-native workflow_id dedup. Original `_DEFERRED_TASKS.md` flagged it for deletion as transitional, but the deletion never happened. Now runs hourly doing nothing useful.

**Pros**: 1 fewer scheduled task firing; clearer scheduled_recovery.py.

**Cons**: ~120 lines of code to remove + 1 test file to update. Need to verify no live dedup pattern still relies on it.

**Context**: From deleted `backend/app/workflows/_DEFERRED_TASKS.md` (cleaned up 2026-05-17). Search history: `git log --oneline -- backend/app/workflows/_DEFERRED_TASKS.md`. Implementation at `scheduled_recovery.py:167` (step) + `:277` (workflow). Cron registration is `@DBOS.scheduled("30 * * * *")`.

**Status**: pending — P3 housekeeping
**Created**: 2026-05-17

---

## TODO-SECURITY-001: verify_folder_access guard for 7 folder-id endpoints

**What**: Write `verify_folder_access(folder_id, auth)` helper in `app/core/scope_guards.py` and apply to 7 endpoints in `resources_folders_router.py` that act on a folder_id with no ownership check.

**Why**: Audited 2026-05-18 (post PR #298). All 7 endpoints (DELETE /folders/{id}, POST trash/restore, PATCH, GET content-count, DELETE/PATCH smart-folders/{id}) currently use admin client, lookup folder by id, then mutate/read without verifying caller owns it. CRITICAL because DELETE /folders/{id} cascade-deletes contained resources.

**Pros**: Closes 7 horizontal-authz holes in one focused PR. Helper is reusable.

**Cons**: New helper needs design: read vs write semantics (does team member have folder write?). Test surface.

**Context**: Full triage at `docs/security/2026-05-18-resources-routers-full-audit.md` § resources_folders_router → "Still open".

**Status**: pending — HIGH security
**Created**: 2026-05-18

---

## TODO-SECURITY-002: verify_resource_read_access guard for 6 versions endpoints

**What**: Write `verify_resource_read_access(resource_id, auth)` helper and apply to 6 endpoints in `resources_versions_router.py` (GET versions list, POST set-current, DELETE version, GET hls/file, POST retry_transcode).

**Why**: Audited 2026-05-18. PR #274 added `verify_resource_write_access` for upload; sibling endpoints (read, set-current, delete, file-serve) all unguarded. Includes file-content leak (hls/file endpoints) and abuse vector (transcode trigger costs $).

**Pros**: Closes 6 horizontal-authz holes. Read semantics may differ from write (team members read team resources).

**Cons**: Read vs write semantics needs design (PR #274's write-only check may be too strict for reads).

**Context**: Full triage at `docs/security/2026-05-18-resources-routers-full-audit.md` § resources_versions_router.

**Status**: pending — HIGH security
**Created**: 2026-05-18

---

## TODO-SECURITY-003: verify_scope_access_optional + 3 trash-by-id endpoints

**What**: Write `verify_scope_access_optional(scope_type, scope_id, auth)` variant (handles `Optional[str] scope_id` default = caller's user_id) and apply to 3 endpoints in `resources_crud_router.py`: `/resources/by-platform-id/{id}` trash + by-media-id + unlink.

**Why**: These 3 took `Optional[str] scope_id` so the required-Query version of `verify_scope_access` from PR #274 / #298 couldn't be slotted in. With `Optional`, the default flow probably falls back to caller's user_id (= safe), but when caller PASSES scope_id, no validation runs (= leak).

**Pros**: ~30min PR, small helper variant.

**Cons**: Need to re-read each handler to understand the optional-default behavior before guarding.

**Context**: Full triage at `docs/security/2026-05-18-resources-routers-full-audit.md` § resources_crud_router → "Still open".

**Status**: pending — MEDIUM security
**Created**: 2026-05-18

---

## TODO-SECURITY-004: complete resources_crud + upload second-pass audit

**What**: Re-enumerate every endpoint in `resources_crud_router.py` (19 decorators) and `resources_upload_router.py` (4 decorators), confirm authz posture for each. First-pass audit (PR #299) listed only the scope_id-Query ones and missed scope-bearing endpoints that take resource_id from path or scope in body.

**Why**: First pass focused on the obvious shape matching PR #298. Adversarial review of #299 surfaced ~10 omitted endpoints in crud_router (PATCH /{id}, /permanent, /restore, /move, /transcode/batch, /file, /cover, /preview-sprite) plus `link-existing` in upload_router. Some (file/cover serving) may leak file content; some (transcode/batch) are abuse vectors.

**Pros**: Closes the audit's blind spot before SECURITY-001/002/003 helpers ship.

**Cons**: Pure investigation, no code changes in this TODO. Findings feed into SECURITY-001/002/003 scope.

**Context**: `docs/security/2026-05-18-resources-routers-full-audit.md` post-review note.

**Status**: pending — MEDIUM (investigation, blocks SECURITY-001/002/003 scope confirmation)
**Created**: 2026-05-18

---

## TODO-TEST-001: mimetypes 推断随宿主平台漂移，测试在 macOS 上必红

**What**: `tests/test_storage_migration_web_resource_files.py::test_migrate_web_resource_files_row_migrates_qishui_audio` 断言 `.m4a` 推断为 `audio/mp4`。macOS 的 mimetypes 数据库给的是 `audio/mp4a-latm`，Linux（CI）给 `audio/mp4`。修法是在测试里 pin 期望值到被测代码自己的推断函数，或用 `mimetypes.add_type` 在 fixture 里固定映射，而不是断言一个随平台变的字面量。

**Why**: 这是「测试进程必须与本机网络/环境隔离」（CLAUDE.md 2026-08-06 立约）的同族缺口 —— 一套结果取决于谁的机器在跑的测试不构成门禁。本机全量 pytest 因此多一个红点，掩盖真实回归。

**Pros**: 消掉一个平台相关假红，本机与 CI 结果对齐。

**Cons**: 纯测试改动，不影响生产行为。

**Context**: 2026-09-11 跑全量 backend pytest 时发现（13 failed 里除去 12 个已知的 distribution 沙箱假红，剩这一个）。在干净 master 上复现，与搜索修复无关。

**Depends on**: 无。

**Status**: pending — LOW
**Owner**: heygo
**Created**: 2026-09-11


---

## TODO-SEARCH-001: rpc_user_media_text_search 的 OR 链让所有 trigram 索引失效

**What**: `rpc_user_media_text_search` 的 WHERE 是一条横跨 parsed_media 列、resources 列和多个 EXISTS 子计划的 OR 链，每个分支还被运行时参数 `'x' = ANY(p_fields)` 门控。planner 无法做 BitmapOr，154 / 342 / 463 建的所有 trgm 索引对这个谓词形状全部用不上，计划恒为 resources⨝parsed_media 全扫 + 逐行 filter。改法是按 p_fields 拆成若干条 UNION ALL 的子查询，让每条各自命中自己的索引；tags 分支还要从相关 EXISTS 改成非相关预筛（先用 idx_tags_name_trgm 反查 resource_id 集合再 semi-join 回来）。

**Why**: 默认搜索范围在 2026-09-11 从 4 个字段扩到 6 个，tags / notes 两条分支现在每次搜索都跑。1383 行的试点账号是几十毫秒量级可接受，但 `search_service.py` 的 Tier-1c 注释明说设计目标是 10 万+ 拥有资源，那一档会退化到秒级。且 `/search/text` 没有速率限制，300ms 防抖是唯一节流，改搜索范围也会重新触发。

**Pros**: 把默认范围的成本从线性全扫降到索引驱动，让扩范围这件事可持续。

**Cons**: 要重写 RPC 主体，是查询形状改动不是加索引，需要在最大的真实账号上用 `EXPLAIN (ANALYZE, BUFFERS)` 对 4 字段 vs 6 字段做前后对照才能确认收益。

**Context**: 2026-09-11 搜索缺陷修复 PR 的两路专家审查独立指出同一点。当时的判断是先把正确性缺陷修掉，性能改造单独开。

**Depends on**: 无。

**Status**: pending — MEDIUM
**Owner**: heygo
**Created**: 2026-09-11

---

## TODO-SEARCH-002: resource_transcripts 的 RLS 策略列配对写错了

**What**: `resource_transcripts` 启用了 RLS，策略里拿 `resources.media_id` 去比 `resource_transcripts.resource_id`（见 `supabase/schema_baseline.sql` 的策略定义）。这两列不是一个东西，配对是错的。

**Why**: 今天不咬人，因为唯一的读方 `rpc_user_media_text_search` 是 SECURITY DEFINER 且属主是 postgres，直接绕过 RLS。一旦属主变更或有非 definer 的读方接进来，转录范围会静默返回零行而不是报错 —— 又一个「空输出被当成否定结论」。

**Pros**: 消掉一个只在改动别处时才会引爆的地雷。

**Cons**: 要先确认没有别的代码依赖当前（错误的）配对行为。

**Context**: 2026-09-11 搜索修复的对抗审查发现。该策略早于本次改动存在，本次只是新增了一个经 definer 读该表的路径。

**Depends on**: 无。

**Status**: pending — LOW
**Owner**: heygo
**Created**: 2026-09-11
