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

## 完成账（2026-09-05 / 06 执行，单 session 不中断）

| Task | PR | 状态 | 实测改判点 |
|---|---|---|---|
| T1 schema（mig 453） | #2121 | 已合并 + 生产迁移成功；publication 含 `agent_run_inbox`，drift 脚本 exit 0 | `public.snowflake_id()` 不存在 → `generate_snowflake_id()` |
| T2 钩子链 | #2122 | 已合并 | — |
| T3 事件单一入口 / step 括号 / 折叠 / 中断收尾 | #2124 | 已合并 | run 路径 tool_call / assistant 在 `step_end` **之后**落行 → 折叠器把它们挂回刚结束的 step；retry 的 `at` 由发射方盖，fold 不读时钟 |
| T4 收件箱 | #2125 | 已合并 | 仓库/路由撞名（`inbox_repository` / `inbox_router` 是通知箱）→ `agent_run_inbox_repository` / `agent_inbox_router`；issue 会话的 run 只带 `conversation_id`（`issue_id` 事后回填）→ 目标解析经 `conversation_ai_meta.context_type='issue'` 自动带上 issue |
| T5 预算钩子 | #2126 | 已合并 | 花费是 issue 级（先前 run 的 `cost_cents` + 本 run 折叠值）；`clear_budget` 才能写 NULL |
| T6 rollup + 对账 | #2127 | 已合并 | 对账候选把"没有任何 run"留给 stranded monitor；`SET LOCAL ROLE service_role` 收成 `merge_execution_state` 单点 |
| T7 前端接缝 C | #2128 | 已合并 | `/runs/{id}/events` 投影原本没有 `turn/step`，补两列 |
| T8 Issue 详情页 | #2129 | 待合并（堆叠链头） | `IssueCostLine` 并入 StatusBlock；`DeliverablesZone`/`SubtaskBar` 有别的消费方，保留并被区块包装（计划的"删除"改为"包装"） |
| T9 Issues 主页 | #2130 | 待合并 | 横条第五类 `inbox_pending` 未做：没有列表级待领计数端点，不放假类型 |
| T10 聊天插话 + 双视图 | #2132（前端）/ #2131（后端） | 后端待合并；前端待合并 | 会话 steer 要在后端同时落一条消息行，否则历史回读看不到（与 issue 评论"保留评论行"同纪律） |
| T11 任务中心 | #2133 | 待合并 | `AGENT_RUN_SELECT` 补 `conversation_id/issue_id` 才有插话目标 |
| T12 修复 | #2134（后端）/ #2135（前端） | 待合并 | 见下 |

### 真栈验收结果（调试账号，生产栈）

跑 `script_ai` 一轮，**先**向会话收件箱投一条 steer 再发消息：

- ✅ 转录：`user → inbox_claimed{turn:1,step:1} → step_start → step_end{usage,duration_ms} → assistant → turn_end{completed}`，`view.revision = 6`
- ✅ 收件箱行 `claimed_run_id / claimed_turn / claimed_step` 齐；模型回复明显吃到了插话内容
- ✅ `GET /issues/{id}/progress`：phase=idle、budget、inbox_pending 随投递变 1、sub_issues、origin 缺省；`PATCH budget_cents` 150 生效、-1 → 422、`clear_budget` → NULL
- ❌→✅ **`metadata_json.view / cost` 是 jsonb STRING**（二期的 `todos / last_retry` 生产存量 3+1 行也全是）：`json.dumps + cast(JSONB)` 双重编码。mock 掉 session 的单测看不出。修法 `bindparam(type_=JSONB)`（#2134），真库回滚事务验证老/新写法分别落 `string` / `object`。前端 selectors 对存量字符串行 `JSON.parse` 一次（#2135）。
- ❌→✅ 转录 payload 的嵌套字段（`usage / counts / todos / result`）被 `_truncate_payload` 字符串化，前端折叠器当对象读 → 改按真实 wire 形状读（#2135）
- ❌→✅ `view.context` 常年 null：`measure_context` 只在压缩时喂 → 每个 `step_end` 用本次 prompt tokens 喂一次（#2134）
- ⚠️ `cost_cents = null`、`run.cost.spent_cents = 0`：`ai_model_prices` **没有 `doubao-seed-2-0-lite-260428`**（预设 agent 的模型）。预算钩子在补价格行之前永远不触发。这是数据不是代码，需运营补一行。

**未做真栈验收（原因）**：预算 80%/100% 落行（缺价格行）；`turn_end(interrupted)`（需让生产 run 心跳过期，等于在生产上杀 worker，不做；单测 + 突变覆盖）；issue 评论运行中改投（需一个正在跑的 issue 根 run 与人工评论同时发生；单测覆盖，路径与会话 steer 相同）；`todo_write` n/m 跳动（`script_ai` 没有 todo 工具，模型用正文写了计划）。

### 合并后手工清单

1. 补 `ai_model_prices` 的 doubao lite 行 → 再跑一次带预算的 issue 派发，看 `budget_check{warn}` 与卡片变黄
2. 前端链（#2129 → #2130 → #2132 → #2133）按顺序合并后 `npm run e2e:prod`
3. Issue 详情页真机走查：cockpit 四格、Trajectory 只 live 展开、评论运行中被领取
4. 第 2 期（pause / resume、类型化提问、回放 + fork、schedule、continuable 子代理、逐工具超时）另立计划

### 进程中的纪律教训

- **合并前必须 `non-success == 0`**：#2127 在 rebase 后 checks 重新排队时被合并（旧 5/5 结论已过期）；结果无害（本地全量绿）但流程有洞，后续所有合并改为先数 non-success。
- **mock 掉 session 的写路径测试要配一次真库回滚验证**：jsonb 路径类型（2026-08-27）与 JSONB 绑定双重编码（2026-09-05）是同一族。

## 上线后账（2026-09-06，全部前端链合并之后）

前端链按 T8 → T9 → T10 → T11 顺序合并（末 #2133 → `4919096d`），`version.json` 4919096，`e2e:prod` 3/3，调试账号真登录截图三处新 UI 均在。随后的真栈走查与补验又挖出六处，全部当天合并上线并复验：

| PR | 症状（真栈） | 根因 | 修法 |
|---|---|---|---|
| #2137 | 7 月的 probe issue（in_review）在列表显示 `running · 860h`，与同页 rollup 的 idle 打架 | 六处同形谓词把「有 `dbos_workflow_id` 且不在 done/cancelled」读成 live；id 从不清除 | `isIssueLive = id && status === 'in_progress'`（与 issue_lifecycle / reconcile sweeper 同口径），六处合一 |
| #2138 mig 454 | 90d 内 7 个在用模型只有 doubao pro 有价格行，其余 `cost_cents` 恒 NULL | 上架模型不带价格行 | 补 lite/pro（方舟 ≤32K 档 ¥ @7.10）、DeepSeek V4 三款（官方峰时价）、ModelScope 与自托管 0 行；每行显式 `supports_vision`（DB 行优先且 `bool(NULL)=False` 会剥图） |
| #2139 | 没有机制防止下一个模型再次静默无价 | — | admin 列表端点每行 `price_coverage`（priced/missing/not_applicable/unknown），与探针 `last_test_status` 正交；admin 页对 missing 画红 Tag；读表失败报 unknown 不报 missing |
| #2142 | 零预算 issue：钩子落 `budget_check{halt,100}`，rollup 却报 `pct null / ok` | rollup 的 `budget > 0` 守卫让 0 像不限 | 0 → 有花费即 100（over） |
| #2146 | todo run `view.step` 3/3 正确，`metadata_json.todos.todos` 为空 → 详情页 Steps 整表二期起一直空 | `_legacy_todos` 写死 `[]` | 折叠把条目留在 `view.todos`（字段白名单、上限 `MAX_TODO_ITEMS`、整值替换），legacy 从 view 取 |
| #2148 | doubao lite 把 `"?op=replace&items=…"` 塞进 `file` 连错四次，整轮零 todo 快照 | Skill 工具 schema 只声明 `skill/file`，内建 todo 的入参模型看不到 | 声明可选 `op/items/id`；prompts README 补「工具 schema」三问 |

**补验通过的一期未验项**：`budget_check` 落行（halt 记录、run 不中断）；issue 评论运行中 `diverted_to_inbox=true` → 下一步 `inbox_claimed`、模型采纳插话；todo n/m 随快照推进（#2148 后 lite 首次调用即正确）。**仍未真栈验**：`turn_end(interrupted)`——要在生产上杀 worker，不做。

**教训追加**：
- 「读正常 ≠ 服务正常」的 agent 版：n/m 计数还在，整表却空了两期无人发现——**派生键替换原始数据时，要检查下游是否还有人读原始形状**。
- 模型用不了它没被展示的参数。工具的入参契约必须写进 inputSchema，不能只写在错误信息里；改模型可见面同时补 README 三问。
- 「自带借口的失败模式」又一例：探针 / 价格表 / 预算 rollup 三处都是「没有信号」而不是「红灯」，与 `reference-self-concealing-failure-modes` 同族。
