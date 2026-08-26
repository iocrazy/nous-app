# harness 借鉴第二期设计：任务跟踪与完成度可视化（2026-08-25）

> 来源：deepseek-harness（MIT, b150a551）第二轮对撞。第一期五波已全部上线
> （账见 `docs/superpowers/plans/2026-08-22-harness-adoption-plan.md` 头部回填）。
> 本期聚焦用户点名的方向：**任务跟踪、完成度** —— agent 在干什么、干到第几步、
> 为什么停，这三问目前对用户全部不可见。
> 沿用第一期铁律：**一律重写设计，不复制代码**；每波开工先验本 spec 的假设
> （第一期五波里四波的计划描述与实况不符，照抄会做错四次）。

## 0. 现状与证据（写 spec 时实测，执行时请复核）

| 事实 | 证据 |
|---|---|
| nous 已有 agent todo（Phase K/L）：`TodoItem{id:int, content:str, status: pending/in_progress/completed, active_form:str?}`，挂在 `SkillToolService.todo_list`，模型经 `Skill(skill='todo', op=replace/show/complete/in_progress)` 操作 | `backend/app/agent_framework/agent_todo.py:50`、`skill_tool_service.py:137` |
| 但它**纯内存、turn 结束即擦、用户全程不可见** | agent_todo.py docstring 自书 "wiped at end of turn"；全仓无任何 UI/持久层消费 |
| dsh 对照：`todo/write` 是**持久 session 事件**，整表快照落日志（last-write-wins），UI 直接渲染完成度 | dsh `packages/todo/tool-todo/README.md`、`docs/subsystems/session.md:133` TodoItem（刻意无 id——整表替换不需要身份） |
| `agent_run_transcript_events` 已有 `record_event(event_type, payload)` 写路径（W1-B 为 `llm_retry` 用过），event_type 有 CHECK 白名单，现值 user/assistant/tool_call/error/system/llm_retry | migration 436；`run_recorder.py:391` |
| ~~该表没有读端点~~ **修订（2026-08-25 复核）：读端点已存在** —— `GET /api/v1/ai-library/runs/{run_id}/events`（after_seq/limit 分页、外人 404），前端 `useRunToolActivity` 已用它渲染工具 chips；issue timeline 已折 run 卡 | `ai_library_router.py:2335` `list_run_events`；本行初版写错，被执行前复核抓住——§5.1 已相应改判 |
| 任务中心 agent run 卡的数据源是 `agent_runs` 行（含 `metadata_json`，业务字段，随 Realtime 推整行） | `frontend/components/TaskCenter/useAgentRunTasks.ts`、路线 C 规则 3 |
| 压缩只把统计写进 metadata，**中途崩溃无痕迹** | `context_compactor.py` 无 start/end 括号 |
| dsh 压缩三事件：start 先落 → summary+替换 → end 最后落；**崩溃留下可检测的孤儿 start，而不是谎称完成的 end** | dsh `docs/subsystems/compaction.md` 事件表 |
| run_turn 的结束方式散落：正常 stop / preflight 拒绝(length) / cancelled / abort_reason / Max iterations —— 无类型化词表，UI 只见 completed/failed | `agent_runner.py:427-599` 各出口 |
| W3-1（warm-prefix）在途：`summarize_warm_prefix` 已实现 8/11 绿，**3 个接线红灯未接**；worktree 未提交 | `.worktrees/feat-harness-w3-warm-prefix`（见 §6） |

## 1. W3-1 收尾（在途，最先做）

半成品接完即可，设计已定型（第一期已拍板成本挪移）：

- `summarize_warm_prefix(adapter=, system_message=, tools=, head=, model=, max_tokens=)` 已在
  `backend/app/agent_framework/summarizer.py` 实现并 8 测全绿：字节级前缀重放（对象同一性
  断言）、同 adapter 同模型、tool-call 回复拒收、reasoning 剥离、空文本 raise、
  尾部孤儿 tool_calls 边界回退。
- **未接的三件**（红灯已写好在 `backend/tests/agent_framework/test_warm_prefix_summarize.py`）：
  1. `ContextCompactor._compact_with_summary` 增可选参 `system_message=None, tools=None, adapter=None`；
     有 adapter+system_message → 走 warm，warm 抛异常 → 回落既有 `summarize(head)`（链条：
     warm → legacy 便宜模型 → emergency cap）；收敛守卫（比源短才收）对两条路径同样生效。
  2. `maybe_compact` 透传这三个参数。
  3. runner 调用点（`agent_runner.py:1088`）补 `adapter=self.adapter, tools=composed.tools`
     （run_turn 与 stream_turn 共用 `_preflight_compact_and_budget`，只此一处）。
- 归因头（dsh 的 `x-*-compact: 1`）**不做**：adapter 无逐调用 header 通道，单独立项不值。

## 2. 核心设计：todo 持久化 + 完成度上 UI（本期主菜）

### 2.1 事件（真相源）

`Skill(todo)` 的每次**成功变更**（op=replace/complete/in_progress；show 不算）追加一条
transcript 事件——照 dsh 整表快照，**推完整状态、绝不推裸 delta**（第一期 W2 整值规则）：

```json
event_type: "todo_write"
payload: {
  "todos": [{"id": 1, "content": "跑测试", "status": "in_progress", "active_form": "正在跑测试"}],
  "counts": {"total": 7, "completed": 3, "in_progress": 1}
}
```

- 当前列表 = 最近一条 `todo_write`（last-write-wins），重放/重启/事后审计都成立。
- 保留 nous 的 `id`/`active_form` 字段（dsh 无 id 是因为它没有 complete-by-id 操作；nous 有，
  id 是既有模型契约，不为对齐 dsh 而砍——"声明不一致 ≠ 漂移"那条纪律的正用）。
- 写入经 `RunRecorder.record_event("todo_write", payload)`——recorder 为 None（无记录的
  调用路径）时静默跳过，todo 功能本身不受影响。
- **遥测永不失败业务**：record_event 抛异常只 warning（与 llm_retry 观察者同款容纳）。

### 2.2 快照镜像（UI 消费面）

事件流没有读端点（W1 欠账），本期**不为 todo 单开端点**。UI 消费走已有的 Realtime 面：
每次 todo_write 事件落库的同时，把同一份 payload 镜像到
`agent_runs.metadata_json.todos`（业务字段，路线 C 规则 3 允许业务代码 PATCH；
`agent_runs` 行本来就随 Realtime 推给任务中心）。

- 镜像是 best-effort：镜像失败不回滚事件（事件是真相源，镜像是缓存）。
- 前端 `agentRunPresentation.ts` 读 `metadata_json.todos.counts` 渲染
  **"3/7 · 正在跑测试"**（`active_form` 优先，无则 content）；卡片展开列全表。
- UI 文案英文（仓库 UI 语言规范）；进度不用 emoji。

### 2.3 迁移（一次放行本期全部事件类型）

CHECK 白名单一次加齐，避免逐波 churn（436 的教训）：

```sql
-- 439: event_type += todo_write / compaction_start / compaction_summary /
--       compaction_end / turn_end
```

迁移 PR 与代码 PR 分开（第一期纪律）；影子演练四件套照 436（旧拒/幂等/新收/乱值仍拒）。

## 3. 压缩事件括号（第一期 W3-5 的偿还）

`_compact_with_summary` 的调用方（`maybe_compact` orange/red 分支）落三事件：

| 事件 | payload | 时点 |
|---|---|---|
| `compaction_start` | `{tier, tokens_before, window}` | 决定要摘要后、调用前 **同步先落** |
| `compaction_summary` | `{summary_tokens, head_tokens, attempts, path: "warm"\|"legacy"\|"emergency_cap"}` | 摘要被接受后 |
| `compaction_end` | `{tokens_after, tokens_saved, error?}` | 无论成败最后落；失败带 error |

关键语义（dsh 原样）：**start 先落、end 最后落** —— 中途崩溃留下无配对 end 的孤儿 start，
可检测；绝不能反过来产出一个谎称完成的 end。同样只在 recorder 存在时落，失败只 warning。
`CompactionStats` 不变（metadata 继续记，事件是补充不是替代）。

## 4. TurnEndReason 类型化

新枚举（`backend/app/services/ai/runner/turn_end.py`）：

```python
class TurnEndReason(str, Enum):
    COMPLETED = "completed"            # 自然收尾(finish_reason=stop)
    PROVIDER_LENGTH = "provider_length" # provider 截断(finish_reason=length)
    CONTEXT_REJECTED = "context_rejected" # preflight 预算拒绝
    MAX_ITERATIONS = "max_iterations"  # 工具循环上限
    CANCELLED = "cancelled"            # 用户取消/abort
    ERROR = "error"                    # 异常路径
```

- run_turn / stream_turn 的每个出口标定一个值；落 `turn_end` 事件
  `{reason, iterations, finish_reason?}` + 镜像 `metadata_json.turn_end_reason`。
- **穷尽性守卫**：测试枚举 run_turn 的 return/yield 终点数与标定点数一致（源码扫描式，
  照 frame-guard 范式），新加出口不标定即红。
- UI：任务卡 completed 态副标题区分 "Completed" / "Stopped at tool limit" /
  "Cut off by model limit"——failed 态已有 error_code，不动。

## 5. W1 读侧（欠账偿还，前端为主）

1. **重试计数读侧**：端点**已存在**（`list_run_events`，见 §0 修订），
   不新建。给它加可选 `types: str = ""`（CSV 过滤，空 = 全部，向后兼容）——
   轮询 todo/retry 时不用拖全量 assistant 正文。TaskDetailModal 由此拉 `llm_retry`。
2. **重试进度实时**：`llm_retry` 事件落库时镜像 `metadata_json.last_retry = {attempt, max_retries, delay_ms, model}`，
   ActiveTaskCard 渲染 "Retry 2/4 · waiting 3.2s"。
3. **errorChain**：`describe_llm_error` 已产完整描述；把 `exc.__cause__` 链逐层拼进
   `error_message`（每层 `type: msg`，截断按既有 500 字符预算），治 "fetch failed 掩盖真因"。

## 6. W3-1 在途工作区快照（执行者直接取用）

```
worktree:  .worktrees/feat-harness-w3-warm-prefix   （分支 feat/harness-w3-warm-prefix，基于 ca1dd44c）
已改未提交: backend/app/agent_framework/summarizer.py        （summarize_warm_prefix 完整实现）
新文件:     backend/tests/agent_framework/test_warm_prefix_summarize.py （11 测：8 绿 3 红）
红灯三个:   test_compactor_uses_warm_prefix_when_it_has_an_adapter
           test_warm_failure_falls_back_to_legacy_then_succeeds
           test_runner_threads_its_adapter_into_the_compactor
```

## 7. 非目标

- W2 的 projection 注册表/整值帧传输/断线重拉——维持第一期改判（无撕裂实证不搬）。
- W4 的 record 契约——同上。
- 容量收割（从 provider overflow 错误文本解析真实窗口回填 `_MODEL_WINDOWS`）——价值成立
  但独立且低优，**不进本期**；`_MODEL_WINDOWS` 缺的两个值（doubao-seed-2-0-lite、
  nous-qwen3-llm）优先走人工查证补表，note 自动消失即收口。
- todo 的跨 turn 持久（per-turn 语义不变——变更它是产品决策不是移植）。

## 8. 验收总则

- 每波突变复做（守卫拆掉要转红），落 commit message。
- 事件写入用真实 recorder 驱动到假 DB 边界，**mock 用真实 wire 形状**（血泪条款）。
- UI 波次跑 `npm run typecheck` 忽略存量错误清单外的新增；相关 vitest 文件全绿。
- 合并后：真栈发一个多步 agent 任务，任务中心能看到 "N/M" 进度在动；
  `agent_run_transcript_events` 里 todo_write/turn_end 有行。


## 9. Issues 详情页（2026-08-25 用户实测截图修订）

用户点进 issue（MH-48 todo / MH-55 blocked）看到的跟踪面：PROGRESS 栏只有
STATUS 一词 + RUNS 计数，Timeline 空，**blocked 不给任何原因**。复核结论：

| 缺口 | 实况 |
|---|---|
| 停摆原因只对一种形态可见 | `outcome_reason` 仅在 `status==needs_followup && agent_outcome==needs_input` 时经 NeedsInputCard 渲染（`IssueDetailView.tsx:443` 的门）；blocked / empty_output / 其它 parked 形态**原因在行上、前端不读**——"后端返回前端从没读"一族又一例 |
| 运行中无步骤进度 | run 卡有工具 chips（useRunToolActivity），但无 todo N/M——本期 Task 3 的事件正是它缺的数据 |
| 消费机器已在 | 事件端点 + 轮询 hook 范式 + timeline 折卡全部现成，**只差新事件类型的消费件** |

设计（全前端 + 一个端点参数，无新表无新推送）：

1. **停摆原因行**：PROGRESS 栏在 status 为 blocked/needs_followup/cancelled 且
   `execution_state.outcome_reason` 非空时加一行 reason（人话化前缀按
   `agent_outcome` 映射；needs_input 保持走 NeedsInputCard 不重复渲染）。
   ⚠️ 不确定点：blocked 可能由跨集依赖谓词写入而非 agent outcome——执行时
   `grep -rn "'blocked'" backend/app | grep -v test` 查写入方；若 deps 来源则
   reason 行落依赖名，核对后再实现。
2. **运行中 todo 进度**：新 hook `useRunTodoProgress(runId, isRunning)`
   （照 `useRunToolActivity` 的轮询范式，`types=todo_write`），取**最后一条**
   快照（last-write-wins）渲染 `{done}/{total} · {active_form|content}`；
   挂在 issue timeline 的 run 卡与 PROGRESS 栏（活跃 run 时）。
3. **重试可见**：同一轮询里 `llm_retry` 事件已可得，run 卡 "Retry 2/4"。

任务中心（第一期 Task 4 的 metadata 镜像消费）与本节不冲突：镜像面向
Realtime 整行推送的列表卡，事件轮询面向已打开的详情页——两个消费面同一真相源。
