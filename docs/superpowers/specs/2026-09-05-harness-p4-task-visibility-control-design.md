# harness 第四轮：任务可见性与控制面 —— 设计 spec

> 状态：已由用户拍板（2026-09-05）。画布（六页 UI 线框 + 骨架图 + 时间轴 + 分期）：
> https://claude.ai/code/artifact/f256ccc7-363b-425e-b493-1ed51e44c0f1
> 前序：一期 `2026-08-22-harness-adoption-plan.md`、二期 `2026-08-25-harness-phase2-task-visibility-design.md`、
> 三期 `2026-08-26-harness-phase3-typed-interaction-design.md`（三期内容并入本 spec 第 2、3 期）。

## 0. 问题与目标

用户对「AI 进程任务管理与消费」四类不满（2026-09-04 勘察后确认全部命中）：

| 痛点 | 现状 | 本 spec 的回答 |
|---|---|---|
| 看不清：跑到哪、为什么卡 | 三张账（task_tracking / agent_runs / issues）各自为账，UI 自己拼；Issue 页只有一个状态词；`agent_runs` 曾整整不在 Realtime 发布里（#2113 已修） | 事件日志 → 折叠注册表 → 整值视图 → 四个界面同读 |
| 管不住：不能暂停 / 改向 / 追问 | cancel 是轮询一个布尔位；插话要等这轮结束；needs_input 只能等 issue 回复 | 收件箱是唯一队列，runner 在 step 边界领取；pause = cancel keepInbox |
| 编不起：多 agent / 定时 / 外部触发 | 子代理同步阻塞；定时全写死；agent 不能写依赖 | 第 2 期：schedule → 收件箱、continuable 子代理、回放 + fork |
| 用不上：产出消费与回看 | 产出与 run 无登记关系；Runs 页无检索 | 第 3 期：deliverable 咽喉点 + 血缘 + 版本 diff、检索 / @引用、日报、效率账 |

对撞对象：deepseek-harness（`/media/heygo/program/projects-code/github-repos/deepseek-harness`，d347e703，46 个子系统全量扫过两轮）。
**设计重写，不复制代码**。

## 1. 骨架：五条原语（画布第一页第 1 图）

### ① 事件日志是真相，且可重放到任意一步
`agent_run_transcript_events` 是唯一真相。**每条事件带坐标 `{turn, step}`**（dsh Location）。新增事件类型：

| 类型 | payload | 时点 |
|---|---|---|
| `step_start` | `{turn, step, model, prompt_fingerprint, tools_hash}` | 每次 LLM 调用前 |
| `step_end` | `{turn, step, usage:{prompt,completion,cached}, cost_cents, duration_ms, finish_reason}` | 每次 LLM 调用 + 其工具执行结束 |
| `inbox_claimed` | `{inbox_id, kind, turn, step}` | runner 领取收件箱条目时（替代二期计划里的 `steer_claimed`） |
| `deliverable` | `{kind, ref_id, version, parent_version, turn, step}` | 产出登记咽喉点（第 3 期启用，白名单第 1 期一次放行） |
| `budget_check` | `{spent_cents, budget_cents, pct, action}` | 预算钩子（80% 提醒 / 100% 停） |

二期已有：`user / assistant / tool_call / error / system / llm_retry / todo_write / compaction_start / compaction_summary / compaction_end / turn_end`。
**推论**：`fold(events[:seq])` 即任意时刻的完整状态 → 回放 = 拖 seq；fork = 用 `events[:seq]` 重建 messages 起新 run（`agent_runs.fork_of_run_id / fork_at_seq`）。

### ② 投影是注册表，一条日志多张整值
`run_projection.register(event_type)(fold_fn)`；`apply(state, event_type, payload)` 纯函数、零 IO；未注册事件**返回同一引用**。四张视图：

| 视图 | 内容 | 存放 |
|---|---|---|
| `run.view` | `{v, phase, step{done,total,label}, retry{attempt,max,until}, context{used_pct,window}, blocked{code,message}, children{total,done}, ended{reason}, inbox_pending, revision}` | `agent_runs.metadata_json.view`（替代二期 `todos / turn_end_reason / last_retry` 三键） |
| `run.cost` | `{spent_cents, by_step[], by_model{}, by_tool{}, budget_cents?, pct}` | `agent_runs.metadata_json.cost` |
| `run.lineage` | `{deliverables:[{ref, version, step, prompt_fingerprint, model, cost_cents}]}` | `agent_runs.metadata_json.lineage`（第 3 期） |
| `issue.rollup` | 多 run 聚合：`{phase, paused_at, current_run, runs[], sub_issues, inbox_pending, budget}` | 读时计算，`GET /issues/{id}/progress` |

`phase` 优先级：`paused > waiting_input > running > blocked > done`。`revision = seq`，前端丢弃旧 revision。

### ③ 收件箱是唯一队列（按持久目标键控）
表 `agent_run_inbox(id, target_kind ∈ {conversation, issue}, target_id, user_id, kind ∈ {steer, answer, pause, resume, budget_reply}, content jsonb, created_at, claimed_at, claimed_run_id, claimed_turn, claimed_step, expired_at)`。
- 领取：`UPDATE … WHERE target… AND claimed_at IS NULL FOR UPDATE SKIP LOCKED RETURNING`；DBOS 路径包成独立 step（重放回放已领条目）。
- 只有**根 run**（`parent_run_id IS NULL`）领取；无 target 的 run（backfill 等）不领。
- 注入形态：`<inbox_message kind="…" at="…">` 框（登记 `OWNED_FRAMES`，正文 `escape_frame_prose`）。
- **issue 评论即收件箱**：`POST /issues/{id}/messages` 在该 issue 有运行中根 run 时改投 inbox（同时保留评论行）；空闲时走既有派发。
- 目标已终结 → 409 `target_ended`；孤儿条目由每日 sweeper 标 `expired_at`。

### ④ 产出必须登记且带血缘（第 3 期启用咽喉点，第 1 期只放白名单）
`register_deliverable(run, kind, ref_id, version, parent_version)` 是**唯一入口**（写资源 / 写画布 / 写分镜 / 发布都经它）→ `deliverable` 事件 + `run_deliverables` 表。没登记 = 不存在（与「DBOS 失败必须 raise」同族纪律）。

### ⑤ 预算是 step 边界的一个钩子
`issues.budget_cents`（NULL = 不限）。`BudgetGateHook`：80% → `budget_check{action:"warn"}` + view 变黄；100% → `budget_check{action:"halt"}` + 停下以类型化提问「追加 / 收尾 / 取消」（第 2 期接类型化提问后才拦；第 1 期只记与变黄）。派发前预演 = 计划步数 × 该 agent 历史每步均价。

## 2. 三个可插拔接缝（横切约束，全线适用）

| 接缝 | 形态 | 谁往里插 |
|---|---|---|
| **A. runner step 边界钩子链** | `StepBoundaryHook.before_llm_call(ctx) -> continue \| stop(reason) \| inject(messages)`；有序注册于 `build_agent_runner_stack`；`_run_turn_inner` / `_stream_turn_inner` 各调一次 `hooks.run(ctx)`（穷尽守卫钉两处） | cancel、pause、inbox.claim、budget.gate、question.inject（第 2 期）、schedule（第 2 期） |
| **B. 投影折叠注册表** | `run_projection.register(type)` | 每个事件族一个 fold 函数；未知事件同引用 |
| **C. 前端区块注册表** | `issueBlocks.register({id, zone ∈ {cockpit, context, timeline}, order, match(issue), component})`；任务中心 `bodies` 同模式 | StageBrief / Deliverables / Subtasks / Runs / Budget / Links 各一项；轨迹渲染器一份代码四处用 |

**边界**：注册显式（装配文件一行一个），不做 import 副作用发现；注册表可被测试枚举；钩子链只放横切行为。

**统一 emit**：`runner/events.py::emit(recorder, type, payload)` 是唯一 best-effort 落事件入口（合并二期三个同形助手）；源码守卫：`app/services/ai/` 下除 `events.py` / `run_recorder.py` 外不得直接出现 `record_event(`。`RunEventWriter(run_id).append()` = 插事件 + 折叠 + 整值写，`RunRecorder` 组合它，sweeper 也用它。

## 3. 数据模型变更（迁移先行，单独 PR）

| 变更 | 说明 |
|---|---|
| `agent_run_transcript_events` CHECK 放行 `step_start / step_end / inbox_claimed / deliverable / budget_check` | 一次放齐三期所需 |
| `agent_run_transcript_events.turn INT, step INT`（可空） | 坐标列；旧行 NULL；索引 `(run_id, seq)` 已有 |
| 新表 `agent_run_inbox` | 见 ③；RLS 按目标 owner；`ADD TABLE` 进 realtime 发布（照 452 写法） |
| `agent_runs.pause_requested BOOL DEFAULT false` | 运行中 run 的暂停信号位 |
| `agent_runs.fork_of_run_id BIGINT NULL, fork_at_seq INT NULL` | 第 2 期 fork |
| `issues.paused_at TIMESTAMPTZ NULL, budget_cents INT NULL` | 目标级暂停与预算 |
| `conversation_ai_meta.paused_at TIMESTAMPTZ NULL` | 聊天路径的暂停（第 2 期后才用，先建列） |
| 新表 `run_deliverables(id, run_id, seq, kind, ref_id, version, parent_version, created_at)` | 第 3 期 ④ |

取号执行时重扫（当前 453 起）。`TurnEndReason` 加 `interrupted`（sweeper 补 turn_end）与 `paused`。

## 4. 接口

| 端点 | 作用 | 失败语义 |
|---|---|---|
| `POST /ai-library/inbox` `{target_kind, target_id, kind, content}` | 投递 | 目标终结 409 `target_ended`；answer 选项不匹配 400；非 owner 404 |
| `GET /ai-library/inbox?target_kind&target_id&pending=1` | 队列 | — |
| `POST /issues/{id}/pause` / `resume` | 目标级暂停 / 恢复（先排空收件箱） | resume 未暂停且无待领 → 409 |
| `GET /issues/{id}/progress` | `issue.rollup` | 只读，带 `computed_at` |
| `GET /ai-library/runs/{id}/events?types=&upto_seq=` | 回放取事件（`upto_seq` 第 2 期） | 已有端点扩参 |
| `POST /ai-library/runs/{id}/fork` `{at_seq, steer?}` | 第 2 期 | — |
| `PATCH /issues/{id}` `{budget_cents}` | 设预算 | — |

## 5. UI（画布第 2–6 页；均由接缝 C 组合）

- **Issue 详情页（方案 A 定稿，深色）**：cockpit 四格（步骤 / 上下文 / 预算 / 下一步 + 暂停·取消）→ 时间线（轨迹渲染器：run 块 + 人的评论 + 交付物卡 + 子 issue 节点；**不堆叠**：只有当前 step 是展开的活动块，之前 step 折成一行）→ 评论框（= 收件箱）；右栏按 origin_kind 注册的上下文块，多「预算」。
- **Issues 主页面**（照生产骨架）：Quick 前四枚阶段芯片（等我 / 运行中 / 暂停 / 定时）；Group 新增 **Phase**（默认）；行加驾驭舱列 + 悬停动作；「等我的」扩到五类（问题 / 审批 / 待确认 / 暂停 / 待领取）；选中一行右侧分栏打开详情。
- **聊天面板**：Chat ｜ Trajectory 双视图同一条日志；作曲区运行中即「插一句」；Plan First → 计划审阅卡（批准 / 改一处 / 打回，第 2 期）。
- **任务中心**：agent run 与 DBOS 后台任务同一卡形；Active 卡加 暂停 / 插一句；历史按目标折叠，筛选按 turn_end 原因。
- **轨迹渲染器**是四处共用的一份组件；Chat / Trajectory 只是两种折叠策略。

## 6. 分期（画布第一页第 3 图）

| 期 | 内容 | 对应 plan |
|---|---|---|
| **1 骨架** | 接缝 A/B/C；事件坐标 + step_start/end；`run.view` + `run.cost`；收件箱 steer（聊天 + issue 评论）；预算钩子（记 + 变黄）；sweeper 补 `turn_end(interrupted)`；UI：详情页 A · 主页面 · 聊天插话 · 任务中心 · 轨迹渲染器 | `2026-09-05-harness-p4-phase1.md` |
| **2 控制与分叉** | pause / resume；类型化提问（选项 / plan-review / 审批 / 预算追加）；回放 + fork；schedule；continuable 子代理；逐工具超时 | 另立 |
| **3 产出与账** | deliverable 咽喉点 + 血缘 + 版本 diff；检索 + @引用；日报；效率账；消息反馈；制片人视图（另立产品 spec） | 另立 |

## 7. 纪律（继承并加严）

- 每个功能先列「合并哪些同形代码」再列「新模块唯一入口」；PR 描述单列「复用 / 删除了什么」。
- 迁移与消费代码分 PR；取号前 fetch 全分支重扫。
- 突变复做；真栈验收（readyz + 容器内代码 + 事件真落行 + 前端不刷新即变化）。
- 前端读 `metadata_json.*` 一律经 selector（`runView.ts`），组件不摸 JSON。
- 新增 realtime 订阅表 = 同 PR 加 `ADD TABLE`（守卫 `check-realtime-publication-drift.sh` 会红）。

## 8. 明确不做 / 不搬

agent-team 独立任务板（映到 issues）；webhook 运行时；workflow 脚本引擎；spill；system-prompt 装配注册表；plan mode 本体（nous 已有 Plan First）；聊天路径的 pause（会话续聊即 resume）。

## 9. 已知真缺陷（本轮勘察捎带发现，独立小票）

- `issues.execution_state` 与 run 终态不对账：MH-1 显示 `running · turn 7 · 2474h`，而 `agent_runs` 无 running 行 → 第 1 期 `issue.rollup` 派生 phase 时以 run 为准，并加对账 sweeper。
- 生产 realtime 发布曾丢 4 张表（#2113 已修 + 守卫）。
