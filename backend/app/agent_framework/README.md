# agent_framework — 上下文预算、压缩与框架级注入

与具体 agent 无关的底座：token 计数、上下文窗口、压缩策略、单条消息上限、循环守卫、计划模式、多模态消息、MCP 描述符。本 README 只写其中**会改变模型看到什么**的部分；纯计数 / 纯转发的模块（`tokenizer` / `context_window` / `catalog_windows` / `output_budget` / `tool_result_cache` / `context_engine` / `hooks_protocol`）不单独成块，理由登记在 `tests/services/ai/prompts/test_model_experience_readmes.py` 的 `NOT_MODEL_VISIBLE`。

- `context_compactor.py` — 每轮起跑前的四档压缩（与 `../boundary/summary_frame.py` 一起决定摘要消息的样子）
- `summarizer.py` — 生成摘要的那一次（或两次）独立请求
- `tool_result_pruner.py` — yellow 档的工具结果去重与老化
- `message_truncation.py` — 单条消息的 token 上限与截断标记
- `loop_guard.py` — 同参重复调用的系统警告
- `plan_mode.py` — 计划模式的请求指令
- `multimodal.py` — 附件变成多段内容或文字占位
- `mcp_descriptor.py` — MCP `tools/list` 的描述符（注册表内容在 `../services/ai/skills/mcp_tool_registration.py`）

## Model Experience

### 压缩摘要 `<conversation_summary>` 与四档阈值

#### What the model sees

压缩成功时，历史的**头部**被替换成**一条** system 消息，尾部原样保留在它后面。渲染只有一处（`boundary/summary_frame.py::frame_summary`），逐字如下，`{summary}` 是摘要模型的输出经 `escape_frame_body` 转义、首尾去空白后的文本：

```text
[Earlier conversation summary]
<conversation_summary>
{summary}
</conversation_summary>
```

`[Earlier conversation summary]` 必须保持在第一行：`runner/replay.py` 与 `services/issues/issue_fork.py` 都用 `startswith(SUMMARY_PREFIX)` 认这条消息。live 路径与 replay 路径产出的这条消息逐字节相同（持久化的是**原始**摘要文本，加框只在这一处发生一次）。

摘要两次都没成功（见下一块）或找不到安全切分点时，模型看到的是另一种样子：**没有**摘要消息，头部每条消息被截到单条上限，尾部带 `message_truncation` 的截断标记（见「单条消息上限标记」）。

yellow 档不产生摘要，只改写工具结果正文（见「工具结果去重与老化」）。

走 `claude` 协议时，Anthropic 没有中段 system 角色，`adapters/claude.py::_convert_messages` 把这条消息**原位**转成一条 user 轮，文本由 `boundary/system_note.py::render_system_note` 渲染（它只单框转义 `</system_note>`，摘要自己的 `</conversation_summary>` 保持完整），逐字如下：

```text
<system_note>
[Earlier conversation summary]
<conversation_summary>
{summary}
</conversation_summary>
</system_note>
```

如果它后面紧跟的保留尾部以 user 开头，adapter 的相邻同角色合并会把两者并成**一个** user 轮（note 在前，是一个 text 块）。OpenAI 兼容 adapter 原样保留 `role=system`，codex daemon adapter 把它展平进文本。

#### Token effect

档位按 `(count_tokens(system) + count_messages_tokens(messages)) / window` 判定（`context_compactor.py` 的 `CompactionThresholds`）：

| 档 | 占用 | 动作 |
|---|---|---|
| green | < 0.60 | 不动 |
| yellow | 0.60–0.80 | 只做工具结果去重与老化；做完若已低于 0.80 就停 |
| orange | 0.80–0.90 | 摘要头部，尾部保留 `EMERGENCY_KEEP_RECENT_TURNS = 4` 条消息 |
| red | ≥ 0.90（`context_window.REJECT_RATIO`） | 摘要头部，尾部保留 `RED_KEEP_RECENT_TURNS = 2` 条消息 |

- **摘要只在它比被替换的头部小时才被接受**；空摘要算一次失败。共 `SUMMARY_ATTEMPTS = 2` 次，都失败则落到应急截断：目标是窗口的 `EMERGENCY_TARGET_PCT = 0.70`，头部每条的上限是 `max(200, head_budget // len(head))` token，尾部保留条数同上。
- 切分点由 `_safe_split_index` 往前退，保证 tool_call 与它的 tool 回复不被拆开，所以尾部可能多于 keep-N 条；退到 0 抛 `NoSafeSplitError`，走应急截断。
- **窗口分母**（`context_window.resolve_model_window`）：先查 provider 目录（`catalog_windows`，`nous_models.context_window_tokens`，缓存 `CATALOG_WINDOW_TTL_S = 300` 秒；同一个键撞上两个窗口时取小的，早压缩是分歧里安全的一侧），再查内置表 `_MODEL_WINDOWS`，都没有就用 `LLM_MAX_CONTEXT_TOKENS` 并在 stats 的 notes 里写明「window is a fallback」。窗口 ≤ 0 直接跳过。
- 开关：`AGENT_AUTO_COMPACT=false` 整体关闭。
- **每轮只跑一次**，在 runner 起跑前的预检里（`agent_runner._preflight_compact_and_budget`，两条路都调）。同一轮内工具迭代之间**不**压缩，一轮内的增长只受 `MAX_TOOL_ITERATIONS = 10` 约束。压缩后仍 ≥ 0.90 的请求由 `check_context_budget` 拒绝，`ContextWindowError` 抛给调用方，不进模型。

#### KV Cache effect

**替换更早的 token**。系统消息从不被改；消息列表从下标 0 起被重写。所以任何一次压缩都**必然**打断消息列表的前缀，这是设计，不是副作用。

在聊天路径上还更糟一层：聊天历史每轮从库里重建（见 `../services/ai/chat/README.md`），而压缩摘要**不写回聊天历史**（`compaction_summary` 事件只有 `runner/replay.py` 与 `issue_fork` 读）。所以一个会话一旦越过 orange，**之后每一轮都从头重新摘要头部**：每轮一次摘要请求，每轮一份新的摘要文本，第一次压缩之后就不再有稳定的消息前缀。本模块不承诺任何 provider 缓存命中。

会让复用失效的改动：`SUMMARY_PREFIX` 或框的文字、四档阈值（改变压缩在哪一轮发生）、keep-N 条数、`escape_frame_body` 的转义规则（改变同一份摘要渲染出的字节）。

### 摘要请求：暖前缀与维护模型两条路

#### What the model sees

两条路都是**独立请求**，不进对话本身。先试暖前缀，失败再走维护模型，再失败由压缩器应急截断。

**暖前缀路**（有 adapter 且有系统消息时）：请求就是对话自己的请求——同一个 adapter、同一个模型、同一条系统消息、同一份 tools、头部消息原样——只在末尾加一条 user 消息 `WARM_PREFIX_INSTRUCTION`，逐字如下：

```text
Stop. Do not continue the conversation and do not call any tool.
Compress everything above into a summary an AI agent can resume work from, following these rules in priority order:
1. Preserve verbatim every URL, absolute file path, ID, model name, code symbol, and any number 8+ digits long.
2. Preserve every tool call made and its outcome (success / error / value). Name the tool.
3. Preserve the user's stated intent and constraints (deadlines, must-not-do, preferences).
4. Drop chit-chat, repeated explanations, and verbose tool output already covered by rule 2.
5. Output ONE paragraph of plain text. No bullet points, no headers, no preamble.
```

头部若以带 `tool_calls` 的 assistant 消息结尾，这些消息先被弹掉，让头部落在完整的一问一答上（否则 Anthropic 形状的 provider 会因孤立的 tool_use 回 400）。模型若回了工具调用而不是摘要，抛错走维护模型路。输出经 `strip_reasoning` 去掉 `<think>`。

**维护模型路**：系统消息 `SUMMARIZE_SYSTEM_PROMPT`，逐字如下（源码里续行开头带四个空格，拼接后留在行内，模型看到的就是这样）：

```text
You compress old conversation turns into a faithful summary so an AI agent can keep working past its context window.

RULES (in priority order):
1. Preserve verbatim every URL, absolute file path, ID, model name,     code symbol, and any number ≥ 8 digits long. Quote them as-is.
2. Preserve any tool call the agent made and the outcome (success /     error / value returned). Mention the tool name.
3. Preserve the user's stated intent and any constraints they     mentioned (deadlines, must-not-do, preferences).
4. Drop chit-chat, repeated explanations, and verbose tool output     that's already reflected in step (2).
5. Output a single paragraph, ≤ 500 tokens. No bullet points, no     section headers, no preamble like 'Here's the summary:'.
```

user 消息是 `Summarize the following conversation, preserving the rules in the system prompt:\n\n` 加上展平的头部。展平规则（`_flatten_messages_for_summary`）：每条消息一段 `role: text`，段间空行；多段内容里 `text` 原样、`tool_use` 写成 `[tool_use 'name' args=…]`、`tool_result` 写成 `[tool_result tool_use_id=…]`、其他类型写成 `[type]`；带 `tool_calls` 的消息尾部追加 `[tool_call 'name']`。

#### Token effect

两条路都是 `max_tokens = DEFAULT_SUMMARY_MAX_TOKENS = 600`、温度 0、超时 `SUMMARIZE_TIMEOUT_S = 20.0` 秒；提示词要求 ≤ 500 token。暖前缀路的输入就是对话头部本身，维护模型路的输入是展平后的头部，**没有长度上限**——头部可以超过维护模型自己的窗口。维护模型的选择顺序：环境变量 `COMPACTION_PROVIDER` → `system_settings.compaction_provider` → 维护档默认模型（`get_maintenance_model()`）。

#### KV Cache effect

**独立请求**。暖前缀路的整个卖点是它的前缀与对话已发出的请求逐字节相同（测试钉到对象同一性），所以 provider 的前缀缓存可以覆盖除末尾指令外的全部输入。任何让暖请求与 live 请求出现差异的改动——tools 列表、系统消息、改写消息对象、弹掉的 tool_calls 条数——都让这一点失效。维护模型路没有共享前缀。

### 工具结果去重与老化（yellow 档）

#### What the model sees

工具结果的**正文**被原地替换，tool_call ↔ tool 回复的配对不动。重复调用（同名同参）的后续结果变成：

```text
[duplicate of earlier tool result tool_call_id={original_tcid}; body elided to save tokens]
```

老化的正文变成：

```text
[aged: {gist}... (original body was {chars} chars)]
```

`{gist}` 是正文压掉空白后的前 80 个字符。

#### Token effect

`aging_after_turns = 10`，但它数的是**离列表末尾的消息条数，不是轮数**；正文 ≤ `max_body_chars = 1000` 字符的不老化；`gist_chars = 80`。去重键是 `sha1(name + "\n" + 原始 arguments 字符串)[:16]`。

#### KV Cache effect

**替换更早的 token**。老化边界随每条新消息后移，所以每追加一条消息都可能让又一个正文老化，在那个位置打断前缀。

### 单条消息上限标记

#### What the model sees

超过上限的消息保留**开头**，末尾追加：

```text


[... truncated for length: {dropped} tokens removed by boundary ...]
```

（标记前有两个换行。）多段内容先截最大的那个 text 段，直到落进上限。工具调用的 arguments 从不截断，超限时整个 `function.arguments` 被换成下面这个 JSON 对象（`json.dumps(..., ensure_ascii=False)`，所以它是合法的 arguments）：

```text
{"_truncated": "[tool_call replaced — arguments exceeded {cap} tokens (was {actual} tokens). Original tool_call_id: {tcid}]"}
```

走 `claude` 协议时 `adapters/claude.py::_convert_messages` 对 arguments 做 `json.loads`，所以模型看到的是 `tool_use.input = {"_truncated": "[tool_call replaced — …]"}`（fh5 之前这里是不合法的 JSON，那条路发出的是 `input: {}`）。

#### Token effect

`DEFAULT_PER_MESSAGE_TOKEN_CAP = 50_000`，截断时给标记留 50 token 余量。两个调用方：聊天路径每轮对**每条**历史消息调一次，`model=""`（用默认 tokenizer，`services/ai/chat/ai_library_chat_service.py`）；压缩器的应急截断用小得多的每条上限（见第一块）。

#### KV Cache effect

**替换更早的 token**，但它是确定性的：同一条消息每轮截成同样的样子，所以在聊天的 50k 上限下前缀是稳定的。改上限、改标记文字、换 tokenizer 会让所有带截断消息的会话一次性失效。

### 循环守卫系统警告

#### What the model sees

同一工具同一参数在最近 5 次调用里出现 3 次时，runner 追加一条 **system** 消息（`loop_guard.render_warning`，`{tool}` 是工具名，`{n}` 是窗口里此刻记录的调用数，3 到 5 之间；`3` 是 `repeat_threshold` 已代入）：

```text
[loop_guard] You have called '{tool}' with the same arguments 3+ times in the last {n} tool calls. Stop repeating it — try a different approach (different args, different tool, or answer the user directly). The next turn must NOT call '{tool}' with these arguments again.
```

`{tool}` 是模型自己发出的工具名，经 `escape_frame_body` 转义后才插进来，所以它关不掉任何自有框。

走 `claude` 协议时这条警告被原位转成 `<system_note>` user 轮（形状见上面的压缩摘要块）：

```text
<system_note>
[loop_guard] You have called '{tool}' with the same arguments 3+ times in the last {n} tool calls. …
</system_note>
```

`run_turn` 路（生产路径）在逐个调用的循环**里面**注入，所以触发的调用不是最后一个时，警告夹在同一个 assistant 轮的两个工具结果之间。adapter 的相邻同角色合并把它们并成一个 user 轮，并把 `tool_result` 块提到最前：Claude 看到的是 `[tool_result A, tool_result B, text(<system_note>…)]`，也就是警告被挪到了这一轮所有工具结果**之后**。OpenAI 兼容 adapter 上它仍然夹在两条 `tool` 回复之间（见限制节）。

#### Token effect

约 70 token 一条。`run_turn` 路一轮最多注入一次（`loop_warning_already_injected`）；流式路每次触发都注入。

#### KV Cache effect

**append-only**：追加在对话中段的一条 `role=system` 消息，不改前面的 token。OpenAI 兼容 adapter 原样保留它；`claude` 协议上它是最后一个 user 轮末尾的一个 text 块。合并只把本轮的工具结果与它并进同一个 user 轮，而下一次请求时这个轮已经完整，之后追加的是新的 assistant 轮，所以更早的前缀不受影响。

### 计划模式提示词

#### What the model sees

`plan_mode` 为 `prompt_user` 或 `dry_run` 时，聊天的默认请求指令被**整段替换**成下面这段（`build_plan_prompt()`，`MAX_STEPS = 20` 已代入）：

```text
You are in PLAN MODE. Do NOT execute anything yet. Output a structured
plan for the user to approve.

Output ONLY a JSON object with this shape (no commentary):

{
  "summary": "1-2 sentence summary of what you'll do and why",
  "steps": [
    {
      "id": 1,
      "description": "Plain-language what this step does",
      "tool": "Skill" | "Delegate" | null,
      "args_summary": "human-readable argument summary",
      "side_effects": ["files modified", "API call to X", "..."]
    }
  ],
  "estimated_cost_usd": null | <number>,
  "estimated_seconds": null | <number>,
  "risks": ["thing that could go wrong", "..."]
}

Rules:
- 1-20 steps, sequentially numbered from 1.
- side_effects: explicit list of EVERY observable effect (file writes,
  external API calls, DB mutations, notifications). Be conservative.
- If a step is purely thinking / analysis, set tool=null.
- DO NOT use markdown. DO NOT add preamble. JSON only.
```

#### Token effect

约 250 token，固定，不随任何输入增长。

#### KV Cache effect

它落在系统消息里 `<!-- CACHE_BOUNDARY -->` 之后的请求指令段，**不影响稳定前缀**；开关计划模式只改边界之后的那一段。

### 附件占位与多段内容

#### What the model sees

模型支持视觉时，带附件的 user 消息是 OpenAI 多段形状：先 `{"type": "text", "text": …}`（有文字时），再每个图片 / 视频缩略图 / PDF 页一个 `{"type": "image_url", "image_url": {"url": …}}`，音频一个 `{"type": "input_audio", "input_audio": {"data": url, "format": mime 或 "wav"}}`。

不支持视觉时，每个附件一行文字占位，空一行，再接原文（`flatten_attachments_to_text`）：

```text
[attachment {i} — {kind}: {label}]
```

`{label}` 是 `alt_text`、否则 URL、否则 `(inline data)`，截到 200 字符；`{kind}` 是附件类型把下划线换成空格。

多段消息被展平回纯文本时（`flatten_to_text`，给不认多段内容的下游用），图片写成 `[image: {url 前 200 字符}]`，音频写成 `[audio]`，其他写成 `[{type}]`。

#### Token effect

图片的 token 由 provider 按像素计，不在本模块控制内；文字占位每个附件一行。data URL 被截到 200 字符后只是噪声，不携带图像内容。

#### KV Cache effect

**append-only**：只塑形本轮新的 user 消息。历史图片的重放策略（哪几条消息重新内联图片）在 `../services/ai/chat/README.md`，那里会改写更早的消息。

### MCP `tools/list` 与 `tools/call`（外部客户端）

#### What the model sees

读者是**外部** MCP 客户端（Claude Desktop / Cursor 等，经 `mcp_stdio.py` 的 stdio 传输），不是本仓的 runner。`tools/list` 里每个工具只有三个字段（`Tool.to_descriptor`）：

```json
{"name": "skill.{slug}", "description": "...", "inputSchema": {"type": "object", "properties": {}, "additionalProperties": true}}
```

`handler` 永不序列化，`tests/agent_framework/test_tool_descriptor_allowlist.py` 钉住。skill 的 description 是 skill 的 description、否则 name、否则 slug；schema 是放任的（`additionalProperties: true`）。agent 名为 `agent.{slug}`，description 是 agent 的 name 或 slug，schema 只有一个必填字符串：

```json
{"type": "object", "properties": {"prompt": {"type": "string", "description": "User instruction for this agent."}}, "additionalProperties": false, "required": ["prompt"]}
```

`tools/call` 的返回（`services/ai/skills/mcp_tool_registration.py`）：skill 返回它的 `body_md`；body 为空时返回错误 `skill '{slug}' has no body_md — check seed loading or DB row.`。agent **不执行**，返回人设全文：

```text
# Agent: {slug}
(MCP-side execution not yet wired — returning agent persona.)

## Identity
{identity_md}

## Soul
{soul_md}

## Instructions
{agent_md}

## Caller's prompt
{prompt}
```

#### Token effect

`tools/list` 随 skill 与持久 agent 的数量线性增长，没有上限；skill 的 `tools/call` 返回整份 `body_md`，没有 64k 截断（与 `Skill()` 工具不同）。

#### KV Cache effect

**独立请求**：这是另一个客户端自己的请求，完全在本仓缓存契约之外。本模块的改动只影响外部客户端看到的工具清单。

### 出站 MCP 工具（agent 连接的第三方 server）

#### What the model sees

与上一块方向相反：这里是**我们的模型**调用用户登记的第三方 MCP server。用户在 `user_mcp_servers`（mig 194）里启用的每一行，由 `services/ai/chat/ai_library_chat_wiring.py` 装进 `MCPOutboundRegistry`（`mcp_outbound_registry.py`）；今天只有 1:1 聊天路装配它，用户一行都没启用时 registry 为 `None`，模型什么都看不到。

有 registry 时，`AgentRunner.run_turn` 与 `stream_turn` 每轮调一次 `all_tools()`，经 `_mcp_tools_to_openai_format`（`services/ai/runner/agent_runner.py`）把每个工具追加到 composer 产出的 `tools` **末尾**。每个工具的形状：

```json
{"type": "function", "function": {"name": "{server}.{tool}", "description": "{server 给的 description，缺省为空串}", "parameters": {"...": "server 给的 inputSchema 原样，缺省 {\"type\": \"object\"}"}}}
```

`{server}` 是用户给这台 server 起的名字，`{tool}` 是 server 在 `tools/list` 里报的名字。**名字、描述、参数 schema 三样全部来自第三方 server，原样进模型，不转义、不截断。** 调用结果也是 server 返回什么就进工具消息什么；只有传输失败被改写成 `MCP transport failure: …`（`../services/ai/runner/mcp_errors.py`，经 `escape_frame_prose`、截到 500 字符，见 runner README）。某台 server 的 `tools/list` 失败时只丢它自己的工具，本轮照常。

#### Token effect

**没有上限**：条数 = 已启用 server 数 × 每台 server 报的工具数，每条描述与 schema 的长度由对方决定。每个 client 的工具清单缓存 5 分钟（`DEFAULT_TOOLS_CACHE_TTL_SECONDS = 300`），缓存只省网络往返，不省 token——每轮请求都带全部工具。

#### KV Cache effect

追加在 `tools` 末尾，所以不动它前面的内置工具，但 `tools` 在多数 provider 侧位于系统消息之前：server 改了工具清单、用户启用或停用一台 server、或某台 server 本轮 `tools/list` 失败，都会让这位用户的前缀在那一刻失效一次。清单不变时逐轮稳定。工具结果是 append-only 的工具消息。

## Known Limitations and Deferred Work

- **循环守卫警告的注入位置与工具结果交错**（fh5 T1 留票）。`run_turn` 缓冲路在逐个调用的循环里注入，触发的调用不是最后一个时，警告夹在同一个 assistant 轮的两条工具结果之间：`claude` 协议靠 adapter 的合并与 `tool_result` 提前兜住，OpenAI 兼容 provider 可能拒绝 `tool` 回复之间的非 tool 消息。流式路注入后 `break`，同一轮剩下的调用**没有**工具结果，两种 API 都会拒绝。修法是把缓冲路的注入挪到循环之后、流式路给跳过的调用补结果。
- **聊天会话越过 orange 后每一轮都重新摘要头部**。压缩摘要不写回聊天历史，历史每轮从库重建，所以每轮一次摘要请求、每轮一份新摘要，没有稳定前缀。第 1 批把「摘要持久化」推迟了；fh4 计划裁定 8 建议第 5 批翻案，本批只记。
- **`tool_result_pruner` 在今天的生产路径上实际不生效**。预检压缩跑在历史上，而聊天历史不带 `role=tool` 行（只重建 user / assistant / system），replay 也丢掉 tool_call 行；去重、老化与工具配对切分只对一轮之内的列表起作用，而一轮之内不压缩。
- **去重键用的是原始 arguments 字符串**。`_hash_tool_call` 旁的注释说会按键排序，实际没有：参数顺序不同的同一调用逃过去重。
- **`aging_after_turns` 数的是消息不是轮**，名字会误导。
- **维护模型路的展平头部没有长度上限**，可以超过维护模型自己的窗口。
- **一轮之内的增长不受压缩约束**，只受 `MAX_TOOL_ITERATIONS = 10` 约束。
- **`summarizer.py` 模块 docstring 仍写默认 `claude-haiku-4-5`**，`context_compactor.py` 里「Phase 2 will swap this for an LLM summarizer」也是过时注释；真实顺序见上文。
- **流式路的循环守卫触发后 `break` 跳出本批工具**：同一条 assistant 消息里排在后面的 tool_call 既不执行、也不补合成结果（AskUser 停靠路径会补 `skipped` 结果，这里不会）。流式路今天不是生产路径（生产 adapter 是没有 `stream` 的 `LLMFallbackChain`），但它一旦启用，这些孤立的 tool_call 会让 Anthropic 形状的 provider 回 400。
- **`multimodal` 的 `alt_text` 是用户设的文件名，未转义**。它不在任何我们拥有的框里，所以 #2472 没有处理，留票。
- **MCP 的 agent 工具不执行 agent**，只回人设（`mcp_tool_registration.py` docstring 写明推迟）；skill 描述缺失时退到 name / slug。
- **出站 MCP 的名字、描述、参数 schema 与调用结果原样进模型**（fh5 T4 登记，留票）。它们来自用户登记的第三方 server，没有转义、没有长度或条数上限；一台恶意或出错的 server 可以用工具描述塞入任意指令文本，也可以用上百个工具撑大每轮请求。要收口就在 `_mcp_tools_to_openai_format` 给描述加上限并过 `escape_frame_body`、给条数加上限，结果侧另议是否过 `neutralize_external_text`。
