# needs_input 一等状态 + 静默失败类型化契约 — 设计

日期:2026-08-01 ｜ 状态:已过用户拍板(入口=Task Center 内联快回;恢复=复用回复链路;范围=合并路线图第 4 项) ｜ 上游:Agent 提升路线图 2️⃣+4️⃣(源自 multica 对比),M4 探针 A1/A2 实证

## 0. 问题

影视 agent 问人频率远高于编程 agent(审美决策多)。现状三缺口:

1. agent 声明 `needs_input` 后 issue 转 `needs_followup`,但 **Task Center 完全不知道**——task_tracking 无对应表达,等人回答的任务在 UI 上和"结束了"无法区分(multica 反面教材:卡住只发可静音通知,从没人看到)。
2. 用户回复后 **状态不自动流转**:issue 停在 needs_followup,回复 turn 约定"不改状态"(Spec-1b),agent 答完了状态还挂着。
3. **静默失败**与 needs_input 是同一主题的两面("agent 停了必须有人知道为什么")。M4 探针实证两个活案例:
   - **A1** `issue_dispatch_auto` 路径 agent_runs 全部 `tokens=0 / cost=NULL`(含有真实模型回复的 run)——按次数的额度守卫不受影响,但成本归因和 `agent_cost_anomaly` 对自动派发全盲。
   - **A2** agent 产出 0 字符且无 outcome 仍记 `completed`、issue 照推 `in_review`——provider 静默失败被洗成"等你审阅";同路径 `liveness_state` 完结后卡 `running`、`agent_runs.issue_id` 不回填。

## 1. 决策记录

| 决策点 | 拍板 | 备选与弃因 |
|--------|------|-----------|
| UX 主入口 | Task Center 置顶分区 + 内联快回 | 只跳转不内联(多一步);全局横幅(打扰过强) |
| 恢复模型 | 复用 `respond_to_issue_reply` 回复链路 + 自动状态流转 | DBOS recv 挂起原地续(更优雅,但改执行模型:长挂 workflow 的超时/重启恢复要整套重设计,与现有 turn-based 分叉——YAGNI) |
| 范围 | 2️⃣+4️⃣ 同批(含 A1/A2/attachment_failures) | 只做 2️⃣(两者同主题,分开做要重进两次同一片代码) |

## 2. 数据流(路线 C 纪律不破)

`task_tracking.phase` 由 DBOS trigger 独管;派单 workflow 是 turn-based、结束即 completed——**不伪造 phase**。Task Center 本就是客户端多源合并(TaskManagerContext 注释明示),增加第二数据源:

- 查询 + Supabase Realtime 订阅 `issues` 表 `status='needs_followup' AND execution_state->>'agent_outcome'='needs_input'`,按当前用户可见范围过滤。
- 行内容:agent 提问原文(`execution_state->>'outcome_reason'`)、issue id/title、项目归属、挂起时刻(updated_at)。
- 后端补一个轻量列表端点(Task Center 首屏拉取用;Realtime 只做增量),走 issues 现有 RLS/权限口径。

## 3. Task Center UI

- 新增置顶分区 **"Needs your answer"**:琥珀 warn 语义色、计数徽章;**不可静音、不可折叠进"已完成"**。
- 卡片展开:agent 问题原文 + 内联输入框(提交=回答)+ "View conversation" 链接跳 issue 详情兜底。
- 提交后卡片进入 pending 态(乐观),Realtime 收到状态流转(needs_followup→in_progress)后从分区移除;agent 再次 needs_input 会作为新行回来。
- 文案全英文走 i18n(en/zh 齐平),遵守 UI 语言规范。

## 4. 恢复链路(核心后端改动)

回答 → 已有 issue 回复端点(`issue_messages_router`)→ `respond_to_issue_reply`(session 上下文完整、per-issue turn 锁防并发)。扩展 Spec-1b:

- **仅当**目标 issue 处于 `needs_followup` 且 `agent_outcome='needs_input'` 时:回复先把状态转回 `in_progress`(走正规 transition_status,`_fire_stage_node_sync` 节点同步自动跟上),再跑续 turn;续 turn 结束按 FinishIssue outcome 正常路由(completed→in_review/done、再次 needs_input→再挂起、continue→按 cap)。**可循环多轮问答。**
- 其它状态的 issue 回复行为零改动(仍然"不改状态")。
- 回复 turn 的 outcome 路由复用 `execute_issue` 的路由表,抽成共享函数,不复制第二份。

## 5. 静默失败类型化契约(4️⃣ + A1/A2)

1. **A2 零产出洗白**:派单/续 turn 产出 0 字符且无 outcome → 不再 completed+in_review;agent_run 记类型化失败(`error_code='EMPTY_OUTPUT'` + error_message),issue 转 `needs_followup`(reason 说明"agent 未产出内容")。用户看到"没干成 + 原因",不是假的"等你审阅"。同 PR 修:run 完结时 `liveness_state` 收尾(不再卡 running)、`agent_runs.issue_id` 派发时回填。
2. **A1 记账盲区**:issue-dispatch 路径(auto 与手动)对齐 chat 路径的 usage 记账点,补写 `prompt/completion/total_tokens`、`cost_cents`。
3. **attachment_failures 回显**:chat 附件失败从只进日志改为随响应回显给用户(2026-07-26 图片修复的遗留项)。
4. **纪律入档**:CLAUDE.md 增条款——"用户动作→agent 触发的每条路径必须返回类型化结果,silent no-op 不可接受",与"DBOS 失败必须 raise"并列;附带盘点现有触发路径的静默分支(盘点结果进实现计划,超出者记债)。

## 6. 不做 / 边界

- 不做 DBOS recv 挂起(见 §1);不动审阅门语义(in_review→done 只有人);不做回答的多人协作/抢答(单人产品);不做 needs_input 超时自动升级(先观察真实频率)。
- A1/A2 触碰 agent_runs 后端,与并行会话的 agent runs UI 工作相邻:实现前先 rebase origin/master,避开 `agent_runs_repository.py` 中已被并行改动的函数;冲突则以 master 为准重排。

## 7. 测试口径

- 状态机往返:needs_input→回答→in_progress→(再 needs_input→再回答)→completed 全链;其它状态回复不改状态(回归 Spec-1b)。
- 零产出:0 字符无 outcome → EMPTY_OUTPUT + needs_followup,断言绝不落 in_review;有产出无 outcome 走原默认路由(回归)。
- 记账:issue-dispatch 路径 mock provider usage,断言 tokens/cost 落库。
- Task Center:分区渲染/计数/提交后移除(vitest);列表端点权限(pytest)。
- E2E(上线后):debug 账号真跑一轮问答闭环。
