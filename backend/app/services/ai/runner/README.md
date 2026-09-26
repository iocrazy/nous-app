# runner — 一轮之内的工具循环与子 agent

`AgentRunner`（`agent_runner.py`）驱动一轮：调模型 → 执行工具 → 把结果追加回消息列表 → 再调模型，直到模型不再调工具或达到上限。`subagent_task_service.py` 与 `../../workforce/agent_worker.py` 在另一个 runner 里跑子 agent，把结果交回父 run。本 README 只写这些模块**自己写给模型的文本**；工具 schema 与收件箱框在 `../prompts/README.md`，超时结果也在那里（`tool_exec.py` / `tool_timeouts.py`），压缩与循环守卫在 `app/agent_framework/README.md`。

- `agent_runner.py` — 工具分派、合成的工具结果、图片提升
- `subagent_task_service.py` — `Skill(skill="task")`：同步 / 并行 / 后台 / 续跑子 agent
- `replay.py` — 从 transcript 重建模型可见历史（续跑与分叉用）
- `../../workforce/agent_worker.py` — workforce 派发的 agent 的请求指令

## Model Experience

### runner 合成的工具结果与注记

#### What the model sees

每次工具调用之前，runner 先追加一条 assistant 消息，`content` 为空串、`tool_calls` 是本批调用：

```json
{"role": "assistant", "content": "", "tool_calls": [...]}
```

每个调用之后追加一条 tool 消息，`content` 是结果对象的 `json.dumps(..., ensure_ascii=False)`：

```json
{"role": "tool", "tool_call_id": "...", "name": "<tool>", "content": "<结果 JSON>"}
```

工具本身没有给出结果时，runner 自己写结果。这些字面量逐字如下（`{…}` 是运行时值，`{Cls}` 是异常类名）：

| 情形 | 结果 JSON 里的文字 |
|---|---|
| 未知工具名 | `{"error": "unknown tool: {name}", "synthetic": true}` |
| 本轮没挂 ResourceFetch | `"error": "ResourceFetch is not available for this turn. Include @resource references in your message to make resources accessible."` |
| ResourceFetch 抛异常 | `"error": "ResourceFetch failed: {Cls}"` |
| 本轮没挂 LibrarySearch | `"error": "LibrarySearch is not available for this turn."` |
| LibrarySearch 抛异常 | `"error": "LibrarySearch failed: {Cls}"` |
| 出图未配置 | `"error": "GenerateImage not configured"` |
| 出视频未配置 | `"error": "GenerateVideo not configured"` |
| MCP 工具传输失败 | `"error": "MCP transport failure: {exc}"`（`{exc}` 是异常的完整字符串） |
| Delegate 未配置（流式路） | `"error": "Delegate tool not configured"` |
| Delegate 未配置（`run_turn` 路） | `"error": "Delegate tool not configured for this run. Cross-agent dispatch requires a DelegateToolService — wiring this run didn't supply one."` |
| FinishIssue 处理器抛异常 | `"error": "FinishIssue failed: {Cls}"` |
| ScheduleWakeup 处理器抛异常 | `"error": "ScheduleWakeup failed: {Cls}"` |
| 写剧本工具：runner 上没装能力门 | `{"ok": false, "error": "{tool} is unavailable on this runner: its capability gate is not installed, so the call cannot be authorized.", "error_code": "capability_gate_missing"}` |
| 写剧本工具：本轮没有 run 记录 | `{"ok": false, "error": "{tool} is unavailable: this turn is not recorded as an agent run, so it carries no script scope.", "error_code": "no_run_context"}` |
| 写剧本工具处理器抛异常 | `{"ok": false, "error": "{tool} failed: {Cls}", "error_code": "tool_error"}` |
| AskUser 停靠后同批排在后面的调用 | `{"error": "not executed: the turn parked on AskUser before this call", "skipped": true}` |

ResourceFetch 返回图片时有两种注记，写在工具结果里替代图片 URL：

```text
[image content delivered as an image part in the following user message]
```

```text
[image omitted: the current model has no vision capability]
```

第一种之后紧跟一条**额外的 user 消息**，先是文字段 `[image content from ResourceFetch]`，再是图片段。

轮次因审批挂起时，runner 往**给用户的答复文本**末尾追加 `\n\n[awaiting approval: {reason}]`；这段文本若被持久化，下一轮会作为 assistant 历史被模型看到。

#### Token effect

一轮最多 `MAX_TOOL_ITERATIONS = 10` 次模型调用（流式路同值）；达到上限时轮次以 `max_tool_iterations_exceeded` 结束，这个标记给调用方，不给模型。每条合成结果只有一行；真正的体积来自工具自己的结果（例如 `Skill()` 正文上限 64k 字符，见 `../skills/README.md`）。一轮之内这些消息**不被压缩**（压缩只在起跑前做一次）。

#### KV Cache effect

**一轮之内 append-only**：每条 tool 消息、每条提升出来的图片 user 消息都接在末尾，不改前面的 token。**跨轮全部丢弃**：聊天历史不重放工具调用与结果（见 `../chat/README.md`），所以下一轮的前缀里没有它们。改这里的字面量只影响本轮之内的后续调用，不影响任何稳定前缀。

### 子 agent / workforce：请求指令、回给父 run 的信封、续跑历史

#### What the model sees

**子 agent 自己看到的**：它的系统消息由 `PromptComposer` 按子 agent 的 slug 组装（骨架见 `../prompts/README.md`），缓存边界之后的请求指令逐字如下。

`Skill(skill="task")` 派生的子 agent（`subagent_task_service.py`）：

```text
You are running as a sub-agent spawned by a parent agent that needs a self-contained answer to the task below. Produce a complete response — the parent only sees your final output, not your intermediate steps.
```

workforce 派发的 agent（`services/workforce/agent_worker.py`）：

```text
You are running as a workforce-dispatched agent. A peer agent has handed you a task to complete. Produce a complete, self-contained response — the result will be delivered back as a single message.
```

**续跑**（`child_run_id`）时子 agent 的消息列表是 `replay.messages_from_events` 从它自己的 transcript 重建的历史，再加一条 user 消息（新指令）。重建规则与聊天历史相同：只有 user / assistant / system，tool_call 行不回来；带摘要文本的 `compaction_summary` 替换它之前的全部消息，渲染与 live 压缩逐字节相同（见 `app/agent_framework/README.md`）。

**父 agent 看到的**：同步形式下，`Skill(skill="task")` 的工具结果是一个信封，键是 `ENVELOPE_KEYS`：

```json
{"summary": "<子 agent 的完整最终文本>", "key_findings": [], "files_created": [], "tokens_used": 0, "cost_cents": 0.0, "byok_cents": 0.0, "sub_run_id": "...", "status": "success"}
```

`status` 取 `success` / `failed` / `cancelled`（后台形式是 `queued`）。并行形式（`tasks=[...]`）的结果是：

```json
{"status": "success | partial | failed", "tasks_run": 3, "results": [<每个任务一个信封>], "summary": "{ok}/{n} sub-agents dispatched"}
```

后台形式（`await=false`）当场只回 `queued` 信封；真正的结果之后作为收件箱消息到达父 run，框见 `../prompts/README.md` 的 `<inbox_message>`。

#### Token effect

- **同步信封的 `summary` 是子 agent 的完整输出，没有上限**。只有写进父 run transcript 的那份副本被截到 `CLAIMED_TEXT_MAX = 500` 字符（`inbox.clip_claimed_text`）。
- 并行上限：列表长度 `MAX_FANOUT = 10`，并发默认 `DEFAULT_MAX_PARALLEL = 3`（agent 的 `capability_profile.max_parallel_delegates` 可改）。一次 10 路并行可以把 10 份无上限的子输出一次注入父 run。
- 派生深度上限 `MAX_DELEGATION_DEPTH = 3`（`services/workforce/delegate_tool.py`，两种派生方式共用）。
- 子 agent 自己的上下文从零开始（续跑除外），不继承父 run 的历史。

#### KV Cache effect

**子 agent 是独立请求**：它的稳定前缀是它自己 agent 的系统消息，与父 run 无关；同一个子 agent slug 的多次派生共享那段前缀。对**父 run** 而言，信封是一条追加在末尾的 tool 消息，**一轮之内 append-only**，跨轮与其他工具结果一样被丢弃。续跑的历史由 `replay` 逐字节重建，所以子 agent 的前缀在续跑时可以复用；改 `replay` 的角色过滤或摘要渲染会让它失效。

## Known Limitations and Deferred Work

- **同步 `summary` 无上限**：子 agent 的完整输出原样进入父 run 的上下文，10 路并行就是 10 份。transcript 里的副本有 500 字符上限，模型看到的那份没有。
- **`MCP transport failure: {exc}` 把原始异常字符串交给模型**。异常文本可能来自外部 MCP 服务，未转义、无长度上限。本批 PR-F (2026-09-26) 修复（`escape_frame_prose` + 500 字符上限）。
- **两条路径的 Delegate 未配置文案不一致**（流式路短、`run_turn` 路长）。
- **工具结果跨轮丢失**：下一轮模型看不到上一轮的工具结果，只看到 assistant 的最终文本。
- **`[awaiting approval: …]` 混进答复文本**。它是给用户看的标记，被持久化后会作为 assistant 历史出现在下一轮。
- **`key_findings` / `files_created` 永远是空列表**。信封里有这两个键是契约，内容尚未填充。
- **Delegate 的结果不进父 run 收件箱**（设计，fh4 裁定 3），父 agent 只能从 Delegate 工具自己的返回值拿到它。
