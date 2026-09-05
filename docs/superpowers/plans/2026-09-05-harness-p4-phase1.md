# harness 第四轮 · 第 1 期「骨架」实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
> 认领方式："执行 harness 第四轮第 1 期 Task N"。每个 Task 独立 worktree、独立 PR；迁移先行。

**Goal:** 立起五条原语中的 ①②③⑤ 与三个接缝，并把四个界面接到同一份整值视图与收件箱上。

**Architecture:** 事件日志（带 turn/step 坐标）→ 折叠注册表（`run.view` / `run.cost`）→ `agent_runs.metadata_json` 整值 → Realtime → 前端 selector → 区块注册表。控制面：收件箱 → step 边界钩子链 → 落回日志。

**Tech Stack:** FastAPI + SQLAlchemy ORM（禁 `text()`）、DBOS、Supabase Realtime、React 19 + vitest。

**Spec:** `docs/superpowers/specs/2026-09-05-harness-p4-task-visibility-control-design.md`

## Global Constraints
- 迁移与消费代码分 PR；取号前 `git ls-remote` 扫全部分支（当前 master 至 452）。
- 每个 Task 的 PR 描述必有「复用 / 删除了什么」一节（用户要求：整洁、复用、模块化、可插拔）。
- 突变复做：每条关键断言至少一次突变转红并记录在 PR。
- 真栈验收：`readyz` + 容器内 grep 代码 + 事件真落行 + 前端不刷新即变化。
- 前端读 `metadata_json.*` 只经 `runView.ts` selector；UI 文案英文 + i18n。
- 不得新增第四个 best-effort emit 助手（源码守卫）。

---

### Task 1: 迁移先行（schema 一次到位）
**Files:** Create `supabase/migrations/<N>_harness_p4_phase1_schema.sql`；Modify `backend/app/models/agents.py`（CheckConstraint 同步、新列、新表 ORM）、`backend/app/models/__init__.py`（导出）。
**Interfaces → 后续 Task 依赖：** 事件类型白名单含 `step_start step_end inbox_claimed deliverable budget_check`；`AgentRunTranscriptEvents.turn/step`；ORM `AgentRunInbox`、`RunDeliverables`；`AgentRuns.pause_requested/fork_of_run_id/fork_at_seq`；`Issues.paused_at/budget_cents`；`ConversationAiMeta.paused_at`。
- [ ] 取号：`git ls-remote origin 'refs/heads/*' | … | sort -n | tail -1` + 1
- [ ] 迁移正文：DROP/ADD CHECK（照 443）；`ALTER TABLE agent_run_transcript_events ADD COLUMN turn INT, ADD COLUMN step INT`；`CREATE TABLE agent_run_inbox(...)` + 索引 `(target_kind,target_id) WHERE claimed_at IS NULL` + RLS（owner via issues/conversation）+ DO 块 `ADD TABLE` 进发布；`agent_runs` 三列；`issues` 两列；`conversation_ai_meta.paused_at`；`run_deliverables`。
- [ ] ORM 同步（schema-drift 门禁会比对）；`uv run pytest tests/models -q`
- [ ] 生产库 `BEGIN; \i mig; ROLLBACK;` 演练，记录 NOTICE；`bash scripts/check-realtime-publication-drift.sh` 在事务内不可测，合并后复验
- [ ] PR → CI → 合并 → `run-migration.yml` 应用 → 复验发布含 `agent_run_inbox`

### Task 2: 接缝 A —— step 边界钩子链（纯 refactor，24h 内合）
**Files:** Create `backend/app/services/ai/runner/step_hooks.py`；Modify `agent_runner.py`（两处 `check_cancelled()` → `await self.step_hooks.run(ctx)`）、`ai_library_chat_wiring.py::build_agent_runner_stack`（注册 `CancelHook`）；Test `backend/tests/runner/test_step_hooks.py`。
**Interfaces:**
```python
class StepDecision(Enum): CONTINUE="continue"; STOP="stop"
@dataclass
class StepContext: run_id; turn: int; step: int; recorder; messages: list[dict]; composed; stop_reason: str|None=None; injected: list[dict]
class StepBoundaryHook(Protocol):
    name: str
    async def before_llm_call(self, ctx: StepContext) -> StepDecision: ...
class StepHookChain:
    def __init__(self, hooks: Sequence[StepBoundaryHook]): ...
    async def run(self, ctx) -> StepDecision   # 顺序执行；任一 STOP 即返回；单个 hook 抛异常 → 记录 warning 继续（分发器容纳异常）
```
`CancelHook` 复刻现有 `check_cancelled` 语义（STOP + `stop_reason="cancelled"`）。runner 收到 STOP 按 `stop_reason` 走既有出口（cancelled → `RunAborted`）。
- [ ] 红灯：顺序执行 / STOP 短路 / 坏 hook 不阻断 / 源码守卫（`_run_turn_inner` 与 `_stream_turn_inner` 各恰好一处 `step_hooks.run(`，且不再出现 `check_cancelled(`）
- [ ] 实现 + 全量 + 突变（删一处调用 → 守卫红；STOP 不短路 → 红）
- [ ] 行为零变化验证：既有 cancel 测试全绿

### Task 3: 接缝 B + 事件写入统一 + `run.view` / `run.cost`
**Files:** Create `runner/events.py`、`runner/run_projection.py`、`runner/folds/{todo,retry,compaction,turn_end,step,inbox,budget}.py`；Modify `run_recorder.py`（抽 `RunEventWriter`；`record_event(type, payload, *, turn=None, step=None)` 写坐标列；删 `_mirror_todos`；镜像只写 `view` / `cost`）、`todo_events.py` / `turn_end.py` / `context_compactor._emit` 改调 `events.emit`、`agent_runner.py`（每次 LLM 调用前后落 `step_start` / `step_end`，从 adapter usage 取 tokens，成本用 recorder 既有费率）、`agent_runs_sweeper.py`（`mark_heartbeat_lost` 后用 `RunEventWriter.append("turn_end", {reason:"interrupted"})`，已有 turn_end 的跳过）、`turn_end.py`（`INTERRUPTED`）。
**Interfaces:** `apply(views: dict, event_type, payload) -> dict`（`views = {"view": RunView, "cost": RunCost}`；未注册同引用）；`EMPTY_VIEWS`；`register(event_type)`。
- [ ] 红灯：逐 fold 单测；重放性质 `fold(all) == mirrored`；未知事件同引用；`events.emit` 三态；源码守卫无第四个 `record_event(`；step_start/end 在 run/stream 两路径各成对（源码守卫 + 行为测：一次 2 步 turn 落 2 对）；sweeper 补 interrupted 且不重复
- [ ] 前端读侧暂不改（Task 7 起）；本 PR 保留旧三键**一并写**（过渡一期），Task 7 合并后再删
- [ ] 突变：删一条 fold → 红；emit 改直调 → 守卫红；step_end 漏 usage → cost 测红

### Task 4: 收件箱后端 + `InboxClaimHook` + issue 评论改投
**Files:** Create `runner/inbox.py`（`claim(target, run_id, turn, step) -> list[InboxItem]`，DBOS 上下文自动 `@DBOS.step`）、`runner/inbox_hook.py`（`InboxClaimHook`：根 run 才领；注入 `<inbox_message>` 框；落 `inbox_claimed`）、`api/inbox_router.py`（POST / GET）、`repositories/inbox_repository.py`；Modify `boundary/frame_markers.py::OWNED_FRAMES` 登记；`issue_messages_router.post_issue_message`（有运行中根 run → 同时插 inbox）；`ai_library_chat_wiring` 注册 hook（顺序：cancel → inbox）；sweeper 加 `expire_orphan_inbox`。
- [ ] 红灯：原子领取（两 claimer 并发只一个拿到）；根 run 才领；注入框转义（`</inbox_message>` 载荷不逃框）；`inbox_claimed` 落坐标；409 `target_ended`；评论改投同时保留评论行；DBOS step 包装（用假 DBOS 上下文断言被包）
- [ ] 突变：去 `SKIP LOCKED`/`claimed_at IS NULL` → 并发测红；子 run 也领 → 红；不转义 → 框守卫红

### Task 5: 预算钩子（记 + 变黄）+ `PATCH /issues/{id}` budget
**Files:** Create `runner/budget_hook.py`（读 `views.cost.spent_cents` 与 `issues.budget_cents`；80% 落 `budget_check{warn}` 一次；100% 落 `{halt}`，第 1 期只记不 STOP）；Modify `issues_router` PATCH 加 `budget_cents`；`run_projection` 的 budget fold 写 `view.budget{pct,state}`。
- [ ] 红灯：80% 只报一次；无预算不落事件；view.budget 变黄；PATCH 校验非负

### Task 6: `issue.rollup` 端点 + execution_state 对账
**Files:** Create `services/issues/issue_rollup.py`（读多 run 的 view/cost + 子 issue + origin 解析器注册表 `origin_resolvers.register(kind)`）、`GET /issues/{id}/progress`；Modify `agent_runs_sweeper`：run 终态而 `issues.execution_state` 仍 running → 写 `agent_outcome` 对账（复用既有 allowlist 写法）。
- [ ] 红灯：phase 优先级 paused > waiting_input > running > blocked > done；MH-1 型漂移被对账；origin 解析器缺省返回 `{kind}` 不 500

### Task 7: 前端接缝 C + selector + 轨迹渲染器
**Files:** Create `components/TaskCenter/runView.ts`（`RunView`/`RunCost` 类型 + selectors）、`components/agentActivity/TrajectoryRenderer/`（`TrajectoryRenderer.tsx`、`foldEvents.ts`（事件族 → 节点；step 折叠；只当前 step 展开）、`nodes/*.tsx` 注册表）、`components/Todolist/issueBlocks.ts`（区块注册表 + 三 zone）；Modify `agentRunPresentation.ts`（`todoProgress/retryProgress/turnEndSubtitle` 改经 selector 或删）、`useRunToolActivity.ts`（改用 `foldEvents`）。
- [ ] 红灯：`foldEvents` 对同一 step 的多次 tool_call 只产一个活动节点；step_end 后折成摘要；未知事件不崩；注册表可枚举；selector 对旧行（无 view）全 null
- [ ] 突变：活动节点改为追加 → 红

### Task 8: Issue 详情页（方案 A 定稿）
**Files:** Modify `IssueDetailView.tsx`（本体只剩三 zone 渲染注册表）；Create `blocks/{CockpitBlock,StageBriefBlock,DeliverablesBlock,SubtasksBlock,RunsBlock,BudgetBlock,LinksBlock}.tsx`（从现有 StageBriefMirror / DeliverablesZone / SubtaskBar / PipelineRunStrip / IssueCostLine 迁入，旧文件删除）；`IssueChatThread` → 用 `TrajectoryRenderer`；`IssueReplyBox` 提示文案随 phase（运行中 = 插一句）；数据源 `useIssueProgress(id)`（Realtime `agent_runs` + `/progress` 轮询兜底）。
- [ ] 红灯：区块按 origin_kind 匹配（publish issue 不出现 StageBrief）；cockpit 四格读 selector；composer 运行中投 inbox、空闲走原路径
- [ ] 删除：StageBriefMirror.tsx、DeliverablesZone.tsx、SubtaskBar.tsx、IssueCostLine.tsx、PipelineRunStrip.tsx（迁为 block）

### Task 9: Issues 主页面
**Files:** Modify `IssueListView.tsx`（Group 加 `phase` 默认；Quick 前四枚阶段芯片；行加 cockpit 列 + 悬停动作；选中行右侧分栏 `IssueDetailView` 紧凑模式）；`AttentionStrip.tsx` + `attentionItems.ts`（五类：question / approval / review / paused / inbox_pending）；`uiIssue.ts` 加 `phase` 派生。
- [ ] 红灯：phase 派生优先级；五类聚合；行动作只在对应 phase 出现

### Task 10: 聊天面板插一句 + Chat｜Trajectory
**Files:** Modify AI 库聊天面板（`git grep planFirst` 定位）：运行中作曲区提示与投递到 inbox；视图切换用 `TrajectoryRenderer`（Chat 折叠策略 = 只显示活动行 + 计划卡）。
- [ ] 红灯：运行中发送 → POST /inbox；空闲 → 原发送；切换视图不重拉

### Task 11: 任务中心统一卡形 + 折叠历史
**Files:** Modify `ActiveTaskCard.tsx`（读 selector：cockpit 行 + 暂停/插一句）、`TaskListView.tsx`（历史按目标折叠 `groupByTarget`）、`taskResultKind.ts`/`bodies` 注册表统一。
- [ ] 红灯：agent 与 DBOS 任务同卡形；历史折叠按 issue_id / conversation_id / batch

### Task 12: 真栈验收 + 完成账
- [ ] 调试账号跑一个用 todo 的 agent：卡片 `n/m` 不刷新即变；`step_start/step_end` 成对落行；`run.view.revision` 递增；评论运行中被领取且转录出现 `inbox_claimed`
- [ ] 设预算 ¢1 跑一次 → `budget_check{warn}` 落行、卡片变黄
- [ ] 让一个 run 心跳过期 → 转录尾部 `turn_end(interrupted)`
- [ ] 计划文档回填完成账；记忆更新

## 波次与依赖
T1 → T2 → T3 → (T4, T5, T6 并行) → T7 → (T8, T9, T10, T11 并行) → T12。
