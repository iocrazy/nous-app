# Proposal: Agent Context Compaction + Sub-Agent Task Tool

**Status**: Draft (2026-05-09)
**Author**: heygo + Claude
**Tracking issue**: TBD (open after sign-off)

## Context

User-reported pains, real, repeatable:

1. **Context overflow** — long conversations 撞 context window，agent 失忆 / 异常截断 / API 报错。
2. **Sub-agent 缺失** — 当前 `agent_runner.py` 是单层 loop，遇到大块工作（research / 多步骤代码生成 / 多文档比对）只能塞进主 agent 自己跑，污染主 context、拖慢响应。

Anthropic 在 [Effective harnesses for long-running agents](https://www.anthropic.com/engineering/effective-harnesses-for-long-running-agents) 给出解法：planning + sub-agents + filesystem + detailed prompt 四件套。mediahub 已有前三件、缺的是把这两个 pattern 落到 agent_runner。

## Goals

- 单 agent 能跑超过 200K token 累计输入（自动 compact 老消息），不再撞上下文上限
- 主 agent 能 spawn sub-agent 干 self-contained 任务、拿 summary 回来，不污染主 context
- 父子 run 可在 Runs tab 树状下钻审查
- **0 个新依赖**（不接 langchain / langgraph / langsmith / openai-agents-sdk）

## Non-goals

- 不替换 `agent_framework/` 7500 行
- 不替换 DBOS workflow runtime
- 不引外部 framework
- 不做 multi-agent 并发协作（M:M handoff），只做主→子 spawn-and-return（1:N）
- 不做 LangSmith 风格 SaaS 追踪（issue #194 留给 Langfuse 自托管）

## High-level design

```
┌─────────────────────────────────────────────────┐
│  Main Agent (token budget: model_max)           │
│                                                 │
│  ContextCompactor.maybe_compact() ─┐           │
│   - prune tool results @ 60%       │           │
│   - compact early turns @ 80%      │  每轮 loop  │
│   - emergency compact @ 90%        │  入口调用   │
│  ────────────────────────────────  ┘           │
│                                                 │
│  Tools: [Skill, Task, ...]                      │
│             │                                   │
│             ▼  Task(subagent="...", prompt="...")
│   ┌──────────────────────────────┐             │
│   │  SubAgentDispatcher          │             │
│   │  - load agent_def by slug    │             │
│   │  - compose system prompt     │             │
│   │  - run nested AgentRunner    │             │
│   │  - parent_run_id 写 telemetry│             │
│   └──────────────────────────────┘             │
│             │                                   │
│             ▼                                   │
│   ┌──────────────────────────────┐             │
│   │  Sub Agent (fresh context)   │             │
│   │  IDENTITY/SOUL/AGENT 三层     │             │
│   │  自己的 tool 子集            │             │
│   │  跑完返回 summary envelope   │             │
│   └──────────────────────────────┘             │
│             │                                   │
│             ▼  {summary, key_findings, ...}     │
│   主 agent 用结构化结果继续推进                  │
└─────────────────────────────────────────────────┘
```

## Phase plan (ship-able PR 切片)

### Phase 1 — Context Compactor (1.5 天)

**Goal**: 给 agent_runner 加分级 token budget + tool result pruning + 老消息 compact。

**Files**:
- 新建 `backend/app/agent_framework/context_compactor.py` (~250 行)
- 改 `backend/app/services/ai/runner/agent_runner.py` 每轮 loop 入口 ~10 行调用
- 复用 `agent_framework/tokenizer.py`、`tool_result_pruner.py`、`message_truncation.py`（不改）

**Trigger thresholds**:
| 区间 | 策略 |
|---|---|
| 0-60% | 正常 |
| 60-80% | `tool_result_pruner` 把大 tool result 替换成短 reference |
| 80-90% | `_compact_head(keep_recent=4)` — 用 cheap 模型 summarize 早期 turns |
| 90%+ | `_compact_head(keep_recent=2, force=True)` |

**Tests**:
- `tests/test_context_compactor.py` — 4 个 case：绿区 noop / 黄区 prune / 橙区 compact / 红区 emergency
- mock `tokenizer.count_tokens` 制造各区间触发
- 验证 `keep_recent` 永远保留最近 N 轮原文

**Rollback**: feature flag `AGENT_AUTO_COMPACT=false` (env)，绕开调用，行为回到现状。

**Risk**: 假阳性 prune — 早期 turn 里 user 之前贴的关键 prompt 被压成 summary。**缓解**: 永远保留 system prompt + 最近 4 轮原汁原味；compact 出来的 summary 用 `[Earlier conversation summary]` 标签 prefix，agent 看得到这是压缩内容。

---

### Phase 2 — Compaction Summarizer (1 天)

**Goal**: 接 cheap 模型（Haiku 4.5 / Qwen-Plus）给 Phase 1 的 `_compact_head` 用。

**Files**:
- 改 `backend/app/services/ai/providers/ai_provider.py` — 加 `summarize(messages, max_tokens=2000)` helper
- 用现有 `adapters/claude.py` Haiku 4.5 endpoint + `QwenAdapter` 任一可选

**Strategy**: 成本敏感 — 默认 Haiku 4.5（成本 $1/MTok input vs Sonnet $3）。Admin 配置可切 Qwen-Plus（更便宜，质量稍降）。

**Tests**:
- `tests/test_summarizer.py` — mock provider call，断言返回 `{role: system, content: "[Earlier conversation summary]\n..."}` 格式
- 真跑一次 cheap 模型集成测（CI optional，本地 dev 跑）

**Rollback**: 跟 Phase 1 共享 feature flag。

**Risk**: cheap 模型 summary 漏关键事实 → 主 agent 后续推理出错。**缓解**: summary prompt 强制要求列出"用户提及的所有 ID / URL / file path / 数字"，事实保真度优先于自然度。

---

### Phase 3 — SubAgent Task Tool (3 天)

**Goal**: 主 agent 能 call `Task(subagent_type, prompt)` spawn 子 agent，跑完拿 summary 回。

**Schema migration** (Phase 3a):
- `supabase/migrations/212_agent_runs_parent_run_id.sql` — `agent_runs` 表加 `parent_run_id UUID FK to agent_runs(id) ON DELETE SET NULL`
- 加 index `idx_agent_runs_parent` for 树查询

**Files**:
- 新建 `backend/app/services/ai/runner/subagent_dispatcher.py` (~300 行)
- 新建 `backend/app/services/ai/runner/task_tool.py` (~150 行) — Task tool definition + JSON schema
- 改 `backend/app/services/ai/runner/agent_runner.py` 注册 Task tool（跟 Skill tool 同级）
- 改 `backend/app/services/ai/runner/run_recorder.py` 写入 `parent_run_id`

**Task envelope** (sub-agent 返回主 agent 的格式):
```python
{
    "summary": str,          # 主要 summary，~200-500 token
    "key_findings": list,    # bullet 关键发现
    "files_created": list,   # 涉及的 resource / file paths
    "tokens_used": int,      # 透明成本
    "sub_run_id": str,       # 主 agent 想 drill-down 时拿这个
    "status": "success" | "incomplete" | "failed",
}
```

**Tests**:
- `tests/test_subagent_dispatcher.py` — mock provider，验证 sub agent 起 fresh context、parent_run_id 写对、envelope 格式
- 集成测：主 agent 真 spawn `script_ai` 做 outline → 主 agent 收 envelope → 用 envelope 决定下一步

**Rollback**: 不注册 Task tool → 主 agent 没工具 spawn，行为回到现状。

**Risk**:
- 死循环（sub agent spawn sub-sub agent spawn ...）→ 加 max_depth=3 强制截断
- 主 agent 把整个 context 当 `context_summary` 塞给 sub agent → sub agent context 也超 → 加 `context_summary` max_tokens=2000 截断
- DBOS workflow_id 冲突 → sub agent run 有自己的 dbos_workflow_id，跟主 run 通过 parent_run_id 关联，不在 task_tracking 共用 row

---

### Phase 4 — Runs Tab Tree View (1 天)

**Goal**: 前端 Runs tab 加父子展开，看主 run 调了哪些 sub run、各自 token / latency / outcome。

**Files**:
- 改 `frontend/components/RunsTab.tsx` — 加 expandable tree row
- 改 `frontend/services/agentRunsService.ts` — query `parent_run_id IS NULL`（top-level）+ 按需 fetch children
- 改 `frontend/types.ts` — `AgentRun.parent_run_id`, `AgentRun.children?: AgentRun[]`

**Tests**:
- `frontend/__tests__/RunsTab.test.tsx` — render mock tree，断言展开 children 渲染

**Rollback**: 前端独立 PR，回滚不影响后端 / agent loop。

---

### Phase 5 — E2E + Observability (1 天)

**Goal**: 跑通"主 agent → spawn sub agent → return summary → 主 agent 继续"完整链路；加结构化日志便于诊断。

**Files**:
- `backend/tests/test_agent_e2e_compact_subagent.py` — 端到端：构造 50 turn 对话 → 触发 Phase 1 compact → 主 agent call Task → sub agent fresh context 跑通
- `backend/app/services/ai/runner/agent_runner.py` — 加结构化 log（`compact_triggered`, `subagent_spawned`, `subagent_returned`）通过 loguru `{}` 占位（不踩 issue #194 Bug D）

**Observability**:
- `agent_runs.metadata` 加 `compact_count`, `compact_tokens_saved`, `subagent_count`
- Runs tab 卡片显示这些数字

---

## Decisions (signed off 2026-05-09)

| # | Decision | 选定 | 备注 |
|---|---|---|---|
| D1 | Compaction 用什么模型？ | **Admin 可配，默认 Haiku 4.5** | system_settings 加 key `compaction_provider`，admin UI 可热切。Phase 2 多 ~5 行。 |
| D2 | Token budget 阈值 | **60 / 80 / 90**（Anthropic blog 推荐） | Admin 也可配 (`compaction_thresholds` JSON key) 但不在 v1 范围 |
| D3 | sub-agent max_depth | **3** | 主 → sub → sub-sub。超出 raise + log |
| D4 | Task envelope 字段 | **6 个**（summary / key_findings / files_created / tokens_used / sub_run_id / status） | 加字段后续兼容 OK |
| D5 | sub-agent 流式 status 反馈 | **v1 不做**，跑完一次性返回 envelope | Phase 6 加（user 等 30s+ 没进度时再做） |

## Time / risk summary

| Phase | 时间 | 风险 | Ship as |
|---|---|---|---|
| 1 | 1.5 天 | 中（compact 质量 + tokenizer 准） | PR #198 |
| 2 | 1 天 | 低（cheap model wrap） | PR #199 |
| 3a (schema) | 0.5 天 | 低（加列 + index） | PR #200 |
| 3b (dispatcher + task tool) | 2.5 天 | 中（递归 + 死循环 / context bleed） | PR #201 |
| 4 | 1 天 | 低（前端独立） | PR #202 |
| 5 | 1 天 | 低（验收 + 日志） | PR #203 |

**Total: ~7.5 天**, 6 个 PR (Phase 3 拆 schema + code 两个)

每 PR 独立 mergeable + 独立 deployable + 独立可回滚。Phase 3b 必须等 3a 部署后才合（schema 先于代码）。

## Reference implementations to read (借鉴 design pattern, 不引代码)

- [Anthropic — Effective harnesses for long-running agents](https://www.anthropic.com/engineering/effective-harnesses-for-long-running-agents)
- [Claude Code `/compact` 命令](https://github.com/anthropics/claude-code) — production 级 head compaction 写法
- [Deep Agents sub_agent.py](https://github.com/langchain-ai/deepagents/blob/main/src/deepagents/sub_agent.py) — Task tool reference impl，~200 行干净
- [OpenAI Agents SDK Handoff docs](https://openai.github.io/openai-agents-python/handoffs/) — handoff vs spawn-and-return 概念区别

## Open questions

1. 主 agent 在 compact 之后，能否仍然 reference 早期 turn 里的具体 file path / ID？
   - **答**：cheap 模型 summarize prompt 显式要求 enumerate ID / URL / path → 有损但足够主 agent 后续 lookup
2. sub agent 失败（model error / step retries exhausted）怎么把信号传回主 agent？
   - **答**：envelope.status = "failed"，主 agent prompt 应教会它读 status 字段决定 retry / fallback
3. 多个 sub agent 并行 spawn 怎么做？
   - **答**：Phase 1-5 不做。M+ Phase 6 加 `Task(parallel=true, ...)` 用 asyncio.gather

## What this proposal is NOT

- 不是 LangGraph state machine — 我们继续用 DBOS workflow + 平 agent loop
- 不是 multi-agent supervisor pattern — 单向 spawn-and-return，不做对等协作
- 不是 framework 替换 — `agent_framework/` 47 文件 7500 行原封不动

---

## Sign-off

- [x] D1-D5 decisions OK (2026-05-09)
- [x] Phase 顺序 + time estimate 接受
- [x] Kickoff Phase 1 immediately
