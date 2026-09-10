# prompts — 系统消息组装

把 agent 的三份身份文档、绑定的 skill 清单、可派发的 worker 清单、记忆/图谱召回，装配成一条系统消息，并算出用于 provider 前缀缓存的指纹。

- `prompt_composer.py` — `PromptComposer.compose()` 是唯一入口，八条 dispatch 路径都走它
- `link_injection.py` — 用户消息里的 URL → 抓取 → 中和后的块，前插到 request instructions
- `render_available_resources()` — @-mention 的资源与资产清单（模块级函数，供 chat 层按轮调用）

## Model Experience

### 系统消息（每次请求）

#### What the model sees

固定顺序的 markdown 段落，段间空行分隔。稳定骨架逐字如下（`<available_workers>` 之后是缓存边界标记）：

```markdown
# Identity
{agent.identity_md}

# Soul
{agent.soul_md}

Embody the persona and tone described above. Avoid generic or stiff replies unless higher-priority instructions override it.

# Agent Instructions
{agent.agent_md}

## Available Skills
Before replying: scan <available_skills> entries.
- If one clearly applies: call Skill(skill="<slug>") first, then follow the returned instructions.
- If none apply: do not call Skill.
Never call Skill more than once per turn unless the task clearly requires chaining.

<available_skills>
  <skill>
    <name>{slug}</name>
    <description>{description}</description>
  </skill>
</available_skills>

<!-- CACHE_BOUNDARY -->

# Request Instructions
{request_instructions}

# Runtime
Model: {model} | Time: {YYYY-MM-DD HH:MM UTC}
```

空的段直接省略（没有 `identity_md` 就没有 `# Identity`）。完整装配后的样子以 `tests/services/ai/prompts/snapshots/system_message_text_turn.txt` 为准——那是本仓唯一一处逐字 pin。

`<available_workers>` / `<graph_facts>` / `<user_context>` / `<agent_memory>` 各自条件出现，形状见快照。

#### Token effect

身份三段与 skill 清单随 agent 配置增长，**无上限**——`identity_md` / `soul_md` / `agent_md` 是无界 TEXT 列，装多少进多少。skill 清单只进 slug + description（不进 body），所以绑 20 个 skill 也只是几百 token；skill 正文要等模型主动调 `Skill()` 才进上下文（见 `../skills/README.md`）。

`<graph_facts>` / `<agent_memory>` 由召回层定条数；`<user_context>` 是 Honcho 的工作表示，按用户增长但每轮替换、不累积。

#### KV Cache effect

**稳定前缀 + 每轮替换的尾部**，边界就是 `<!-- CACHE_BOUNDARY -->`。

- 边界**之前**（身份 / skills / workers）逐轮不变，构成可复用前缀。改 agent 任一份文档、增删一个绑定 skill、增删一个 persistent agent，都会让前缀失效——这是正确的。
- 边界**之后**（图谱事实 / 用户模型 / agent 记忆 / request instructions / runtime 行）每轮都变。`# Runtime` 里的时间戳精确到分钟，所以**同一分钟内的连续请求前缀相同，跨分钟必变**——这一段本来就在边界之后，不影响前缀复用。
- 「不会让复用失效」指的是本模块保证不动已有前缀；provider 侧缓存**是否命中、何时淘汰**不在本模块契约内。

指纹：`_prefix_fingerprint()` 只吃边界之前的输入（agent 三段 + skill id/updated_at + worker id/slug）；`_dynamic_fingerprint()` 在它之上再吃三份召回记忆的**内容哈希**——目的是缓存隔离，让 A 用户的记忆不可能被当作 B 用户的前缀命中。

### 工具 schema：`Skill`（请求的 `tools` 参数，不在系统消息文本里）

#### What the model sees

`_skill_tool_spec()` 产出的 function 描述，随每次请求以 `tools` 参数发出（不进 `system_message_text_turn.txt` 快照）。稳定字面量：

```json
{"name": "Skill",
 "description": "Load a local skill definition and its instructions. Returns the SKILL body (and optional sub-file content). Built-in skill=\"todo\" keeps your multi-step plan for this turn: op=replace with items to set the steps, then op=complete with id as you finish each. Built-in skill=\"task\" runs a sub-agent synchronously: subagent_type + prompt (or tasks=[...] to fan out) and returns its result.",
 "parameters": {"type": "object", "required": ["skill"],
   "properties": {
     "skill": "Skill slug from <available_skills>, or a built-in: 'todo' or 'task'.",
     "file":  "Optional sub-file path like 'references/examples.md'. Omit to return the SKILL.md body.",
     "op":    {"enum": ["replace", "complete", "in_progress", "pending", "show"]},
     "items": [{"content": "string", "active_form": "string (optional)"}],
     "id":    "integer",
     "subagent_type": "string", "prompt": "string", "description": "string",
     "tasks": [{"subagent_type": "string", "prompt": "string", "description": "string (optional)"}],
     "await": "boolean — false 则后台跑，结果晚些以收件箱消息到达，而不是这次调用的返回值",
     "child_run_id": "string — 续跑某个更早的子 run，而不是新开一个"}}}
```

`subagent_type` / `prompt` / `description` / `tasks` 只对 `skill="task"`（派子 agent）有意义，与 todo 的四个参数同一天补齐、同一理由。

`await` / `child_run_id`（2026-09-10，harness 二期 2b-2）也只对 `skill="task"` 有意义。省略 `await` 等于 `true` —— 没听说过这个参数的模型拿到的仍是原来的同步行为。`await=false` 的返回值是 `{"status": "queued", "task_id": …}`，**`sub_run_id` 是 `null`**：那一刻还没有子 run。三条拒绝也从这里回给模型：`no_reply_target`（结果没有可投递的目标）、`async_not_allowed_for_subagent`（子 agent 不能再派后台子 agent）、`continue_not_allowed_in_fanout`（`tasks` 与 `child_run_id` 同时出现）。

`op` / `items` / `id` 只对 `skill="todo"` 有意义，2026-09-06 起才声明——此前模型只看得到 `skill` 与 `file`，内建 todo 的参数全靠猜：doubao lite 把 `"?op=replace&items=…"` 塞进 `file` 连错四次，整轮没有一个 todo 快照，任务卡的 n/m 也就从未出现。模型用不了它没被展示的参数，这不是提示词问题。

#### Token effect

固定约 300 token，不随 agent 配置增长；每次请求都带（provider 把 `tools` 当请求的一部分计费）。

#### KV Cache effect

`tools` 在多数 provider 侧位于系统消息之前的前缀里，所以**改这份 schema 的任何一个字都会让全部 agent 的前缀一次性失效**——这是一次性的，之后逐轮不变。本模块不为它计指纹（`_prefix_fingerprint()` 不吃 tools），因为它对所有 agent 恒等，没有跨 agent 串味的问题。

### 收件箱消息框 `<inbox_message>`（步骤边界注入的 user 消息）

#### What the model sees

`runner/inbox.py::render_inbox_message` 在步骤边界把每条领到的收件箱行渲染成一条 user 消息。我们拥有这个框（登记在 `OWNED_FRAMES`），属性走 `escape_frame_attr`、正文走 `escape_frame_prose`：

```
<inbox_message kind="steer" at="2026-09-05T00:00:00+00:00">
{正文}
</inbox_message>
```

`kind="subagent_result"`（2026-09-10，harness 二期 2b-2）多带两个属性，正文只有子 agent 的 summary：

```
<inbox_message kind="subagent_result" at="…" child_run_id="52" subagent_type="librarian">
found three docs
</inbox_message>
```

`child_run_id` 是给父 agent 下一轮 `Skill(skill="task", child_run_id=…)` 续聊用的。信封里其余字段（`status` / `cost_cents` / `tokens_used` / `description`）**刻意不进框** —— 模型无法据它们行动，进框只是白烧 token。

#### Token effect

每条一个框，长度就是那条消息的正文长度。`subagent_result` 只放 summary，所以一次后台子 agent 的回执通常是几十到几百 token，而不是整个信封的 JSON。领取本身有条数上限（见 `agent_run_inbox_repository.claim`），所以单个步骤边界注入的量是有界的。

#### KV Cache effect

**append-only**：这些框作为新的 user 消息追加在历史末尾，不改写更早的 token，因此不使 provider 前缀失效。本模块不为它计指纹。

### 工具 schema：`AskUser`（请求的 `tools` 参数，两条路都有）

#### What the model sees

`ask_user_spec()` 产出的完整 function 描述（`json.dumps(ask_user_spec(), indent=1)` 原样），2026-09-08（harness 二期 2a Task 3）起随**每次**请求以 `tools` 参数发出——聊天与 issue 两条触发路径都注入，位置在 `FinishIssue` 之后。稳定字面量：

```json
{
 "type": "function",
 "function": {
  "name": "AskUser",
  "description": "Ask the human a question and stop until they answer. Use it when you cannot proceed without a decision. Give up to 6 short options when the choice is between known alternatives; the human may also type a free-text answer unless you set allow_free_text to false. Your turn ends after this call; you will receive the answer as the next user message.",
  "parameters": {
   "type": "object",
   "properties": {
    "question": {
     "type": "string",
     "maxLength": 500,
     "description": "The question, one or two sentences."
    },
    "options": {
     "type": "array",
     "maxItems": 6,
     "description": "Up to 6 choices for the human. Labels must be unique and at most 80 characters; omit when the answer is open-ended.",
     "items": {
      "type": "object",
      "required": [
       "label"
      ],
      "properties": {
       "label": {
        "type": "string",
        "maxLength": 80
       },
       "description": {
        "type": "string",
        "maxLength": 200
       }
      }
     }
    },
    "allow_free_text": {
     "type": "boolean",
     "default": true,
     "description": "Whether the human may answer with their own text instead of picking an option."
    }
   },
   "required": [
    "question"
   ]
  }
 }
}
```

`options` 的子 schema是 `question.OPTIONS_JSON_SCHEMA` 这**一个**对象，`FinishIssue.options`（同日新增，仅 issue 触发可见）引用的是同一个，所以模型在两处看到的形状永远一致。

模型调用后收到的工具结果是 `{"asked": true, "question_id": "q:<run>:<seq>", "warnings": []}`，随后**本轮立即结束**（`turn_end{awaiting_input}`），模型不会再被调用；回答以下一条用户消息的形式到来，正文就是它自己给出的 label（或自由文本）。不合规的 `options`（重复 / 空 / 超长 label、超过 6 项）不会让调用失败：问题退化为开放问题，`warnings` 里说明原因。

#### Token effect

固定约 262 token（紧凑 JSON 1050 字符），不随 agent 配置或对话增长；每次请求都带（与 `Skill` 同一计费方式）。`FinishIssue.options` 另加约 100 token，只在 issue 触发时存在。

#### KV Cache effect

与 `Skill` 同一段前缀：改这份 schema 的任何一个字都会让全部 agent 的前缀一次性失效，之后逐轮不变。本模块不为它计指纹（对所有 agent 恒等）。注意 `AskUser` 出现在 `FinishIssue` **之后**，所以 issue 触发与聊天触发的 `tools` 列表前缀不同——两条路本来就是两个缓存键，这不新增失效。

### 工具 schema：`ScheduleWakeup`（仅 issue 根 run）

#### What the model sees

`schedule_wakeup_spec()` 产出的 function 描述，2026-09-10（harness 二期 2b-2 Task 5）起注入，位置在 `FinishIssue` 之后、`AskUser` 之前。**只在 issue 根 run 上存在**——聊天触发与后台子代理 run（`trigger="workforce"`，走 workforce worker，根本不经过这段注入）都看不到它。稳定字面量：

```json
{
 "type": "function",
 "function": {
  "name": "ScheduleWakeup",
  "description": "Schedule a one-time wake-up for this issue; when it fires you will receive the note as a message. Use it to wait for long external work instead of polling.",
  "parameters": {
   "type": "object",
   "properties": {
    "at": {
     "type": "string",
     "description": "Absolute time to wake up, ISO-8601 (e.g. 2026-09-11T09:00:00+00:00). Takes precedence over delay_minutes."
    },
    "delay_minutes": {
     "type": "integer",
     "description": "Wake up this many minutes from now."
    },
    "note": {
     "type": "string",
     "description": "What you want to be told when it fires — you will receive this text as a message."
    }
   },
   "required": [
    "note"
   ]
  }
 }
}
```

调用成功后模型收到 `{"schedule_id": "<uuid>", "fire_at": "<ISO>"}`，**本轮照常继续**（与 `AskUser` 不同，它不停靠）。拒绝一律是工具结果不是异常，模型可以据此改时间重试：`at or delay_minutes required` / `note is required` / `at must be an ISO-8601 timestamp` / `delay_minutes must be a whole number of minutes` / `fire_at must be in the future` / `fire_at must be within 30 days` / `too_many_wakeups`（每个 **run** 最多 3 次——按 `payload.run_id` 在表上数，所以跨轮次也是 3 次不是每轮 3 次；被拒的调用不计数）/ `ScheduleWakeup failed: <ExceptionClass>`。未注册该工具的轮次上误调用得到 `ScheduleWakeup is not available on this turn — it only applies while working an assigned issue.`。

到点时模型看到的**不是**这个工具的返回，而是一条普通用户消息（issue 空闲）或收件箱里的 steer 框（run 在跑）——正文就是 `note` 原文。

#### Token effect

紧凑 JSON 约 480 字符、约 120 token，恒定，不随 agent 配置或对话增长。只在 issue 根 run 的 `tools` 数组里；聊天路与子代理 run 上完全不存在，那两条路的请求体一个字节都不变。

#### KV Cache effect

`tools` 数组是稳定前缀的一部分，issue run 与 chat run 因而是两个前缀族——**本模块任何改动（描述、参数名、参数顺序）都会让 issue 路的前缀复用一次性失效，chat 路不受影响**。本模块不为它计指纹（对所有 agent 恒等）。⚠️ provider 端是否真的命中缓存不在本模块契约内。

### 工具结果：超时（任何工具，两条路都有）

#### What the model sees

2026-09-09（harness 二期 2b-1 Task 4）起每个工具调用都有墙钟上限（`runner/tool_timeouts.py`：Skill 30s / ResourceFetch 200s（高于其自身 180s 的抽帧截止，让它自己的「frame extraction timed out」结果仍可达）/ GenerateImage、GenerateVideo 600s / Delegate 900s / `skill.*` 同 Skill、`agent.*` 同 Delegate、其余 MCP 名 120s / 其他 60s；**AskUser、FinishIssue 不计时**——它们只写一行 transcript，切断会留下 runner 没看见的停靠问题；`config.yml TOOL_TIMEOUTS` 按工具名覆盖）。超时**不是** run 停止：模型收到的是那次调用的工具结果，正文是这一行 JSON（`timeout_s` / `elapsed_s` / `tool` / `message` 里的数值随实际值变化，其余字面量固定）：

```json
{"error": "timeout", "timed_out": true, "timeout_s": 200.0, "elapsed_s": 200.004, "tool": "ResourceFetch", "message": "Tool ResourceFetch timed out after 200s. Retry once with a narrower request, or choose another way."}
```

然后本轮照常继续——模型自己决定重试、换工具还是告知用户。工具自己抛异常（包括它内部 `wait_for` 抛的 `TimeoutError`）不经过这层：各调用点既有的类型化错误结果（如 `ResourceFetch failed: …` / `MCP transport failure: …`）原样不变。⚠️ Delegate 只是**入队**（`delegate_tool` 自己 180s 内等一次回执）：超时发生在入队之后时任务其实已派出，模型若据此重试会重复派发——`dedup_key` 可选，README 记录此风险，Task 8 真栈验收覆盖。

#### Token effect

一次超时一条 ≤220 字符（约 60 token）的工具消息，与任何工具结果同样进入本轮对话并随历史压缩；不随 agent 配置增长。

#### KV Cache effect

append-only：它是一条普通的 tool 角色消息，接在该次 tool_call 之后，不改动此前任何 token。本模块的时限表与 `TOOL_TIMEOUTS` 只影响是否产生这条消息，不影响前缀。

### `<available_resources>`（仅当本轮有 @-mention）

#### What the model sees

一个框里两种条目：`<resource … />` 是可取的文件，`<asset …>…</asset>` 是资产库实体（角色 / 场景 / 道具 …），正文就是它的一致性提示词。资源条目全部排在资产条目之前——资产用 `primary_resource_id` 指回其中一条，模型读到指针时那张图已经在页面上了。真实渲染结果逐字如下：

```markdown
<available_resources>
  <resource id="9001" kind="image" mime="image/png" scope="team:42" size="793KB" updated="2026-09-01" name="lin-wei-ref.png" />
  <asset id="7001" type="character" name="Lin Wei" scope="42" primary_resource_id="9001" has_image="true" loadout="5001">a tall woman in her mid 30s, short black hair, red wool scarf</asset>
</available_resources>

Use the ResourceFetch tool to load any of these on demand:
  ResourceFetch(resource_id, mode?, args?)
  - mode for video: summary (default) | transcript | frames
    frames returns evenly sampled still images from the video; args.frames sets how many (default 6, max 12)
  - mode for doc: excerpt (default) | full
  - mode for pdf: excerpt (default) | page (args.page)
  - mode for image: omit (returns image part)
  - mode for audio: transcript (default)
  - an <asset> entry carries its consistency prompt as the body; fetch its picture with ResourceFetch(primary_resource_id, mode=image) when has_image is true
```

最后那行 `- an <asset> entry …` **只在本轮有资产时出现**；纯资源的轮次与 P5 之前逐字一致。

`status` 属性只对 video/audio 出现——对一个 markdown 文件说 `transcript:none` 是模型要读过去的纯噪声。（源码注释里说它还会 churn cache fingerprint；实际上这一块不进任何一个指纹，那句话指的是渲染文本本身。）

资产条目的三条形状约定：

- **图片不内联。** `<asset>` 只给 `primary_resource_id`，那是一条普通 `resources` 行，模型自己决定要不要花一次 `ResourceFetch` 去看（P5 裁决 F）。
- **`has_image` 是独立字段，写作 `"true"` / `"false"`。** 音频资产有主资源却没有图，两者必须能分开——不然模型会对着 `.wav` 调 `mode=image`。Python 的 `True` 在属性里是另一个 token，拼写由渲染层负责，不由 `ChatAssetRef` 负责。
- **`primary_resource_id` / `loadout` 为空时整个属性省略，不渲染成空串。** `primary_resource_id=""` 读起来仍然像一个 id，模型会拿它去调用然后失败；属性缺席 + `has_image="false"` 才是「没有图可取」。

**所有属性值都过 `escape_frame_attr`，`<asset>` 的正文过 `escape_frame_prose`**（`name` 是用户可自由改的，一致性提示词整段都是用户写的）；见 `../../../boundary/frame_markers.py`。

⚠️ **正文用的是 `escape_frame_prose` 而不是 `escape_frame_body`，这一块是例外**（终审 I1）。`escape_frame_body` 的契约是「只中和 `OWNED_FRAMES` 的闭合标记，别的标签原样保留」——`</div>`、`</think>`、`<b>` 照原样到达模型，因为剧本可能合法地谈到它们。那个契约对**自由排版**的框是对的，对**逐行目录**的这一块不成立：

- 这个框是**按行读**的，一行一条 `<resource … />`；
- 一致性提示词是这里唯一「用户写的 + 无长度自然上限 + 允许换行 + 不转义 `<`」的模型可见值。

两者相乘的结果是**可以伪造一条兄弟目录行**，缩进、属性顺序、自闭合形式与我们自己写的那行**字节级无法区分**。它关不掉框（`</available_resources>` 仍被中和），伪造的 id 也进不了 ResourceFetch 白名单，所以不是提权；它伪造的是这个框唯一要断言的东西——**这些条目是系统列出来的**。

`escape_frame_prose` 两手都要：**压平** `\r\n\t`（杀掉伪造的「行」）+ **实体转义** `&<>`（杀掉留在同一行里的伪造「元素」）。引号不转义——这是元素正文不是属性值。两条各有独立的突变验证。

⚠️ **`asset` 刻意不在 `OWNED_FRAMES` 里**（P5 裁决 A，spec §6.5 已按此修订）：它是我们拥有的框**内部的元素**，不是框；把它登记成框会把提示词里每一次合法提到该词都糟蹋掉。`tests/services/ai/prompts/test_frame_escape_wiring.py` 的 `ignore` 名单记着这条理由。⚠️ 裁决 A 当时说的代价是「字面 `</asset>` 会截断它自己那一条」——**终审 I1 之后这个代价降到零**：正文的角括号全被实体转义，那个字面量关不掉任何东西。这两件事不矛盾，它们说的是两层（`OWNED_FRAMES` 是 `escape_frame_body` 的词表，正文防护来自 `escape_frame_prose`）。

#### Token effect

与本轮 @-mention 的条数成正比：

- **每条 `<resource … />`** 约 30-60 token。
- **每条 `<asset …>…</asset>`** = 属性约 25-40 token + 一致性提示词。提示词是这里唯一无自然上限的输入（资产自身提示词 + loadout 的 `prompt_extra` + 每个链接的服装 / 道具 / 场景的提示词拼起来），所以在 `app/services/assets/chat_ref.py` 里**硬截断到 `MAX_CONSISTENCY_PROMPT_CHARS = 600` 字符**。⚠️ `" [truncated]"` 标记是**追加在上限之外**的，被截断的条目正文是 **612** 字符而不是 600——按常量本身算预算会每条少算 12 字符。600 字符在纯 ASCII 下约 150 token，全中文时可以接近 600 token，估上限要按后者。
- **`<asset>` 条数有界，`<resource>` 条数无界。** 三个上限**互不相同**，别读串：
  - **`asset_ref` 每轮最多 8 条**——`ai_library_chat_service.MAX_ASSET_REF_ATTACHMENTS`（终审 I2）。超出的**不解析、也不静默丢**：每条产出一个 `attachment_limit_exceeded` 的 `attachment_failures` 条目，index 是它在**调用方完整附件表**里的位置。按**位置**计数不按去重后的 id：否则调用方发 200 份同一个 id 仍然要占 200 条请求体，而请求体正是要封的东西。
  - **`resource_ref` 不设上限**，刻意的：不论多少条都是**一次批量查询**，而 `asset_ref` 每条约 5 次串行往返——封它是拿走一条能用的路而换不到任何成本收益。所以这一块的最坏体积是「8 条资产 x 单条上限 + 客户端发的资源条数 x 每条 30-60 token」。
  - **binary 桶的 `chat_attachment_resolver.MAX_ATTACHMENTS_PER_TURN = 8`** 与第一条**数值相同但是两个常量**（封的成本不是一回事：字节 vs 数据库往返），而且 ⚠️ 它是**静默截断**（见下面 Known Limitations）。
  - ⚠️ `MAX_ASSET_REF_ATTACHMENTS` 在 TS 侧有**镜像**（`frontend/components/chat/attachmentLimits.ts`），因为横幅文案要把这个数插进去给用户看。`tests/services/ai/chat/test_attachment_limit_frontend_mirror.py` 读那个文件比对，两侧不一致即转红；文案里必须写 `{{n}}` 不许写字面 8，那条也钉住了。

**资源正文与资产主图都不在这里**——这一块只是目录，正文/图片要模型主动调 `ResourceFetch` 才进上下文。没有 @-mention 也没有资产时整块返回空字符串，这样无 mention 的轮次系统消息缓存键不变。

#### KV Cache effect

它拼在 request instructions 里，位于缓存边界**之后**，所以逐轮变化不影响稳定前缀——资产条目同样在边界之后，改一个资产的提示词、它的 loadout 或它的链接，只动这段后缀。

本模块不缓存资产内容：每轮从活数据重新组装，所以资产表里的编辑对下一条消息立即可见，没有失效步骤。

## Known Limitations and Deferred Work

- **身份三段无长度上限**。一个 `agent_md` 写到 200k 字符的 agent 会把每一轮请求都撑爆，而且因为它在缓存边界之前，代价逐轮重复。skill 正文有 64k 上限（`../skills/`），身份文档没有对应的护栏。
- **两个指纹都不覆盖 `request_instructions`、`<available_resources>` 与 `# Runtime` 行**。它们是缓存键，不是"这次请求的输入摘要"——`_dynamic_fingerprint()` 只加了记忆内容，因为缓存隔离只需要防跨用户串味。**别拿它判断"两轮输入是否相同"**：改了 request instructions、换了 @-mention 的资源、跨了一分钟，动态指纹都可能一模一样。
- **`Skill` 的 inputSchema 现在同时承载 skill 装载、内建 `todo`、内建 `task` 三类参数**，靠 description 里的「skill='…' only」区分。这是裁决不是遗漏：三者共用一个工具名是既有契约（`AgentRunner` 按 `tool_name == "Skill"` 分派），拆成三个工具会改动分派处与所有 pin。代价是 schema 约 300 token 且每次请求都带。
- **`_build_tools()` 只决定给模型看什么，不是执行期的强制**。写权限的真正拦截在 `AgentRunner._dispatch_screenwriting`；把这里的过滤当成权限校验是 A4 评审记过的错误。
- **binary 附件超过 8 条时被静默截断**（`chat_attachment_resolver.py` 的 `capped = requests[:MAX_ATTACHMENTS_PER_TURN]`）。第 9 条起既不解析也**不产出 `attachment_failures` 条目**，只写一条 warning 日志——用户贴了 12 张图，其中 4 张从未到达模型而界面上没有任何提示。与「触发路径必须类型化失败回显」相悖，是 P5 之前就存在的缺口。⚠️ `asset_ref` 那一侧**不是**这样（`MAX_ASSET_REF_ATTACHMENTS` 超出即类型化回显）——两者数值相同、行为相反，别读串。
- **`resource_ref` 的条数仍然没有服务端上限**，这是**裁决而不是遗漏**：不论多少条都是一次批量查询，封它只会拿走一条能用的路（`@` 选择器不限制暂存条数）。代价是系统消息里 `<resource>` 那一半的体积仍由客户端决定——今天的写方只有我们自己的 composer UI，直接打 API 的调用方可以把目录撑长。真出现问题时该封的是**渲染出来的字符数**，不是条数。
- **issue 回复框不支持资产引用**（P5 裁决 H）。`frontend/components/Todolist/IssueReplyBox.tsx` 走的是另一条发送路径，本期只接了聊天面板一侧——「两个入口只接一个」这类缺口在本仓已经出现过多次，所以显式记在这里而不是留在源码 TODO。
- **资产的主图可能「有」却「取不到」，此时条目被降级渲染**。`has_image` 由解析器用**系统作用域**读 `resources` 算出（资产的文件行是经资产可读的，不是经调用者的 team 成员关系），而 `ResourceFetch` 只认本轮可访问集合——两者会不一致，最典型的是系统预设资产，它的文件落在用户不属于的 scope 里。`ai_library_chat_service._merge_asset_primaries` 在这种情况下把条目改写成 `has_image="false"` 且**省掉 `primary_resource_id`**（即上面那条「没有图可取」的形状），并向用户回一条 `asset_no_primary_image`。宁可少给一张图，也不给模型一个用了就失败的 id。
- **`audio` 资产的主资源取不到时，用户端没有回显**（同上那条的副作用）。裁决 C 的 reason 词表里，`asset_no_primary_image` 明确只对「本该有图的类型」成立，所以音频只写日志、不进 `attachment_failures`——模型仍拿到一致性提示词，只是听不到那段音频，而用户不会被告知。要补就得再给词表加一个值（词表现在是五个：四个来自 `asset_ref_resolver`，第五个 `attachment_limit_exceeded` 由 chat service 的 `asset_ref` 条数上限产出）。
- **`link_injection` 失败会落显式占位块**，不是静默跳过——但占位块的文案目前只有英文，与 UI 的 i18n 口径不一致。
