# harness 借鉴三期设计：类型化提问 · 逐工具超时 · 消息反馈（2026-08-26）

> 来源：对 deepseek-harness 46 个子系统的第二轮全量对撞（一二期只覆盖约 10 个）。
> 三件都有**实测缺口**且成本 S–M。沿用铁律：重写不复制；每项开工先复核本 spec §0。

## 0. 现状与证据（写作时实测；执行时复核）

| 事实 | 证据 |
|---|---|
| `FinishIssue` 工具 schema 只有 `outcome`（enum completed/needs_input/continue）与 `reason`（一句话）；needs_input 时 `route_finish_outcome(issue_id, outcome, reason, *, auto_close, set_status, content_len, run_id)` 把 reason 写进 `execution_state.outcome_reason` | `finish_issue_tool.py:26-80`、`issue_lifecycle.py:642-716` |
| 前端 `NeedsInputCard({question, agentName, onSubmit(body)})` 只渲染一段文本 + 自由输入框；用户手打回答，回答以 issue reply 正文回流 | `NeedsInputCard.tsx:25-29` |
| dsh `AskUserQuestion`：`options[{label, description?}]` + 可选 `intent` 决策类型；**答案编码 = 被选 label**；不认识的 intent 退化为通用选项列表 | dsh `docs/subsystems/user-questions.md` |
| runner 只有 run 级 `timeout_sec`（`agent_runner.py:57` 自承 "the per-run wall-clock deadline is the real bound"）；工具执行散在 6 处：`skill_tool.execute`（:731/:1400）、`delegate_tool.execute`（:799/:1488）、`_dispatch_finish_issue`、`_dispatch_screenwriting`、MCP、ResourceFetch；**无逐工具超时**，一个挂住的工具吃掉整轮 | `agent_runner.py` |
| `race_until_abort(coro, abort)` 已是 adapter 调用的 abort 熔合面；`AbortController` 基于 asyncio.Event | `abort_controller.py:53-95` |
| dsh timeout-policy：工具在**自己的定义**上声明 `timeoutMs`，一个 around-dispatch 监听器统一执行，deadline 与调用方 abort 熔合成一个 signal，超时返回结构化 `TOOL_TIMEOUT` | dsh `packages/guard/timeout-policy/README.md` |
| `public.messages(id bigint, conversation_id bigint, seq, sender_type, from_agent_id uuid, type, body jsonb, …)`；agent 消息 465 行；**无任何消息级反馈**（grep 命中的 feedback 字样都在发布页，无关） | 生产库 information_schema；前端 grep |
| `AIChatBubble` props 无 messageId（role/content/agentName/tokens/onApply/onCopy/attachments/toolCalls/awaitingApproval） | `AIChatBubble.tsx:21-43` |
| 迁移号：master 最大 442；443/444 全分支空闲 | `git ls-tree` 全分支扫描 |
| **spill 不做**：30 天 tool_call 事件仅 8 条、截断 0 次 | `agent_run_transcript_events` 30 天统计 |

## 1. 类型化提问（dsh user-questions → nous needs_input）

### 1.1 模型侧契约

`FinishIssue` schema 增可选字段：

```json
"options": {
  "type": "array", "maxItems": 6,
  "items": {"type": "object",
    "properties": {"label": {"type": "string", "maxLength": 80},
                   "description": {"type": "string", "maxLength": 200}},
    "required": ["label"]},
  "description": "Only with outcome=needs_input: the concrete choices you need the human to pick from. Omit when the question is open-ended."
}
```

description 里明确：**只在 needs_input 时有意义**；开放式问题就不给 options。

### 1.2 服务端落库

`route_finish_outcome` 增 `options: Optional[list[dict]] = None`；needs_input 分支把**校验后的**选项写进 `execution_state.question_options`（jsonb 已有，无需迁移）：

- 校验：list、≤6 项、每项 label 非空且 ≤80 字、description ≤200；**label 去重**（重复 label 会让"答案 = label"歧义）；不合规整体丢弃 + warning（不让一次坏输出卡住 turn）。
- 非 needs_input 携带 options → 忽略 + warning（不入库）。
- 与既有 `outcome_reason` 并存：reason 是问题正文，options 是候选答案。

### 1.3 前端与回答编码

`NeedsInputCard` 增 `options?: {label, description?}[]`：有则渲染按钮列（label 主文案、description 次级），点击 → `onSubmit(label)`（**答案编码 = label 原文**，与 dsh 一致，后端零改动——回复正文就是 label，agent 下一轮读到的是它自己给出的选项文字）。保留自由输入框（用户可以不选）。
`issueChips.ts` 的 needs_input 芯片文案：有 options 时显示 "Pick one of N"。

### 1.4 非目标

dsh 的 `intent` 决策类型（如 confirm/choose-model）不搬——本仓无 UI 会特殊渲染；通用选项列表即全部。

## 2. 逐工具超时（dsh timeout-policy）

### 2.1 声明在工具自身

每个工具 spec（`_skill_tool_spec` / `_delegate_tool_spec` / `screenwriting_tool_specs` / MCP descriptor / ResourceFetch）可带 `"x-nous-timeout-s": <float>`（放在 function 对象上的扩展键，**不进** provider 请求体——`_build_tools` 出口剥掉所有 `x-nous-*`）。默认值表（无声明即用）：

| 工具 | 默认 |
|---|---|
| Skill | 30s（读 DB 正文，快） |
| ResourceFetch | 120s（可能抽帧/读大文档） |
| Delegate | 无（子代理有自己的 run 级预算；本层不重复计时） |
| MCP | 沿用 `DEFAULT_TIMEOUT_SECONDS=30`（httpx 层已有，本层不再叠加） |
| screenwriting 写工具 | 60s |
| FinishIssue | 10s |

### 2.2 单一执行点

新增 `AgentRunner._execute_bounded(tool_name, coro, *, timeout_s, abort)`：
`asyncio.wait_for(race_until_abort(coro, abort), timeout_s)`——deadline 与 abort **熔合**（abort 先到走既有 RunAborted 路径，超时到返回结构化结果）。六处执行点统一改经它。

超时的**结构化结果**（不是异常、不终止 turn）：

```json
{"error": "tool timed out", "tool": "<name>", "timeout_s": 30, "code": "TOOL_TIMEOUT"}
```

模型看到它可以换策略；transcript 落 `tool_call` 事件时 payload 含 `code`。run 级 `timeout_sec` 保持不变，仍是总闸。

### 2.3 非目标

不改 MCP 客户端的 httpx 超时；不给 Delegate 加层（避免双重计时）。

## 3. 消息级反馈（dsh message-feedback）

### 3.1 数据

migration 443：

```sql
CREATE TABLE IF NOT EXISTS public.message_feedback (
  message_id  bigint NOT NULL REFERENCES public.messages(id) ON DELETE CASCADE,
  user_id     uuid   NOT NULL,
  rating      text   NOT NULL CHECK (rating IN ('positive','negative')),
  note        text,
  created_at  timestamptz NOT NULL DEFAULT now(),
  updated_at  timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (message_id, user_id)
);
CREATE INDEX IF NOT EXISTS idx_message_feedback_message ON public.message_feedback(message_id);
```

一人一条（主键），可改可删（dsh 的"可编辑、与不可变会话事件分离"）；不搬 dsh 的 CAS 版本 token——单用户单条、最后写入胜出即可，冲突场景不存在。RLS：与 `messages` 同口径（能读该会话即可写自己的反馈）——执行时对照 `messages` 现有策略复制。

### 3.2 API

- `PUT /api/v1/conversations/{conversation_id}/messages/{message_id}/feedback` body `{rating, note?}` → upsert，返回当前值
- `DELETE …/feedback` → 删自己的
- `GET /api/v1/conversations/{conversation_id}/messages` 既有列表响应给 assistant 消息补 `my_feedback: {rating, note} | null`（一次 LEFT JOIN，避免 N+1）
- 只允许对 `sender_type='agent'` 的消息评价；对用户消息评价 → 422。

### 3.3 前端

`AIChatBubble` 增 `messageId?: string` 与 `feedback?: {rating, note} | null` + `onFeedback?(rating|null)`；assistant 气泡 hover 区两个图标钮（lucide `ThumbsUp`/`ThumbsDown`，无 emoji），已选态高亮，再点取消。乐观更新，失败回滚 + toast。

### 3.4 非目标

不做"反馈 → 训练/评估"消费（只落数据）；不做 issue_messages 的反馈（那是评论流，不是模型输出）。

## 4. 验收总则

- 每项突变复做，写进 commit。
- 边界 mock 用真实 wire 形状（FinishIssue 的 tool_call arguments 是 JSON 字符串；messages 行的 id 是 **number**）。
- 合并后真栈：(1) 让 agent 在 issue 上问一个带 3 个选项的问题，点选后 agent 下一轮收到 label；(2) 用一个 sleep 60s 的假 MCP/工具触发超时，turn 不死且模型收到 TOOL_TIMEOUT；(3) 聊天里点踩一条，`message_feedback` 出现一行。
