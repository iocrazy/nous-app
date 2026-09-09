# harness 第四轮 · 二期 2b-1「回放 + fork + 逐工具超时」设计

> 前序：P4 总 spec `2026-09-05-harness-p4-task-visibility-control-design.md`（§5 把「控制与分叉」列为第 2 期）；2a `2026-09-06-harness-p4-phase2a-control-plane-design.md`（已全部上线，2026-09-08 完成账）。
> 2b 拆两段：**本文 = 2b-1**（运行器与轨迹侧：回放、fork、逐工具超时）；2b-2 = schedule → 收件箱 + continuable 子代理（编排侧，要先接活 workforce 链，另立）。
> **状态（2026-09-09）**：用户拍板范围（选 A：先 2b-1）、fork 只做 issue run、超时让模型看到并继续；其余由作者定；**用户只验 UI**（§4 三页稿）。

## 0. 前提核对（执行前必读）

| 假设 | 实况（2026-09-09 勘察） | 结论 |
|---|---|---|
| fork 列已在 | `agent_runs.fork_of_run_id BIGINT NULL`（FK → agent_runs）、`fork_at_seq INT NULL`，mig 453 | 不加列 |
| 事件端点可回放 | `GET /ai-library/runs/{id}/events?after_seq=&limit=&types=` 已有；缺 `upto_seq` | 扩一个参数 |
| 前端已能折叠事件 | `components/agentActivity/TrajectoryRenderer/foldEvents.ts`（step 不叠：一个 step 一个节点）；issue 详情在 `IssueChatThread.tsx` 用 `<TrajectoryRenderer events isRunning>` | 回放是前端切片 + 现成折叠 |
| 事件类型白名单 | `agent_run_transcript_events_event_type_check`，459 是最新一版（DROP/ADD 幂等写法，不 SET ROLE） | 新类型 `fork` 走 460 |
| issue 的会话指针 | `issues.ai_session_id`（BIGINT，`issue_session.ensure_session` 缺则建） | fork 换会话就是换这个指针 |
| 回复轮次怎么装历史 | `AILibraryChatService.run_session_turn` 先 `get_messages(session_id)` 再 `build_history_messages` | fork 只要把重建消息写成新会话的 `ai_messages` 行，既有路径原样可用 |
| 工具超时 | 只有 run 级 `composed.timeout_sec`；`agent_framework.mcp_descriptor.Tool` 无超时字段；runner 的工具循环两处（`_run_turn_inner` / `_stream_turn_inner`）直接 `await` handler | 从零加，两处都要 |
| 步边界钩子 | `StepHookChain`：Heartbeat → Cancel → Pause → BudgetGate → InboxClaim | fork 的 steer 经收件箱在第一步被领取，不加新 hook |

## 1. 回放（Replay）

**原则**：回放不是新状态，是「把事件流切到 seq 再折叠」。后端零新表、零新事件。

- 端点：`GET /ai-library/runs/{id}/events` 加 `upto_seq: int | None`（含）。与 `after_seq` 可同用（区间）。
- 前端：`IssueChatThread` 的轨迹区加 `ReplayScrubber`：
  - 一次拉全事件（沿用现有分页拉法），本地持有 `events`；刮擦条的值是 seq，**刻度只落在 step 边界**（`step_start` 的 seq）与 `turn_end`；←/→ 逐刻度。
  - 显示 = `fold(events.filter(e => e.seq <= seq))`：轨迹用现成 `foldEvents`；Cockpit 的步数/上下文/花费用现成 `runView` 折叠（前端已有 `selectRunView` 系列，传切片后的 view——这里要给 `runView.ts` 补一个「从事件切片折出 view」的纯函数 `viewAtSeq(events, seq)`，与后端 `folds/*` 同规则，用后端快照测试钉住等价性）。
  - 正在跑的 run：一旦用户拖离最新，进入「冻结」态，新事件继续入本地数组但不推进显示；「回到最新」（Live）一键恢复。结束的 run 默认停在最后一个刻度。
  - 深链：`?run=<id>&seq=<n>` 打开即定位，方便在 PR / 消息里贴「看第 7 步」。

**实施记录（2026-09-09，Task 1）**：前端不再自己折 `viewAtSeq`。改为后端 `GET /ai-library/runs/{id}/view-at?seq=N` 直接调 `run_projection.replay(events[:seq])`——一个折叠注册表，不复制到 TS；刮擦条每个刻度请求一次（step 数量级，不缓存）。`upto_seq` 是含上界。

## 2. Fork（只做 issue 的 run）

**语义**：人的「从这一步重来」。原 run、原会话一字不改；新 run 在同一 issue 下、以 `events[:at_seq]` 重建的上下文起跑。

### 2.1 端点

`POST /ai-library/runs/{run_id}/fork` `{ at_seq: int, steer?: str }` → `201 { run_id, session_id, workflow_id, forked_from: {run_id, at_seq} }`

前置条件（顺序即检查顺序，全部类型化 4xx）：

| 条件 | 失败 |
|---|---|
| run 可见且 `agent_runs.issue_id` 非空 | 404 / 409 `not_an_issue_run` |
| issue 没有活着的根 run（复用 2a 的 `running_root_run_id`；读失败 503 `run_state_unavailable`） | 409 `run_live` |
| `at_seq` 是该 run 的一个 step 边界（`step_start.seq`）或 `turn_end.seq` | 400 `not_a_step_boundary` |
| issue 未隐藏、未终态（done/cancelled/closed 不许分叉——要先 reopen） | 409 `issue_terminal` |

### 2.2 事件 → 消息重建（新模块 `runner/replay.py`）

`messages_from_events(events: list[Event]) -> list[Message]`，纯函数，单测钉每种事件：

| 事件 | 产出 |
|---|---|
| `user` | `{role:user, content}` |
| `assistant` | `{role:assistant, content, tool_calls?}`（tool_calls 从同 step 的 `tool_call` 事件回填 id/args） |
| `tool_call` | `{role:tool, tool_call_id, content: json(result)}`（超时/错误结果原样保留——分叉后的模型也该看到它） |
| `compaction_summary` | 用摘要**替换**它之前的所有消息（与运行时压缩语义一致） |
| `question_asked` | 保留为 assistant 的 AskUser 调用；若切点恰在提问处，fork 等于「不回答、从提问前重来」 |
| `inbox_claimed` / `step_*` / `budget_check` / `llm_retry` / `turn_end` / `system` | 不产消息 |

切点在 step 边界，所以最后一条消息永远是完整的（不会把半个 tool 调用切开）。

### 2.3 落地流程（`services/issues/issue_fork.py`，DBOS 之外的一次事务 + 一次派发）

1. 新建会话（`conversations`/`conversation_ai_meta`，与 `issue_session.ensure_session` 同一建法），把重建消息按顺序写成 `ai_messages`，每行 `metadata_json.forked_from = {run_id, seq}`。
2. `issues.ai_session_id` 指向新会话；`execution_state.forked_from = {run_id, at_seq, steer: bool}`；清 `paused_at`、`awaiting_input`（分叉是明确的人为「从这里走」）。原 run 若停在提问上（`awaiting_input.question_id` 属于它），标 `answered{superseded:true}` 并落 `question_answered{superseded:true}`。
3. 若带 `steer`：写一条收件箱 `kind=steer`（现有 `agent_run_inbox` 路径），第一步 `InboxClaimHook` 领取——不另造注入通道。
4. 派发：`_start_execute_issue(issue_id)`（2a 的同一入口）；新 run 行落地时写 `fork_of_run_id / fork_at_seq`（executor 从 `execution_state.forked_from` 读，先把它 merge 成 `null` 再起 turn），并在 seq 1 之前 emit `fork{of_run_id, at_seq, steer: bool}`。
5. 响应带 `workflow_id` 与新 `session_id`；前端据此切详情页。

> **实施记录（Task 2，2026-09-09）**
> - 印记形状是 dict `forked_from = {run_id, at_seq, steer}`（不是原文的标量 `forked_from_run_id`）：`at_seq` 要落进 `agent_runs.fork_at_seq`，标量放不下。executor 读到后先 `merge_execution_state(issue_id, {"forked_from": null})` 再起 turn——merge 写法只能置 null 不能删键，读方一律 `.get("forked_from") or None`。畸形印记记 warning 当普通 run 跑，不炸。
> - `compaction_summary` 事件从本 Task 起带 `summary` 文本（仅 LLM 摘要被接受的那条；emergency-cap 行仍只有指标）。`replay.messages_from_events` 遇到没有文本的压缩行**保留**已有消息而不是清空——旧 run 与紧急截断都不能让分叉起点空白。
> - run 的 transcript 只有**本轮**的 `user`（最后一条用户文本）与终态 `assistant`；此前各轮在原会话的 `ai_messages` 里。所以 Task 3 建新会话时先复制原会话中早于该 run 的消息，再叠 `messages_from_events(events_upto(events, at_seq))`；run 内的工具往返不重放——在第 N 步分叉 ≈ 带 steer 重跑这一轮，这是 §1「重建不含工具消息」的直接后果。
> - `events_upto(events, at_seq)` 按 **seq** 切片而非列表下标，Task 3 只许用它。
> - **steer 不走 inbox**（偏离 §2 第 3 条）：fork 时 run 尚未开始，inbox 注入会叠在一份已经含它的历史上；改为印记带 `steer_text`，executor 把它当作分叉那一轮的**用户消息**（`run_session_turn(content=steer_text)`，进 `ai_messages` 可见、进上下文一次），没有 steer 就发既有的 `CONTINUATION_NUDGE`——分叉 run 绝不重发完整任务文本，历史里已经有了。`fork{steer: bool}` 事件不变。

失败回滚：步骤 1–2 在同一事务；派发失败则把 `ai_session_id` 指回原会话并 503 `dispatch_failed`（与 2a `/resume` 失败恢复同款）。

### 2.4 事件与折叠

- 新事件类型 `fork`（mig 460 放行）：`{of_run_id, at_seq, steer}`。
- `folds/fork.py`：`view.fork = {of_run_id, at_seq}`（只在分叉 run 上有）。原 run 侧不落事件——它已结束、不可写；「分叉点」由前端反查得来：`GET /ai-library/runs/{id}/forks` → `[{run_id, at_seq, created_at}]`（一个 `WHERE fork_of_run_id = :id` 的读）。

**实施记录（2026-09-09，Task 1 勘察）**：§2.2 的 `tool_call → tool 消息`不做——`build_history_messages` 只保留 user / assistant / system，tool 结果本来就不在跨轮上下文里，重建只做 user / assistant / compaction_summary（compaction 替换其前全部）。§2.4 的 `fork` 事件已由迁移 460 放行。

## 3. 逐工具超时

**原则**：超时是**工具结果**，不是 run 结果。模型看到、run 继续；`timed_out` 与 `error` 各自独立上报（防御模式「正交的结果各自独立上报」）。

- 时限表 `runner/tool_timeouts.py`：

  | 工具 | 默认秒 |
  |---|---|
  | Skill | 30 |
  | FinishIssue / AskUser | 10 |
  | ResourceFetch | 60 |
  | GenerateMedia 系 | 600 |
  | Delegate（await=true） | 900 |
  | MCP 工具（`skill.*` / `agent.*` 外的所有远端） | 120 |
  | 未列出的 | 60 |

  `config.yml` `TOOL_TIMEOUTS: {<tool name>: <seconds>}` 逐项覆盖（`settings` 读一次，`resolve_timeout(name)`）。
- 执行：`runner/tool_exec.py::run_tool_with_timeout(name, coro)` 是**唯一**包法（源码守卫：runner 里工具 handler 的 `await` 只能经它）：`asyncio.wait_for`；超时后 `task.cancel()` 并 `await` 到静止（Dispose 纪律）；子进程型工具在 `CancelledError` 里走 `kill_tree`。结果：`{error:"timeout", timed_out:true, timeout_s, elapsed_s}`；正常错误 `timed_out:false`。
- 事件：不加类型。`tool_call.payload.result` 原样带 `timed_out`；`folds/tools.py` 折出 `view.tools = {timed_out: n, last_timed_out: name}`。
- 两条循环各接一次；穷尽守卫测试同 step hooks：给 `run_turn` / `stream_turn` 各喂一个永不返回的假工具，断言两边都在 `timeout_s` 内拿到 `timed_out:true` 且 turn 继续到下一步。
- 模型侧提示：工具错误消息正文 `Tool <name> timed out after <n>s. Retry once with a narrower request, or choose another way.`（进 `prompts/README.md` 的 What the model sees）。

> **实施记录（Task 4，2026-09-09）**
> - **AskUser / FinishIssue 不计时**（偏离上表的 10s）：它们只写一行 transcript 就返回；10s 截断在「行已写、结果没回」的窗口里会留下 `view.question` 已设而 runner 没看见 `asked` 的悬空问题（异步状态不是同步状态）。`config.yml TOOL_TIMEOUTS` 显式给它们数值仍可强制计时。
> - **ResourceFetch 200s**（偏离 60s）：工具自己的抽帧总截止是 180s（`FRAMES_TOTAL_DEADLINE_SECONDS`），60s 会让它那条优雅的「frame extraction timed out」结果永远不可达并把 ffmpeg 腰斩。
> - `skill.*` 按 Skill（30s）、`agent.*` 按 Delegate（900s），其余带点的 MCP 名 120s。
> - **handler 自己的异常原样抛出**（不转成 `{error, timed_out:false}`）：包法只管墙钟，各调用点既有的类型化 `except`（ResourceFetch / MCP 传输）先于任何通用兜底；内层 `wait_for` 抛的 `TimeoutError` 因此是 handler 的错，不是我们的超时（用 `asyncio.wait` 而不是 `wait_for` 判定，避免被它冒充）。
> - handler 吞掉 CancelledError 晚返回的值丢弃、仍报超时；取消后的清理等待有上限 `CLEANUP_GRACE_S = 5s`（超过记 error 并放手，不让一个卡死的清理拖住整轮）；run 自身被取消时同样先给 handler 这段宽限再传播。
> - 超时结果多带 `message` 字段承载上面那句模型侧提示（同一条工具消息，不另起一条）。

## 4. UI（三页稿，用户验）

1. **回放刮擦条**（issue 详情 → 轨迹区顶部）：一条水平轨道，刻度 = step，当前刻度高亮并标 `step 7 / 12 · turn 2`；右侧 `Live` 胶囊（正在跑且未拖离时亮）；拖离后轨迹只显示到该 step，Cockpit 四格显示该时刻的值并加灰色角标「as of step 7」。键盘 ←/→。
2. **从这里分叉**：刮擦到某一步后，轨道下出现 `Fork from step 7` 按钮 → 小弹窗：一句说明「A new run will start from this step with the same context. The current run is left as is.」+ 可选 steer 输入框 + `Fork` / `Cancel`。成功后详情页切到新 run，轨迹头部芯片 `Forked from run #… @ step 7`（可点，回到原 run 并自动刮到 step 7）；原 run 轨迹在 step 7 节点右侧出 `Fork →` 小标（可点）。issue 时间线一条系统行「Forked from step 7」。
3. **超时**：轨迹里超时的工具行：名字后 `Timed out · 60s` 红字徽标（tone danger），展开可见 `elapsed 60.0s`；Cockpit 第五格 `Tools` 显示 `1 timed out`（有则显示，无则该格不出现）；Reason 行不出现（run 没停）。

配色沿用语义 token（回放/分叉用 info，超时用 danger）。

**实施记录（2026-09-09，Task 1 勘察）**：第 2 页的「issue 时间线一条系统行」不写——`issue_messages.kind='system_status'` 行由 DB trigger 写、无 body。时间线入口 = 分叉 run 自己的气泡头部 `Forked from run #… @ step N` 芯片（可点回原 run 并自动刮到该 step）。

## 5. 数据与端点汇总

| 项 | 变更 |
|---|---|
| mig 460 | 白名单加 `fork` |
| `GET /ai-library/runs/{id}/events` | `+upto_seq` |
| `POST /ai-library/runs/{id}/fork` | 新 |
| `GET /ai-library/runs/{id}/forks` | 新（只读） |
| `config.yml` | `TOOL_TIMEOUTS` |
| `run.view` | `+fork{of_run_id, at_seq}`、`+tools{timed_out, last_timed_out}` |

## 6. 测试与验收

- 单测：`replay.messages_from_events` 逐事件类型 + 压缩替换 + 切点在提问处；fork 端点四个前置条件各一红；`tool_exec` 超时/取消/子进程清理；两条循环穷尽守卫；`folds/fork`、`folds/tools`；前端 `viewAtSeq` 与后端折叠快照等价、`ReplayScrubber` 刻度只在 step 边界、冻结/Live、深链；fork 弹窗与芯片；超时徽标与 Cockpit 格。
- 突变：`messages_from_events` 不做压缩替换 → 红；fork 端点去掉 `run_live` 检查 → 红；`run_tool_with_timeout` 不 `cancel()` → 红；折叠不区分 `timed_out` 与 `error` → 红。
- 真栈：对 2a 留下的 MH-67（多步 run）刮擦到 step 2 分叉并带 steer → 新 run `fork` 事件、`inbox_claimed{steer}`、原 run 不变、`/forks` 列出；超时：`config.yml` 把 `ResourceFetch` 设 15s、让 agent 抓 `httpbin.org/delay/30` → `tool_call.result.timed_out=true`、run 继续、Cockpit 出 `1 timed out`（验收后把配置改回）。
- 前端：`npm run e2e:prod` + 三页对照截图。

## 7. 明确不做

聊天会话的分叉；fork 正在跑的 run（要先 pause/cancel）；跨 issue fork；回放的服务端快照缓存；工具超时后自动重试；per-agent 时限覆盖（config 全局即可）。

## 8. 纪律（继承 2a §8）

每 Task 独立 worktree + PR；TDD + 突变记录；对抗评审（opus）全修；CI 绿即合并并盯两条部署链；偏离即回写本文与 plan 的实施记录。**给 run_turn 结果或工具结果加任何新标志，必须用「adapter 无 `stream`」用例证明穿过缓冲回退分支**（2a Task 9 教训，CLAUDE.md 已知陷阱）。

## 9. Hand-off

1. 三页 UI 稿发到画板给用户验（只验 UI）。
2. 认可后 `superpowers:writing-plans`，Task 粗切：T1 mig 460 + `upto_seq` + spec 回写 → T2 `replay.messages_from_events` + fork 端点 + 会话切换 + `fork` 事件/折叠 + `/forks` → T3 逐工具超时（时限表、`tool_exec`、两处循环、折叠、prompt README）→ T4 回放刮擦条 + `viewAtSeq` → T5 fork 弹窗/芯片/时间线 → T6 超时徽标 + Cockpit 格 → T7 真栈验收 + 完成账。
3. 开工前复核 §0（另一会话仍在合并，`IssueChatThread` / `runView.ts` 可能又变）。
