# TODOS

跨 milestone 跟踪未完成项。每条必须有 What / Why / Pros / Cons / Context / Depends on。

---

## TODO-AI-001: agent_run_events 高 QPS 监控（M3+）

**What**: M3+ 监控 `agent_run_events` 表 INSERT QPS。如果 > 50/s，考虑改 batch INSERT 或 partition by `created_at`。

**Why**: M1.A CostAuditor hook 每个 tool call 写一行 `agent_run_events`。一个 chat 跑 5 iteration → 5 行/run。10 用户并发 → 50 行/秒 INSERT。当前 PG 单行写没问题，但用户量增长后是潜在瓶颈。

**Pros**: 提前发现性能拐点，避免事故。

**Cons**: 现阶段不需要任何代码改动，只是监控提醒。

**Context**: 来自 plan-eng-review 2026-04-25 outside voice 提示。`agent_run_events` 表设计在 M1.A migration 里。CostAuditor hook 实现见 `backend/app/services/hooks/cost_auditor.py`。

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

**Context**: 来自 plan-eng-review 2026-04-25 outside voice 提示。Compaction 实现见 `backend/app/services/llm_compactor.py`（M1.A 周 5）。

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

**Context**: 来自 plan-eng-review 2026-04-25 outside voice 提示。Message bus 设计在 M2 design doc。`backend/app/services/agent_workers/inbox_processor.py`（M2 创建）。

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

**Context**: Adversarial review #1. `agent_runner.py:78` calls adapter.call BEFORE PreToolUse hook.

**Status**: pending — M2

---

## TODO-AI-006: Memory writer task — async loop + idempotency (M2)

**What**: write_memory_task uses `asyncio.run()` per Celery task; no idempotency key. Risk: ~100ms loop creation overhead per task; concurrent tool calls in same second can double-write same facts.

**Context**: Adversarial review #15, #16. `tasks/memory_tasks.py:51-55`.

**Status**: pending — M2

---

## TODO-AI-007: LLM retry+FALLBACK global deadline (M2)

**What**: Worst case retry+fallback can spin 7-15 min before final fail. Cancel polling depends on Supabase round-trip which may itself be the cause of failures being retried. Add total_deadline_seconds ceiling.

**Context**: Adversarial review #9. `llm_retry_middleware.py:204`, `llm_fallback_chain.py:101`.

**Status**: pending — M2

---

## TODO-AI-008: Memory cache empty-result short TTL (M1.B follow-up)

**What**: Empty recall results cached for full 5min TTL. User rephrases 3s later, still no recall. Use 30s TTL for empty results.

**Context**: Adversarial review #14. `retriever.py:130`.

**Status**: pending

---

## TODO-AI-009: Compactor edge cases — interleaved system msgs (M2)

**What**: Compaction algo may miss orphans when multi-call assistant replies are split across boundary by interleaved system messages. Need iterative re-discovery loop.

**Context**: Adversarial review #4, #5. `llm_compactor.py:222-235`.

**Status**: pending — M2

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
