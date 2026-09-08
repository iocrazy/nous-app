# harness 第四轮 · 二期 2a：控制面（提问 / 暂停 / 预算追问 / 排队计数）—— 设计 spec（进行中）

> 前序：一期 spec `2026-09-05-harness-p4-task-visibility-control-design.md`（五原语 + 三接缝；本文只做它 §6 表里「2 控制与分叉」的前半，即 **2a**）、一期完成账 `../plans/2026-09-05-harness-p4-phase1.md`、三期 spec `2026-08-26-harness-phase3-typed-interaction-design.md`（其 §1 类型化提问被本文吸收）。
>
> **状态（2026-09-06）**：§0–§2 已与用户过完并认可；§3–§6 用户明确「你来定」，由本文作者拍板并自复核；**用户只验 UI**。下一步见 §9 Hand-off。

## 0. 前提核对（执行前必读；与一期 spec 对二期的假设不一致处）

| 一期假设 | 现状（2026-09-06 实测） | 处置 |
|---|---|---|
| 「预算 100% 停下以类型化提问」接三期的类型化提问 | 三期 spec/plan 存在，**Task 1/2 一行未做**（plan 23 个勾 0 个） | 本文 §1 吸收三期 §1，作为 2a 的第一块 |
| pause 有列有语义 | `agent_runs.pause_requested`、`issues.paused_at`、`conversation_ai_meta.paused_at`、`fork_of_run_id/fork_at_seq` 均在生产库；`TurnEndReason.PAUSED` 已定义；收件箱 CHECK 放行 `pause/resume/budget_reply` | ~~只有壳~~ → **Task 5 已补后端**（PauseHook、`/pause` `/resume`、暂停期间改投、sweeper 跳过已暂停 issue；`issue_repository.transition_status` 到终态时清 `paused_at`）。仍缺：收件箱 API 只放行 `steer/answer/budget_reply`、横条 paused 类恒空、CockpitBlock 只有 cancel（Task 8） |
| 钩子链可 stop / inject | `StepContext.inject()` 已有；`StepHookChain` 首个 STOP 即停；**所有 STOP 一律记 `turn_end{cancelled}`** | 需加 `stop_reason → TurnEndReason` 映射（§2） |
| 挂起/唤醒原语 | needs_input 走 `input_gate`（workflow 原地 `DBOS.recv`，回复经 gateway `DBOSClient.send` 唤醒）；approval_gate 同款 | §1 复用，不再造 |
| 列表级 inbox_pending | issue 列表端点不带 rollup；已有列表级范式 `GET /issues/needs-input` | §4 同范式加 `pending-summary` |
| 聊天路径的「等待人决定」 | `awaiting_approval`：钩子返回 → `turn_end{awaiting_approval}` → assistant 消息 metadata `awaiting_approval` → `ApprovalCard` → approve 后**下一条消息**才续跑 | §1 聊天侧提问完全镜像这条路 |

另一会话（资产引用 v2，#2158/#2159）与本文正交；两边都会碰 `IssueReplyBox` 与聊天面板，但 QuestionCard 放 NeedsInputCard 与气泡，不碰回复框作曲区。

## 1. 提问原语 AskUser（用户已认可）

**是什么**：给 agent 一个「向人提选择题」的动词，做成模型可调用的工具，issue 与聊天两条路都有。此前 issue 只能 `FinishIssue(needs_input, reason)` 让人手打回答，聊天连这条路都没有。

**模型侧**：新工具 `AskUser`：`question`（≤500 字）、`options[]`（1–6 项 `{label ≤80, description? ≤200}`，label 去重）、`allow_free_text`（默认 true）。`FinishIssue(needs_input)` 同样加 `options`（三期 §1.1 原样），两者服务端汇入同一个咽喉点 `ask_question(target, prompt, options, kind="user")`。校验不合规**整体降级为开放式问题 + warning**，不让一次坏输出卡住回合。AskUser 只问不结束 issue；FinishIssue 仍是 issue 回合结局的宣告。

**事件与折叠**：咽喉点发 `question_asked{question_id, kind, prompt, options, allow_free_text}`（新事件类型，迁移放行 CHECK），fold 写 `view.question = {id, kind, prompt, options, asked_at}`，run 以 `turn_end{reason: awaiting_input}` 收尾（`TurnEndReason` 加 `AWAITING_INPUT`，fold 映射到 phase `waiting_input`）。回答落地发 `question_answered{question_id, value, superseded?}` 并清 `view.question`。`question_id`：`q:<run_id>:<seq>`；预算问题 `budget:<run_id>`。

**持久化按目标各接一处**：
- issue：沿用 `issues.execution_state.awaiting_input`，marker 多带 `question_id / options / allow_free_text / kind`；`input_gate` 的挂起原语与 TTL 不动。
- 聊天：assistant 消息 metadata 的 `awaiting_input = {question_id, prompt, options, allow_free_text, kind}`，与 `awaiting_approval` 同位同形。

**回答通道（实施计划作者调整，2026-09-07）**：**不走收件箱**。回答 = 一条带 `answer_to: <question_id>` 的普通消息——issue 侧 `POST /issues/{id}/messages`（评论），聊天侧 `POST` 现有发消息端点。label 校验（`value` 必须等于某个 label，或 `allow_free_text` 时任意文本，否则 400 `answer_shape`）、`question_answered` 落行、唤醒都在消息端点完成。理由：issue 侧唤醒本就走消息端点的 `_try_wake_waiting_workflow`，聊天侧「下一条消息续跑」本就是 `awaiting_approval` 的路；再经收件箱会让同一个答案注入两次。收件箱 `answer` kind 保留原语义（运行中插话），不承担对已挂起问题的回答。issue 侧兼容三期约定：评论正文等于某个 label 即视为回答（消息端点内、`_divert_to_inbox_if_running` 之前先匹配）。模型在唤醒后读到的是它自己给出的选项文字。

**过期**：issue 侧沿用 input_gate TTL，到期 needs_followup；聊天侧无 TTL，下一条用户消息到来即作废旧问题并记 `question_answered{value: null, superseded: true}`。

**前端**：一个 `QuestionCard`（问题、选项按钮、可选自由输入、提交中态、已回答态显示所选），三处复用：NeedsInputCard 内嵌、详情页 cockpit 的 waiting_input 态、聊天气泡（与 ApprovalCard 并列同一挂点）。任务中心 needs-input 区显示「Pick one of N」摘要并跳转。i18n 走 `question.*`。

## 2. 暂停与恢复（issue 目标级；用户已认可，细节委托）

**语义**（一期拍板「pause = cancel keepInbox」）：暂停是目标级状态，不杀 run。`POST /issues/{id}/pause`：写 `issues.paused_at = now()`，并把该 issue 运行中的根 run `pause_requested = true`。runner 新 `PauseHook`（链序：Heartbeat → Cancel → **Pause** → InboxClaim → BudgetGate）在下一个 step 边界读到即 `ctx.stop("paused")`；正在跑的那一步跑完才停，最坏延迟一步。

**收尾**：新增 `stop_reason → TurnEndReason` 映射表（`cancelled → CANCELLED`、`paused → PAUSED`、`awaiting_input → AWAITING_INPUT`；穷尽守卫：`StepContext.stop()` 的 reason 必须在表内，否则测试红）。run 以 `turn_end{paused}` 收尾；issue 状态**保持 in_progress**，phase 由 rollup 的 `paused_at` 判出（优先级 paused 最高，已实现）。dispatch workflow 收到 `stop_reason=paused` 正常结束，不写 blocked / needs_followup。

**暂停期间**：`_divert_to_inbox_if_running` 改为「运行中**或已暂停**都改投收件箱」，评论行照旧保留。带 `answer_to` 的回答不改投：仍走 §1 通道（label 校验、`question_answered` 落行在消息端点完成，且在改投判断之前），唤醒沿既有路径。收件箱 sweeper 对已暂停目标的条目不标 `expired_at`。

**恢复**：`POST /issues/{id}/resume`：清 `paused_at`；有未领条目或上一 run 以 paused 收尾 → 走既有 dispatch 续跑路径（新 run，InboxClaimHook 首步注入全部排队条目 = 「先排空收件箱」）；未暂停且无待领 → 409 `not_paused`。新 run 的 `metadata_json.resumed_from_run_id` 记上一 run（不加列）。

**与 cancel 的边界**：cancel 仍是 run 级、不可逆；暂停中 cancel = 清 `paused_at` + 取消挂起。两者都经 `issue_visibility` 鉴权。

**聊天路径不做**（一期 §8：会话续聊即 resume）；`conversation_ai_meta.paused_at` 留列不用。

**实施记录（2026-09-08，Task 5 落地后与本节的偏差）**：
- 暂停中带 `answer_to` 的回答**照常唤醒**（本节原文成立；一版曾改成 409，对抗评审指出挂起 workflow 的 recv TTL 在暂停期间照样计时，拒答会让长暂停把问题耗死）。唤醒后的那一轮 reply turn 是暂停唯一不拦的 turn（它是新 run 行，pause_requested 到不了它）；它若 `continue`，下一轮在循环里被 `paused_at` 挡住。
- `resumed_from_run_id` 写在 **`issues.execution_state`**（恢复时新 run 的行还不存在，`metadata_json` 无处可写），值是上一 run 以 paused 收尾时的 id，否则 `null`（有待领条目但上一 run 正常结束）。
- `/resume` 的决策表（首个命中）：有活 run 且已暂停 → 撤回 `pause_requested`（`reason=withdrawn`；撤回落空说明 run 已结束，重读后按「没在跑」继续判）；有活 run 未暂停 → `running`，不派发（排队条目在它下一个 step 边界被认领）；`execution_locked_at` 持有但无活 run → 挂起在问题上（`parked`，带原 `dbos_workflow_id`）或 turn 之间（`running`），都不派发第二个 workflow（会输在 `atomic_checkout`，还会把 `dbos_workflow_id` 盖成死的）；有待领或上一 run 以 paused 收尾 → `dispatched`；否则 `cleared`。响应新增 `reason` 字段给 UI 原样展示。
- `running_root_run_id` 读失败不再吞成 None（「空输出不是否定结论」）：仓储 raise，`/pause` `/resume` 回 503 `run_state_unavailable`、什么都不写；评论端点让它冒 500 而不是走唤醒路径。
- `/resume` **先清 `paused_at` 再派发**（新 workflow 第一轮循环顶就读 `paused_at`，晚清会让它立刻自认暂停），派发失败则恢复原值，issue 仍可见为暂停。
- dispatch 循环里多一道 `paused_at` 检查，位置在**开新 turn 的分支**而不是循环顶：一版放在循环顶，评审指出它排在 needs_input 挂起之前，暂停落在「turn 返回 needs_input → 循环顶」之间会把挂起整个丢掉（无 needs_followup、无标记、问题丢失）。
- `request_pause(run_id, *, user_id=None)`：鉴权在目标层（issue visibility），团队成员可暂停别人开的 run，所以不按 run 行的 owner 过滤。
- 「暂停中 cancel = 清 `paused_at`」落在**两个**状态写入方：`issue_repository.transition_status` 与 `issue_lifecycle.set_status`（agent 自己的终态落地走后者，评审指出只改一处会让 done 的 issue 永远显示 paused）；集合 `PAUSE_CLEARING_STATUSES` 一处定义。

## 3. 预算 100% → 类型化追问（作者拍板）

`BudgetGateHook` 在 `pct ≥ 100` 时不再只记事件：调用 `ask_question(kind="budget", prompt="Budget exhausted: spent X¢ of Y¢.", options=[Top up, Wrap up, Cancel], allow_free_text=False)` 并 `ctx.stop("awaiting_input")`。仍只对**有 issue 的根 run**生效（聊天无预算，一期已定）。80% 仍只 warn。

回答后的动作走一个小注册表 `question_kinds`（`kind → on_answer(issue, value)`；`user` 类什么都不做，只注入）：
- **Top up**：前端先弹既有 BudgetBlock 的编辑器改 `budget_cents`（PATCH 已有），成功后再发带 `answer_to` 的回答（§1 通道）；`on_answer` 复核预算，仍 ≤ 已花则 409 `budget_still_exhausted`。然后续跑。
- **Wrap up**：回答落地后由 `on_answer` 往收件箱 `enqueue` 一条 `steer{body: "Budget is exhausted. Finish in one step: summarize what is done and stop."}`（回答本身不进收件箱），续跑；下一回合 BudgetGateHook 对 `kind=budget` 的问题**已回答 wrap-up** 的 run 放行一步（用 `view.question` 的已答记录判断，不加列）。
- **Cancel**：走既有 cancel + issue 状态 `cancelled`。

事件序列：`budget_check{halt}` → `question_asked{kind: budget}` → `turn_end{awaiting_input}`；回答后 `question_answered` → 新 run。一期已落行的 `budget_check{halt}` 语义不变（记录），只是后面多了停下。

**实施记录（2026-09-08，Task 6 落地后与本节的偏差）**：
- Wrap up 的「放行一步」不靠 `view.question` 的已答记录判断（那在旧 run 的视图里，新 run 读不到），而是 `on_answer` 写 `issues.execution_state.budget_wrap_up{run_id, at}`（plan 口径，不加列）；默认 loader 在**下一个** run 首次读预算时把它消费掉——打上 `consumed_by = <本 run id>`，同一 run 的 DBOS 重试仍读到宽限，更晚的 run 读到已消费 → 再次 halt 再次提问。放行的 run 记 `budget_check{action: "wrap_up"}`，折叠成 `view.budget.state = "wrap_up"`。
- Cancel 走 `issue_repository.transition_status(issue, "cancelled")` 再 merge `execution_state.outcome_reason = "budget_exhausted"`（`transition_status` 没有 reason 参数）。
- `on_answer` 对 `target` 无 `id`（聊天路径的 `{"session_id"}` 形状）回 409 `no_issue_target`——预算只对有 issue 的根 run 生效，聊天永远问不出这个 kind，但注册表允许任何 kind 被任何通道调到。
- 提问落行失败（`QuestionNotRecorded`）时 run **仍然停**：无按钮地挂起（旧 needs_input 形态）好过继续烧预算。
- `question_kinds/budget.py` 在 `question.py` 底部 import 完成注册，`registered_kinds() == ["budget", "user"]` 被测试钉死。

## 4. 列表级排队计数与 UI（作者拍板；UI 稿待用户验）

**数据**：`GET /ai-library/inbox/pending-summary?target_kind=issue` → `[{target_id, count, oldest_at}]`，只含调用者可见的 issue（复用 `issue_visibility`），与 `GET /issues/needs-input` 同范式。列表页拿到后并进行 model；不进 issues 列表端点（保持列表查询单表）。

**UI（都由接缝 C 组合，无新框架）**：
- 详情页 **CockpitBlock**：running 显 `Pause`，paused 显 `Resume`（旁边保留 Cancel）；waiting_input 时内嵌 `QuestionCard`；预算问题同一张卡，Top up 按钮先开预算编辑器。
- **StatusBlock**：补显 blocked / empty_output 的原因（`execution_state.error_message` / `outcome_reason`），关掉「后端写了前端不读」那张旧票。
- **主页**：行动作 paused → `Resume`、waiting_input → `Reply`（已有）；行芯片新增 `N queued`（来自 pending-summary）；横条第五类 `queued`（有排队且目标 paused 的 issue），与 `paused` 类并列。
- **任务中心**：issue 型任务卡显示 Paused 态与 Resume；needs-input 区显示「Pick one of N」。
- **看板**：卡底一行阶段芯片 + `N queued` + 悬停动作，受阻卡带原因芯片。
- **聊天气泡**：`QuestionCard` 与 ApprovalCard 同挂点；已回答后按钮变为只读高亮所选。

**UI 稿**：在既有「Issue Workbench」画板（https://claude.ai/code/artifact/f256ccc7-363b-425e-b493-1ed51e44c0f1 ）追加 8 页（暂停/恢复、QuestionCard 三处、预算三选一、主页、聊天、看板、任务中心、Progress 卡原因），页「二期 2a · 控制面」。**用户只验这 8 页**（已于实施计划立项前画出并经用户认可）。

## 5. 数据与接口（迁移先行，单独 PR）

| 变更 | 说明 |
|---|---|
| mig：`agent_run_transcript_events.event_type` CHECK 放行 `question_asked / question_answered` | 与 mig 443/453 同做法 |
| 无新列 | `paused_at / pause_requested` 已在；`resumed_from_run_id` 进 metadata；`question_options` 进既有 jsonb |
| `POST /issues/{id}/pause` / `resume` | 见 §2；409 `not_paused` / `already_paused` |
| `POST /ai-library/inbox` 放行 `kind ∈ {steer, answer, budget_reply}` 不变 | 收件箱不承担对已挂起问题的回答（见 §1 回答通道）：label 校验落在消息端点，消息带 `answer_to`；`budget_reply` 保留给兼容 |
| `POST /issues/{id}/messages` 与聊天发消息端点加 `answer_to: Optional[str]` | §1 回答通道：有值时正文必须等于某 label 或 `allow_free_text`，否则 400 `answer_shape`；`question_id` 与挂起 marker 不符 409 `no_open_question`；匹配则写 `question_answered`（到提问的那个 run）→ `on_answer` → 既有唤醒/续跑 |
| `GET /ai-library/inbox/pending-summary` | §4 |
| `AskUser` 工具 spec 进 `prompt_composer._build_tools()`（两条路都给）；FinishIssue 加 `options` | prompts README 三问同步（模型可见面） |
| `TurnEndReason` 加 `AWAITING_INPUT`；`folds/turn_end.py` 映射到 `waiting_input`；`folds/question.py` 新增 | 投影注册表新条目 |

## 6. 测试与验收

- 每个钩子 / 折叠 / 端点：单测 + 至少一处突变转红（照一期）。
- 穷尽守卫：`stop_reason` 表覆盖 `StepContext.stop()` 全部字面量；`question_kinds` 注册表可枚举。
- 真栈（调试账号）：① issue 派发中 pause → 下一步边界 `turn_end{paused}`、phase paused、评论改投；resume → 新 run 首步 `inbox_claimed` 全部条目；② AskUser 走 issue：`question_asked` 落行、NeedsInputCard 出按钮、点选后 `question_answered` + 续跑读到 label；③ AskUser 走聊天：气泡出按钮、回答后下一回合注入；④ 零预算 issue：`budget_check{halt}` → 三选一卡 → Top up 改预算后续跑 / Wrap up 一步收尾 / Cancel；⑤ pending-summary 与行芯片 `N queued`。
- 前端 `e2e:prod` 走查后人工点一遍 8 页 UI。

## 7. 明确不做

回放 + fork、schedule、continuable 子代理、逐工具超时（→ 2b）；聊天路径 pause；dsh 的 `intent` 决策类型；预算的派发前预演。

## 8. 纪律（继承一期 §7）

合并哪些同形代码先列（`awaiting_approval` 的落法与渲染挂点；`needs_input` 的 marker 与 input_gate；三期 §1 的 options 校验）；新模块唯一入口 `ask_question()`；迁移与代码分 PR；模型可见面改动补 README 三问；突变复做；真栈验收。

## 9. Hand-off（给下一 session / 另一台机器）

1. **先画 UI 稿**（§4 的 8 页）到 Issue Workbench 画板，请用户验；用户只验 UI，不再过技术节。（已完成：2026-09-07 画出并认可，实施计划见 `docs/superpowers/plans/2026-09-07-harness-p4-phase2a-control-plane.md`。）
2. UI 认可后 → `superpowers:writing-plans` 出实施计划（已出，9 Task：T1 迁移 → T2 提问原语后端 → T3 AskUser/issue 挂起 → T4 聊天 → T5 暂停恢复 → T6 预算三选一 → T7 QuestionCard → T8 计数与暂停 UI → T9 真栈验收 + 完成账）。
3. 开工前按一期纪律复核 §0 表（另一会话在密集合并，`IssueReplyBox` / 聊天作曲区可能又变）。
4. 已知与本文相关的旧记忆：`project-issue-page-tracking-redesign-pending`（详情页重设计已由一期 T8 落地，别另立 spec；残余 blocked 原因显示并入本文 §4）。
