# needs_input 一等状态 + 静默失败类型化契约 设计（agent 路线图 2️⃣+4️⃣）

> 源自 2026-07-27 multica 对比分析定下的四项提升路线（执行顺序 2→4→1→3）。
> 本 spec 覆盖 2️⃣（needs_input 一等状态）与 4️⃣（静默失败类型化契约）——
> 路线图标注 4️⃣ "小，可与 2 同 session"。

## 背景与问题

影视 agent 问人频率远高于编程 agent（审美决策多）。现状链路逐层盘点：

| 层 | 现状 | 结论 |
|----|------|------|
| 声明 | FinishIssue tool 已有 `needs_input` outcome，落 `issues.status=needs_followup` + `agent_outcome` + `outcome_reason` | ✅ 已有 |
| 恢复 | 用户在 issue 里回复 → `respond_to_issue_reply` workflow → 同 session 完整上下文跑一回合 | ✅ 已有（但是"终结旧 workflow + 新起 workflow"模型） |
| 挂起 | `execute_issue` 的 dispatch loop 在 needs_input 处**直接终结** | ❌ 本次核心 |
| 呈现 | `task_tracking` phase 无等待态；`agent_runs.RunStatus` 无 waiting；`inbox_notifications` 是封闭三 kind；Task Center 里 agent 提问后只剩一条"已完成"的 run | ❌ 用户不知道 agent 在等自己 |
| 附件失败回显 | 后端 `attachment_failures` 一路返回到 API 响应（`ai_library_router.py:2454`），**前端零消费**；issue 回复链路整个丢弃 | ❌ 4️⃣ 的主修项 |

反面教材（multica）：声明了 `agent_blocked` 通知类型却从没写入，agent 卡住只发可静音的
info 级通知。本设计的验收线是：**agent 提问后，用户在 30 秒内能从任一常驻界面看到并一键跳到回复框**。

## 设计决策

### D1 恢复模型：DBOS recv 真挂起 + 旧路径兜底（路线图既定选择）

`execute_issue` 的 continuation loop 在 needs_input 处不再终结，改为
`DBOS.recv_async(topic, timeout)` 挂起等用户回复；回复通过 `DBOS.send_async` 唤醒原
workflow **原地继续**（continuation 计数、issue 快照、run 语境全部延续）。这是
multica 想做都做不到的原语，DBOS 白送（`recv_async`/`send_async` 已确认存在于
dbos 1.x，`approval_gate.py` 已用同族 API 验证过 pause-resume 可行性）。

**旧路径不删**：`respond_to_issue_reply`（终结后回复重启）保留为兜底。挂起超时、
worker 换版本后挂起丢失、send 竞态失败——所有失败形态都退化为今天的行为，不会更糟。

### D2 等待态的落位：业务装饰字段，不动 phase 状态机（路线 C 合规）

CLAUDE.md 路线 C 纪律：`task_tracking.phase/status` 由 `mirror_dbos_lifecycle_to_tracking`
trigger 全权同步，业务代码禁止 PATCH。挂起期间 DBOS 视角 workflow 是 PENDING（活着，
阻塞在 recv）——**phase=in_progress 是诚实的**，不该伪造一个引擎不知道的 phase。

等待态作为**业务装饰字段**写进 `task_tracking.metadata`（jsonb，纪律第 3 条明确允许）：

```json
metadata.awaiting_input = {
  "prompt": "<agent 的提问，截断 500 字>",
  "since": "<ISO8601>",
  "issue_id": 123
}
```

进入等待时 PATCH 上去，被唤醒/超时/清理时移除。前端据此渲染，不引入新 phase、
不碰 trigger、不需要状态机 migration。

### D3 醒目化双通道：Task Center 高亮 + inbox 通知

1. **Task Center**（已实时监听 `task_tracking`）：`metadata.awaiting_input` 存在的行
   渲染为橙色 "Waiting for your input" 高亮行，副标题显示 agent 的提问，点击深链到
   issue 聊天页。状态条计数器加 waiting 数。
2. **inbox_notifications**：CHECK 约束扩一个 kind `agent_question`（migration），
   进入 needs_input 时写一行（title=agent 提问摘要，link_kind=issue）。InboxPanel
   已有的 realtime + 深链解析直接吃到。

Issues 页的橙色 Needs Follow-up 状态维持不变——挂起期间 issue 状态仍走
`needs_followup` + `agent_outcome=needs_input`，对既有 UI 零破坏。

### D4 回复入口：复用 issue 聊天框，不在 Task Center 造输入框（YAGNI）

Task Center 高亮行与 inbox 通知都只做**深链**到 issue 聊天页。回复框、附件、
@提及一套都是现成的。

### D5 4️⃣ 静默失败：附件失败回显 + 盘点 + 纪律入档

multica `admission.go` 原则："业务对象写入成功"≠"agent 被触发"，每条触发路径必须
返回类型化结果，silent no-op is never acceptable。本次落三件事：

1. **`attachment_failures` 回显**：chat 前端消费 API 已返回的字段，在 assistant
   消息气泡上方渲染 per-file 失败条（"3 个附件中 1 个解析失败：xxx.pdf — 超过
   10MB 上限"）；issue 回复链路把 failures 写进 assistant 消息的 metadata 随
   `publish_message` 带出，issue 聊天 UI 同样渲染。
2. **静默分支盘点**："用户动作 → agent 触发"各路径逐条过（issue dispatch、issue
   reply、chat、scheduled、pipeline relay、subissue barrier），每条列出静默 no-op
   分支，小的当场修（返回类型化结果/写日志+通知），大的记 issue。
3. **纪律入档**：CLAUDE.md 增加与"DBOS 失败必须 raise"并列的条目："agent 触发
   路径禁止静默 no-op——每条路径必须返回类型化结果或写 inbox 通知"。

附带安全项：审计 issue reply/评论内容嵌入 prompt 的转义与信任边界（multica 教训：
多行评论可越出 blockquote 变成顶级 prompt 文本）。审计结论记录在实施 PR 描述里；
发现实际漏洞则当场修。

## 架构

### 新组件

| 组件 | 路径 | 职责 |
|------|------|------|
| `input_gate.py` | `backend/app/agent_framework/input_gate.py` | `approval_gate` 的同族兄弟：`await_user_input_in_workflow(issue_id, ttl)` 包 `DBOS.recv_async`；`signal_user_reply(workflow_id, issue_id, payload)` 包 `DBOS.send_async`。topic = `needs_input:<issue_id>` |
| waiting 标记 helper | 同上或 `issue_lifecycle.py` | 进入/退出等待时 PATCH `task_tracking.metadata.awaiting_input` + 写/清 inbox 通知（均为 @DBOS.step，可重放） |
| 挂起清理 reaper | `backend/app/main.py` 启动钩子 | 见"部署与版本"节 |

### 数据流（挂起-唤醒主路径）

```
agent FinishIssue(needs_input, reason)
  → execute_issue loop:
      set_status(needs_followup, agent_outcome=needs_input)     # 与今天相同
      mark_awaiting_input(task metadata + inbox 通知)            # 新增 @DBOS.step
      payload = await_user_input_in_workflow(issue_id, ttl=72h)  # DBOS.recv_async 挂起
      ├─ 收到回复 payload {reply_text, user_id, attachments}:
      │    clear_awaiting_input()
      │    set_status(in_progress)                # 回到执行态
      │    继续 continuation loop（把回复作为下一回合输入，
      │    经由现有 run_issue_reply_step 的同款 turn 路径）
      │    → agent 可再次 needs_input（有轮数上限）或 completed/continue
      └─ 超时（72h）:
           clear_awaiting_input()（inbox 通知保留，等待标记移除）
           workflow 正常返回 —— issue 停在 needs_followup，
           与今天的终态完全一致；此后回复走旧路径
```

### 唤醒路径（reply endpoint 改造）

`issue_messages_router` 的回复入口，在现有 `_dispatch_respond_to_issue_reply` 之前加分流：

```
用户回复 issue
  → 读 issue.dbos_workflow_id + task_tracking.metadata.awaiting_input
  ├─ 标记存在:
  │    signal_user_reply(workflow_id, issue_id, payload)   # DBOS.send_async
  │    send 后复查 workflow 状态：
  │    ├─ 非终态 → 完成（原 workflow 会消费）
  │    └─ 终态（send 与超时竞态，消息被 DBOS 丢弃）
  │         → 兜底 dispatch respond_to_issue_reply
  └─ 无标记 → 走今天的 respond_to_issue_reply（行为不变）
```

回复消息本身照常先落库（issue 消息表）——即使唤醒链路全灭，消息不丢，用户重发
或旧路径都能接住。

### 回合语义

- needs_input 的等待**不消耗** continuation 计数（cap 只约束 `continue` outcome）。
- 每次 dispatch 的**等待轮数上限 5**（防 agent 无限问人把 workflow 挂成常驻）；
  超限后按 needs_input 终结（今天的行为）。
- 挂起期间用户把 issue 拖到 cancelled/done/closed：唤醒后 loop 顶部现有的
  `PREEMPT_STATUSES` 复查会接住并让路——不需要新代码，但要有测试钉住。
- 回复回合与 `respond_to_issue_reply` 共用 per-issue turn lock 语义：原 workflow
  被唤醒后跑回合前先 acquire，同款有界等待。

### 部署与版本（挂起的生存性）

DBOS PENDING workflow 绑定 app_version；部署换版本后旧版本的挂起 workflow 不会被
新 worker 恢复——挂起会"假活"（状态 PENDING 但永远无人执行），send 过去的消息
也没人消费。对策：

1. **启动 reaper**（复用 `_bg_reap_internal_queue` 模式）：扫
   `task_tracking.metadata.awaiting_input` 非空且 workflow app_version ≠ 当前版本的
   行 → 清标记 + `DBOS.cancel` 该 workflow → issue 留在 needs_followup，回复自动
   走旧路径。用户视角无感（Task Center 高亮消失，inbox 通知还在，点进去照常回复）。
2. 唤醒路径的"send 后复查状态"对 CANCELLED 同样生效，兜底 dispatch。

gpupc 发版频繁，这条不是边角而是主场景——**挂起的价值在发版间隔内成立，跨发版
自动降级为旧模型**，两代模型共存是设计而非妥协。

## 错误处理与边界

| 场景 | 行为 |
|------|------|
| recv 超时 | 清标记、正常返回，issue 停 needs_followup（=今天） |
| send 与超时竞态 | send 后复查 workflow 终态 → 兜底 dispatch；极端窗口消息落库不丢，用户可重发 |
| 挂起中 worker 崩溃重启（同版本） | DBOS 从 recv checkpoint 重放，继续等（原语保证） |
| 挂起中发版换版本 | 启动 reaper 清标记+cancel，降级旧路径 |
| 挂起中 issue 被人工关闭 | 唤醒后 PREEMPT_STATUSES 复查让路 |
| agent 连环问人 | 每 dispatch 等待轮数 ≤5，超限终结 |
| 标记 PATCH 失败 | 不阻断挂起（等待照常，只是 UI 不高亮）；PATCH 在 @DBOS.step 里有重试 |
| inbox 写失败 | 同上，日志 WARNING，不阻断 |

## 测试策略

- **input_gate 单测**：mock DBOS.recv_async/send_async，验证 topic 构造、payload
  防御性解析（畸形 payload 不炸 workflow）、超时返回形态。
- **continuation loop 单测**：`_run_dispatch_with_continuation` 已是依赖注入可测的，
  扩展注入 fake gate：needs_input→回复→继续、needs_input→超时→终结、等待轮数上限、
  唤醒后 PREEMPT 让路。
- **唤醒分流单测**：有标记走 send、无标记走 dispatch、send 后终态复查走兜底。
- **reaper 单测**：版本不匹配的挂起被清理，匹配的不动。
- **attachment_failures 回显**：前端组件测试（失败条渲染）+ 后端 issue 链路
  metadata 传递测试。
- **E2E**（Claude 调试账号）：真实 dispatch 一个必然问人的 issue → Task Center
  出现高亮行 + inbox 通知 → 回复 → 原 workflow 唤醒完成。

## 明确不做（YAGNI）

- Task Center 内嵌回复输入框——深链到 issue 聊天页够用。
- `agent_runs.RunStatus` 加 waiting 值——run 是回合粒度，回合确实结束了；等待是
  issue/task 粒度的事。
- chat 路径的挂起——chat 本来就是人机交互循环，needs_input 在 chat 里就是一条
  普通回复。
- 新 phase / 状态机 migration——D2 已论证。
- Discord/邮件等站外通知——inbox + Task Center 先验证站内闭环；站外通知是
  独立课题。
- 4️⃣ 盘点中发现的大改动——记 issue 进 backlog，不在本 session 扩 scope。

## Migration 清单

仅一条：`inbox_notifications` 的 kind CHECK 约束扩 `agent_question`
（`DROP CONSTRAINT` + `ADD CONSTRAINT`，无数据变更）。
