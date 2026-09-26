# chat — 1:1 聊天轮次的组装

`ai_library_chat_service.py` 把一次聊天轮次拼成请求：系统消息由 `../prompts/` 组装，这里负责两件事——**历史怎么重建**，以及**缓存边界之后的请求指令里放什么**。系统消息本身的结构、`<available_resources>` / `<referenced_outputs>` 等框见 `../prompts/README.md`；runner 在一轮之内追加的工具消息见 `../runner/README.md`。

- `conversations_ai_store.py` — 读写会话消息（历史的来源）
- `turn_history.py` — 本轮从哪段历史起跑（存储的摘要 + 水位之后的行，或最新 200 条），以及把本轮被接受的摘要写回 `conversation_memory`
- `history_image_replay.py` — 把历史重建成消息列表，并按预算重放最近的图片
- `ai_library_chat_service.py` — 请求指令组装、单条上限、工具挂载

## Model Experience

### 聊天历史组装（每轮从库重建）

#### What the model sees

历史由 `turn_history.load_turn_history` 决定，两种形状（fh5 A2）：

- **有存储摘要**（`direct_agent` 会话，且 `conversation_memory` 里有这个会话的非空行）：第 0 条是存储的摘要框（`render_summary_message(summary_md)`，逐字节与 live 压缩、replay 产出的一样，框的字面见 `app/agent_framework/README.md`），后面接 **`seq > last_seq_summarized` 的最新 200 条**未删除消息（`get_messages_after`）。
- **其余情况**（还没压缩过、群聊会话、sidecar 读失败）：**最新 200 条**未删除消息（`get_messages(..., newest=True)`，默认 `limit=200`）。

两种都按 seq 升序，再由 `build_history_messages` 转成消息列表：

- 只保留 `user` / `assistant` / `system` 三种角色，每条只有 `role` + `content`。**工具调用与工具结果从不重放**——上一轮 runner 产生的 tool_call 存根不进历史，持久化的 assistant 消息只有最终文本。
- assistant 文本是 runner 去掉 `<think>` 之后的版本（`runner/reasoning.strip_reasoning`），所以历史里只有答案。
- 分叉出来的会话会带一条持久化的 system 消息，内容是源会话的压缩摘要（`append_system_message`，形状见 `app/agent_framework/README.md` 的摘要框）。走 `claude` 协议时这条消息被 `adapters/claude.py::_convert_messages` 原位转成一条 `<system_note>` user 轮（形状见 `app/agent_framework/README.md` 的摘要块与 `app/boundary/README.md` 的 `<system_note>` 块）。
- 模型支持视觉时，**最新 `REPLAY_MAX_MESSAGES = 2` 条带图片附件的 user 消息**被重建成多段内容（图片字节重新从对象存储取、经调用者的 team 成员关系校验），全局最多 `REPLAY_MAX_IMAGES = 4` 张；更早的图片消息、取不到的、以及不支持视觉的模型，都保持纯文本。多段形状见 `app/agent_framework/README.md` 的「附件占位与多段内容」。
- 然后本轮新的 user 消息接在最后（`assemble_turn_messages`，同时给出与消息 1:1 的 seq 列表：摘要框是水位、历史行是各自的 `seq`、新 user 是 `None`，交给 runner 预检让压缩器算出新摘要覆盖到哪）。每条消息再过一遍单条上限（`cap_messages_tokens`，50k token，标记文字见 `app/agent_framework/README.md`）。
- 摘要框**不会**出现在会话视图里：它只在 sidecar 表，`GET /ai-library/sessions/{id}` 读的是 `messages`；消息 dict 里新增的 `seq` 键被 `LibraryChatMessageOut` 过滤掉，OpenAPI 不变。

#### Token effect

上限是（摘要框 +）200 条消息乘单条 50k token，实际由 runner 预检里的四档压缩兜住（`app/agent_framework/README.md`）。有存储摘要时历史只含水位之后的行，所以一次压缩之后的若干轮都在 orange 以下、不再调摘要；尾部再越过 orange 时新摘要把旧摘要连同头部一起吸收（取代，不链接），`conversation_memory` 始终一行。图片重放每轮最多 4 张，每张每轮都重新取、重新内联。

#### KV Cache effect

**两次压缩之间是 append-only**：有存储摘要时第 0 条是同一个摘要框，后面按 seq 追加，前缀逐轮稳定（长会话基准 `tests/benchmarks/test_long_session_continuation.py` 断言 20 轮里前缀只在产生新摘要的轮次断开，摘要调用 ≤ 3 次；A2 之前是每轮 1 次、每轮断开）。结构性的前缀破坏还剩：

1. **新摘要被接受的那一轮**：摘要框换成新文字，水位前进，这是设计。
2. 还没有存储摘要、会话又超过 200 条时，窗口**每轮滑动一次**，第 0 条消息每轮都变。
3. 第 3 条带图片的 user 消息出现时，最早那条被重放的图片消息**退回纯文本**，改写了更早的一条消息。
4. 编辑 / 删除一条 `seq <= 水位` 的消息会删掉存储摘要（`conversation_service`），下一轮回到最新 200 条窗口。

会让复用失效的改动：窗口条数、两个重放预算、角色过滤规则、`strip_reasoning` 的规则、摘要框的渲染（见 `app/agent_framework/README.md`）。本模块不承诺 provider 缓存命中。

### 聊天 request_instructions 组装（缓存边界之后）

#### What the model sees

请求指令落在系统消息 `<!-- CACHE_BOUNDARY -->` 之后的 `# Request Instructions` 段（系统消息骨架见 `../prompts/README.md`）。由内到外逐层前置，最终顺序是：

1. `[link-summary]` 块（本轮用户消息含 URL 时，见 `../prompts/README.md`）
2. `<user_selection>`（带剧本选区时）
3. `<pending_followups>`（会话第一轮且有待兑现的承诺时）
4. 默认指令，或计划模式时整段换成计划提示词（见 `app/agent_framework/README.md`）

各层之间空一行。默认指令逐字如下：

```text
You are in an interactive chat session with the user. Respond conversationally. Use the Skill tool when a bound skill is clearly applicable; otherwise answer directly in natural language. If <available_workers> lists a specialist agent that's a clearly better fit for the request than you are (e.g. summarize for transcript condensation, analyze for visual analysis), call Delegate(agent_slug=..., prompt=..., await=true) and weave the returned result into your reply. Use Delegate only when the specialist is a clear win — for general chat, just answer directly.
```

`<pending_followups>`（`_surface_next_session_commitments`），`{n}` 是待兑现条数，每条描述经 `escape_frame_body`：

```text
<pending_followups>
You committed to {n} follow-up(s) in earlier sessions. Surface them naturally in your first reply if relevant:
  - {description}
</pending_followups>
```

`<user_selection>`（`format_script_context_block`）。有 `element_ids` 时：

```text
<user_selection>
scene: {scene_label}
scene_id: {scene_id}
element_ids: {id1}, {id2}
element_type: {element_type}
spans multiple scenes
The user's message refers to this selection. Use these ids directly (ReadScene the scene, then target the selected elements with ProposeEdit/ApplyEdit) instead of re-locating the text by content.
</user_selection>
```

只有 `scene_id` 时，最后一行换成：

```text
The user's message refers to this scene. Use this scene_id directly (ReadScene the scene) instead of re-locating it by content.
```

`scene` / `element_type` / `spans multiple scenes` 三行各自可缺省。`scene_label` 与 `element_type` 经 `escape_frame_body`；`scene_id` 与每个 `element_ids` 项经 `escape_frame_attr`（#2472，正常 id 原样不变）。请求模型 `ScriptContextRequest` 限长：id 各 64、`element_ids` 最多 100 项、`element_type` 64、`scene_label` 500 字符。

issue 触发的轮次（`issue_dispatch` / `issue_dispatch_auto` / `issue_reply`）另在**整条系统消息末尾**（`# Runtime` 行之后）追加 `FinishIssue` 指令，并在 tools 里挂上 `FinishIssue`；见 `../prompts/README.md`。能力授予时 tools 里还会挂 `GenerateImage` / `GenerateVideo`，同见该 README。

#### Token effect

默认指令约 110 token，计划提示词约 250 token。`<pending_followups>` 最多列 5 条；`<user_selection>` 受请求模型限长约束，最多约 100 个 id。链接块每个 URL 最多约 2k token 正文加标题与描述，最多 3 个 URL。这些都只在本轮存在，不持久化，所以不随会话增长。

#### KV Cache effect

**全部在缓存边界之后**，对稳定前缀没有影响；逐轮变化只改边界之后那一段。

### 工具 schema：`ResourceFetch`（1:1 聊天）

#### What the model sees

本轮 @-mention 解析出至少一条**资源**引用时（只 @ 了 `prompt` 类资产、没有可取的资源时不挂），`AILibraryChatService` 把下面这份内联 schema 追加到 composer 产出的 `tools` 末尾（在 `GenerateImage` / `GenerateVideo` 之前），并把本轮可访问的资源 id 集合绑进 runner 的 `resource_fetch_handler` 闭包。逐字如下：

```json
{"type": "function", "function": {"name": "ResourceFetch", "description": "Load content for a resource listed in <available_resources>. Call with the resource id and an optional mode.", "parameters": {"type": "object", "properties": {"resource_id": {"type": "string", "description": "id attribute from <available_resources>."}, "mode": {"type": "string", "description": "How to read the resource. Defaults vary by kind — see system message for details."}, "args": {"type": "object", "description": "Optional extra args (e.g. {page: 2} for PDF)."}}, "required": ["resource_id"]}}}
```

它与团队频道 @agent 那份同名 schema（`app/services/chat/README.md`）**描述不同**：这份指向系统消息里的 `<available_resources>` 目录（见 `../prompts/README.md`），那份说「available in this conversation」。两份各有各的块，谁也不能替对方过守卫。

执行体是 `services/ai/tools/resource_fetch_tool.resource_fetch`：成功 `{"content": …, "meta": {"name", "mode"}}`，失败 `{"error": "<短句>"}`；只认本轮绑定的 id 集合，模型伪造的 id 拿到的是错误而不是内容。图片结果的两种注记与本轮没挂此工具时的错误文案见 `../runner/README.md`；墙钟上限 200s 的超时结果见 `../prompts/README.md`。

#### Token effect

schema 固定约 140 token（紧凑 JSON 584 字符），只在有资源引用的轮次出现。工具结果的大小由资源决定：文本类按 mode 返回正文或摘要，图片走多段内容（见 runner README 的图片注记），本工具自身不截断，进上下文后受 runner 的单条消息上限约束。同一轮内对同一资源的重复调用由 `request_cache` 命中，不重复读取。

#### KV Cache effect

`tools` 在多数 provider 侧位于系统消息之前的前缀里。这份 schema **按轮出现**：同一会话里有 @-mention 的轮次与没有的轮次 `tools` 不同，所以两类轮次各是一个前缀族，在两类之间切换就换一次前缀；改这份 schema 的任何一个字会让有 @-mention 的前缀族一次性失效。工具结果是 append-only 的工具消息。

## Known Limitations and Deferred Work

- **200 条窗口滑动（没有存储摘要时）**：会话一过 200 条而还没压缩过，每轮第 0 条都变。有存储摘要之后只读水位之后的行。
- **水位之后超过 200 条时中间有缺口**：那段行既不在摘要里也不在窗口里，`load_turn_history` 记 warning。正常情况下压缩远早于此触发。
- **图片重放会改写更早的消息**：第 3 条图片消息出现时，最早被重放的那条退回纯文本。
- **分叉会话不写 `conversation_memory`**：分叉把摘要作为一条持久化的 system 消息带过去（上文），它的第一次压缩把那条消息一起摘掉后才有自己的 sidecar 行。
- **工具调用不跨轮**：上一轮模型读过的工具结果，下一轮只剩 assistant 的最终文本。需要再看就得再调。
- **`<pending_followups>` 的条数与列表可能不一致**：待兑现超过 5 条时，开头说的是全部条数，列表只列 5 条，也只把这 5 条标成已兑现。标记失败时静默跳过（`except Exception: pass`），那条会在下个会话再出现。
- **`<user_selection>` 的 id 在 #2472 之前原样进框**，客户端可以借它提前关掉框。#2472 起经 `escape_frame_attr` 并在请求模型上限长；记录在此供对照。
- **单条上限用的是默认 tokenizer**（`model=""`），对非默认 tokenizer 的模型只是近似。
- **计划模式整段替换默认指令**。`ai_library_chat_service.py` 里有一处 docstring 说它是「前置」，以代码为准。
