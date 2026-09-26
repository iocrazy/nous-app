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

`<available_workers>` 有界且**双重条件**：只在 `FEATURE_WORKFORCE_DELEGATE` 打开时才去查、才渲染，条目数 = `ai_agents.persistent=true` 的行数（2026-09-10 起为 3：`summarize` / `analyze` / `coordinator`，由 seed frontmatter 声明）。一条约 20–30 token（slug + description + model），加上固定的一段说明句，量级是**每个 agent 每次请求百余 token**。

⚠️ 这个块**不看当前 agent 有没有真的会用 Delegate**——只要开关开着，每个 agent（含 ChatPanel、17 个 seed、全部用户自建）都会看到同一份清单。它与工具本身共用一个开关，所以「模型看得到清单」与「模型调得动工具」永远同真同假；曾经它是无条件渲染的，等于给永远不会 delegate 的 agent 付永久 token（Task 7a 评审 I3）。

#### KV Cache effect

**稳定前缀 + 每轮替换的尾部**，边界就是 `<!-- CACHE_BOUNDARY -->`。

- 边界**之前**（身份 / skills / workers）逐轮不变，构成可复用前缀。改 agent 任一份文档、增删一个绑定 skill、增删一个 persistent agent，都会让前缀失效——这是正确的。
- **`FEATURE_WORKFORCE_DELEGATE` 翻一次 = 全站前缀失效一次**。开关一开，每个 agent 的系统消息多出 `<available_workers>` 块，`_prefix_fingerprint()` 随之改变，所有 agent 的 provider 前缀缓存**一次性**全部作废；关掉时同样。这是把这个能力打开该付的、可见的代价，不是漂移——它只发生在翻开关的那一刻，之后回到稳定前缀。往 `ai_agents.persistent` 增删一行也会让所有 agent 的前缀失效一次（同一段进指纹），所以 persistent 名单不该被当成日常可调的配置。
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

2026-09-26 fh4 E3 加第四条：整棵根树的活跃子 agent 已到上限（`MAX_ACTIVE_SUBAGENTS_PER_TREE`，默认 8，env 可覆盖）时，`Skill(task)`（同步、后台、`tasks` 扇出一样）回的是标准失败信封再多三个键，扇出装不下就**整体拒绝**、一个都不启动：

```
{"status": "failed", "error": "tree_capacity_exceeded", "limit": 8, "active": 8, "requested": 1, "summary": "", "sub_run_id": null, …}
```

`Delegate` 在同样情形回 `{"error": "tree_capacity_exceeded", "limit": …, "active": …, "requested": 1, "agent_slug": …}`。拒绝不写 `subagent_spawned`、不建子 run 行，模型据 `active` / `limit` 决定是等已派出的孩子回来还是自己做。Token effect 与 KV Cache effect 同其余工具结果（一次几十 token，append-only）。

`op` / `items` / `id` 只对 `skill="todo"` 有意义，2026-09-06 起才声明——此前模型只看得到 `skill` 与 `file`，内建 todo 的参数全靠猜：doubao lite 把 `"?op=replace&items=…"` 塞进 `file` 连错四次，整轮没有一个 todo 快照，任务卡的 n/m 也就从未出现。模型用不了它没被展示的参数，这不是提示词问题。

#### Token effect

固定约 300 token，不随 agent 配置增长；每次请求都带（provider 把 `tools` 当请求的一部分计费）。

#### KV Cache effect

`tools` 在多数 provider 侧位于系统消息之前的前缀里，所以**改这份 schema 的任何一个字都会让全部 agent 的前缀一次性失效**——这是一次性的，之后逐轮不变。本模块不为它计指纹（`_prefix_fingerprint()` 不吃 tools），因为它对所有 agent 恒等，没有跨 agent 串味的问题。

### 工具 schema：`Delegate`（仅当 `FEATURE_WORKFORCE_DELEGATE` 开启）

#### What the model sees

`_delegate_tool_spec()` 产出，`FEATURE_WORKFORCE_DELEGATE` 开启时由 `_build_tools` 追加在 `Skill` 之后（与 agent 是否绑定了 skill 无关），随每次请求以 `tools` 参数发出。整份原样：

```json
{
  "type": "function",
  "function": {
    "name": "Delegate",
    "description": "Hand off a sub-task to another persistent agent. The target picks the task from its inbox on the next dispatch tick. Fire-and-forget by default; use status_query to check progress later. Use this for parallel work, specialised expertise, or when you need a different agent's persona/skills.",
    "parameters": {
      "type": "object",
      "properties": {
        "agent_slug": {
          "type": "string",
          "description": "Slug of the target persistent agent (see <available_workers>). Target must have ai_agents.persistent=true."
        },
        "prompt": {
          "type": "string",
          "description": "The task description / instruction for the target agent. Be specific."
        },
        "title": {
          "type": "string",
          "description": "Optional short title for the task (shown in worker UI)."
        },
        "priority": {
          "type": "integer",
          "description": "Inbox priority 1-10 (higher = sooner). Default 5."
        },
        "dedup_key": {
          "type": "string",
          "description": "Optional dedup key — repeated calls with the same key are folded while the message is unread."
        },
        "await": {
          "type": "boolean",
          "description": "If true, block this turn until the target finishes and embed the result content in the response. Default false (fire-and-forget). Use sparingly: holds the caller's agent for up to await_timeout_seconds."
        },
        "await_timeout_seconds": {
          "type": "number",
          "description": "Max seconds to block when await=true. Default 60, capped at 180. Ignored when await=false."
        }
      },
      "required": [
        "agent_slug",
        "prompt"
      ]
    }
  }
}
```

调度在 `AgentRunner`（工具名在 `SUPPORTED_TOOLS` 里），执行体是 `services/workforce/delegate_tool.py`。模型拿回的结果形状：

- 默认（`await` 省略或 `false`）：`{"delegated_to", "agent_id", "inbox_message_id", "outbox_message_id", "depth", "await": false, "status": "queued", "note": "Task is queued. …"}`。
- `await=true`：同一组基础键再加终态 `{"status": "done" | "failed" | "cancelled", "waited_seconds", "result", "task_id", "run_id"}`（失败/取消另带 `error_code` / `error_message`），或等满 `await_timeout_seconds` 仍未终态时的 `{"status": "timeout", "waited_seconds", "last_lifecycle", "note"}`。
- 拒绝：`{"error": "<一句话>", …}`，部分带 `limit` / `depth` / `retry_after_seconds` 等附加键（缺 `agent_slug` / `prompt`、未知 slug、委派给自己、每个调用方 agent 60 秒 30 次的速率上限、深度上限 3、A→B→A 环路、入队失败等）；树容量上限的形状见上面 `Skill` 块。

#### Token effect

schema 固定约 350 token（紧凑 JSON 1487 字符），开关开着就每次请求都带，不随 worker 数增长——worker 名单在系统消息的 `<available_workers>` 里，不在这里。结果是一次几十 token 的 JSON，`await=true` 时 `result` 是目标 agent 的回答原文，长度由对方决定、本工具不截断（进上下文后受 runner 的单条消息上限约束）。

#### KV Cache effect

与 `Skill` 同理：`tools` 在多数 provider 侧位于系统消息之前的前缀里，改这份 schema 的任何一个字、或翻 `FEATURE_WORKFORCE_DELEGATE`，都会让全部 agent 的前缀一次性失效（翻开关还同时改动 `<available_workers>`，见上面系统消息块）。结果是 append-only 的工具消息。

### 工具结果：内建 todo 的 `<todo_list>`（`Skill(skill="todo")` 的返回值）

#### What the model sees

`skill_tool_service` 把 `AgentTodoList.render_for_prompt()`（`agent_framework/agent_todo.py`）放进 todo 工具结果的 `prompt` 字段。框的形状（状态符号 ⏸ / 🔄 / ✅，进行中的条目显示 `active_form`）：

```
<todo_list>
  ✅ 1. <条目文字>
  🔄 2. <进行中条目的 active_form>
  ⏸ 3. <条目文字>
</todo_list>
```

框名 `todo_list` 登记在 `../../../boundary/frame_markers.py` 的 `OWNED_FRAMES` 里（2026-09-25，T7）。条目文字是模型自己写的，但可以被它本轮读到的任何外部内容带偏，所以每条都过 `escape_frame_prose`：字面 `</todo_list>` 关不掉框，换行被压平成空格、伪造不出第二条（比如一条假的 ✅），`&<>` 实体转义。代价是条目里的 `&` 会显示成 `&amp;`。

#### Token effect

每条一行，上限 `MAX_TODO_ITEMS`（30）条；只在模型调用 todo 工具的那一次结果里出现，逐次调用各自一份。

#### KV Cache effect

工具结果是 append-only 的对话消息，不改更早的 token；本模块的改动不影响系统消息前缀。

### 收件箱消息框 `<inbox_message>`（步骤边界注入的 user 消息）

#### What the model sees

`runner/inbox.py::render_inbox_message` 在步骤边界把每条领到的收件箱行渲染成一条 user 消息。我们拥有这个框（登记在 `OWNED_FRAMES`），属性走 `escape_frame_attr`、正文走 `escape_frame_prose`：

```
<inbox_message kind="steer" at="2026-09-05T00:00:00+00:00">
{正文}
</inbox_message>
```

`kind="subagent_result"`（2026-09-10，harness 二期 2b-2；2026-09-26 fh4 E2 加 `status` / `reason`）多带四个属性，正文只有子 agent 的 summary：

```
<inbox_message kind="subagent_result" at="…" child_run_id="52" subagent_type="librarian" status="success" reason="producer">
found three docs
</inbox_message>
```

`child_run_id` 是给父 agent 下一轮 `Skill(skill="task", child_run_id=…)` 续聊用的。`status` 是结果本身（`success` / `failed` / `cancelled`），`reason` 是谁结束了它（`app/services/workforce/settle.py::SettleReason`）：`producer` 子 agent 自己跑到头（含它自己崩溃）、`kill` 被外部取消、`teardown` 所在 worker 被有意停机、`lost` 所在 worker 死了。两者正交 —— `status="failed" reason="lost"` 说的是「没有答案」，`status="failed" reason="producer"` 说的是「答案是失败」，模型据此决定重派还是换思路。fh4 之前框里没有 `status`，失败的子 agent 只能从正文猜；worker 死掉的子 agent 根本不进收件箱。崩溃的子 agent 正文是错误文本（以前是空框）。fh4 之前入库的行没有 `settle_reason`，渲染为 `reason=""`。信封里其余字段（`cost_cents` / `tokens_used` / `description`）**刻意不进框** —— 模型无法据它们行动，进框只是白烧 token。

同一个后台任务的结果只进收件箱一次（`dedupe_key=subagent-result-<task_id>`，mig 462 唯一索引）：worker 正常完成、DBOS 重放的 step、reaper 关闭丢失的子 agent，三个写方收敛到第一个写入的那一行，所以模型不会看到同一个孩子的两个框、也不会看到互相矛盾的两个 status。

正文取值顺序是 `content.text` → `content.body`（2026-09-23 FH2 T1，与 `claimed_event_content` 同序）。在此之前定时唤醒的 steer 是 `{"text", "source"}`、没有 `body`，正文会落到整行 JSON，模型读到的是 `{"text": "…", "source": {"kind": "schedule", …}}`。

带附件的 steer（评论在 root run 忙时被转进收件箱）在正文之后、闭合标记之前多一段附件清单；**没有附件就一行都不加**，上面两个例子的字节不变：

```
<inbox_message kind="steer" at="…">
see these
Attached to this message (listed for reference; the files are not loaded into this turn):
[attachment 1] kind="resource_ref" name="shot-04.png" resource_id="353004118021504"
[attachment 2] kind="output_ref" title="Draft v2" ref_kind="script" ref_id="352701793895008" version="2"
</inbox_message>
```

每行只带白名单字段、按固定顺序、缺的不写：`kind` / `name` / `title` / `resource_id` / `asset_id` / `loadout_id` / `ref_kind` / `ref_id` / `version`。只列 `str` / `int` 值（嵌套 dict、列表、bool 跳过——免费形状的收件箱 API 能塞任意值，`str()` 出来只是 Python repr 噪声）；一个白名单字段都不剩的附件整条不列，编号在过滤之后再数，所以不会出现空的 `[attachment N]`，全被过滤时标题也不加。值全部走 `escape_frame_attr`（文件名是用户写的，引号与 `<>&` 都会被转义，闭合标记伪造不了）；正文经 `escape_frame_prose` 压成一行，所以用户文本伪造不出**独立的**清单行（它能照抄措辞，但只会待在正文那一行里）。`url`（文件系统路径）与 `data_url`（字节）**刻意不进框**。只是文本清单：像素没有注入，**清单里的 id 本轮也取不到**——`ResourceFetch` 的白名单是本轮请求开始时由请求自带的引用算定的，所以标题刻意不许诺「可以打开」（见 Known Limitations）。

**重投递（2026-09-26 fh5 T5）**：一条领到的行被注入了一次没有回答的 LLM 调用（调用抛出、进程死掉、worker 停机）时，它会回到队列，下一次领取时**逐字节相同**地再注入一次（`at` 仍是入队时间；计数器 `content.redelivered` 是簿记，JSON 兜底正文里也会被剔掉）。所以模型可能在后一个回合里第一次真正读到一条更早的 steer。只有两种情况不重投：run 被取消（取消即「别再做」）和 issue 已 done / cancelled / 隐藏。同一条行最多重投 2 次，第 3 次改为过期并记 ERROR —— 见 `app/repositories/agent_run_inbox_redelivery.py`。

#### Token effect

每条一个框，长度就是那条消息的正文长度，外加每个附件一行（几十 token，不含文件内容）。`subagent_result` 只放 summary（加 fh4 的两个短属性，约 10 token），所以一次后台子 agent 的回执通常是几十到几百 token，而不是整个信封的 JSON。领取本身有条数上限（见 `agent_run_inbox_repository.claim`），所以单个步骤边界注入的量是有界的。重投递让同一条行最多被注入 3 次（首投 + 2 次重投），但每次都是在前一次调用没有回答之后。例外：崩溃类收口按「该步没有 `step_end` 事件」判断没回答，而 `step_end` 的写入是 best-effort —— 它恰好没写进库时，模型会把一条已回答过的消息再读一遍（受同一个上限约束）。

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
  "description": "Schedule a one-time wake-up for this issue; when it fires you will receive the note as a message. Use it to wait for long external work instead of polling. At most 3 per run, and at most 5 consecutive wake-ups per issue without a user reply.",
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

调用成功后模型收到 `{"schedule_id": "<uuid>", "fire_at": "<ISO>"}`，**本轮照常继续**（与 `AskUser` 不同，它不停靠）。拒绝一律是工具结果不是异常，模型可以据此改时间重试：`at or delay_minutes required` / `note is required` / `at must be an ISO-8601 timestamp` / `delay_minutes must be a whole number of minutes` / `fire_at must be in the future` / `fire_at must be within 30 days` / `too_many_wakeups`（每个 **run** 最多 3 次——按 `payload.run_id` 在表上数，所以跨轮次也是 3 次不是每轮 3 次；被拒的调用不计数）/ `{"error": "too_many_wakeups_on_issue", "limit": 5, "hint": "wait for the user to reply; stop scheduling"}`（2026-09-23 FH2 T2：每个 **issue** 自上次有人说话以来最多连续 5 次 agent 唤醒——每次唤醒起的是新 run、per-run 预算随之重置，单靠上一条挡不住链条。按 `user_schedules` 里该 issue 的 agent 行数、锚点是 issue 会话里最后一条**人**写的 user 消息（`body.meta` 带 `source` / `subissue_barrier` / `pipeline_relay` 任一键的系统写入与续跑 nudge 不算人），没有就用 `issues.created_at`；人一评论就归零；在 per-run 检查之后判，拒绝同时记 WARN）/ `ScheduleWakeup failed: <ExceptionClass>`。未注册该工具的轮次上误调用得到 `ScheduleWakeup is not available on this turn — it only applies while working an assigned issue.`。

到点时模型看到的**不是**这个工具的返回，而是一条普通用户消息（issue 空闲）或收件箱里的 steer 框（run 在跑，或 issue 停在 needs_input 闸门上等人回答——后者由用户回答起的那一轮在首个步边界领取）——正文就是 `note` 原文。

#### Token effect

紧凑 JSON 735 字符、约 180 token（口径：`json.dumps(schedule_wakeup_spec(), separators=(",", ":"))` 实测字符数，按约 4 字符/token 折算；FH2 T2 描述加了一句上限说明），恒定，不随 agent 配置或对话增长。只在 issue 根 run 的 `tools` 数组里；聊天路与子代理 run 上完全不存在，那两条路的请求体一个字节都不变。

#### KV Cache effect

`tools` 数组是稳定前缀的一部分，issue run 与 chat run 因而是两个前缀族——**本模块任何改动（描述、参数名、参数顺序）都会让 issue 路的前缀复用一次性失效，chat 路不受影响**。本模块不为它计指纹（对所有 agent 恒等）。⚠️ provider 端是否真的命中缓存不在本模块契约内。

### 工具 schema：`SetAcceptanceCriteria`（仅 issue 根 run）

#### What the model sees

与 `ScheduleWakeup` 同一注入点（issue 触发且已知 `issue_id`），tools 里追加在 `ScheduleWakeup` 之后、`AskUser` 之前。稳定字面量（`json.dumps(set_acceptance_criteria_spec(), indent=2)` 原样）：

```json
{
  "type": "function",
  "function": {
    "name": "SetAcceptanceCriteria",
    "description": "Record the acceptance criteria for this issue before you start working, when the issue has none yet. Write concrete, checkable outcomes: how many scenes or shots, whether images are generated, a word-count range. A person's own criteria are locked and cannot be changed by you. Call this at most once per turn.",
    "parameters": {
      "type": "object",
      "properties": {
        "criteria": {
          "type": "string",
          "description": "The completion criteria, as a short checklist a reviewer can verify. At most 4000 characters."
        }
      },
      "required": [
        "criteria"
      ]
    }
  }
}
```

工具结果是 `{"ok": true, "criteria": "...", "source": "agent"}`，或类型化拒绝 `{"error": "criteria_required" | "criteria_too_long" | "criteria_locked" | "criteria_already_set" | "criteria_write_failed"}`（`criteria_locked` / `criteria_already_set` 附带当前 `criteria`，`criteria_too_long` 附带 `max_chars`）。人写的标准（`source='user'`）锁定；agent 自己先前的提议可以被它替换；每回合最多一次成功调用。指令句是 `ACCEPTANCE_CRITERIA_INSTRUCTION`，逐字贴在 `FinishIssue` 块里；它和本工具在同一个注入块，只跟着本工具出现。

#### Token effect

schema 约 130 token，固定。结果体 ≤ 4000 字符（`ACCEPTANCE_CRITERIA_MAX_CHARS`）+ 十几个 token 的外壳。每回合最多一次成功调用。

#### KV Cache effect

只在 tools 列表里，与 `FinishIssue` / `ScheduleWakeup` 同族：issue 轮次与聊天轮次本来就是两个缓存族，本工具不再分裂族。改这份 schema 的任何一个字会让 issue 族的 tools 前缀失效一次，之后逐轮不变。

### 工具 schema：`LibrarySearch`（请求的 `tools` 参数，两条路都有）

#### What the model sees

`library_search_spec()`（`../tools/library_search_tool.py`）产出的 function 描述，2026-09-23（向量分层 spec §4.5 的 PR 5）起注入。资源库聊天路**无条件**挂在 `AskUser` 之后（issue 触发同样会带上）；会话 @agent 路只在 agent 有 `read_team_resources` 时挂，与 `ResourceFetch` 同一道门。稳定字面量：

```json
{
 "type": "function",
 "function": {
  "name": "LibrarySearch",
  "description": "Search the caller's own resource library (saved videos, images and other media) by keywords or a natural-language description. Returns each hit's resource_id, the layer that matched (text = keyword match in title/description/tags, semantic = meaning match, visual = a frame of the video looks like the description) and a score. A visual hit carries shot = {shot_id, start_ms, end_ms}: the moment to play from. Every other hit has shot = null. Run several short queries rather than one long sentence.",
  "parameters": {
   "type": "object",
   "properties": {
    "query": {
     "type": "string",
     "description": "Keywords or a short description."
    },
    "layers": {
     "type": "array",
     "items": {
      "type": "string",
      "enum": [
       "text",
       "semantic",
       "visual",
       "camera",
       "transcript"
      ]
     },
     "description": "Only return hits from these layers. Omit for all."
    },
    "limit": {
     "type": "integer",
     "minimum": 1,
     "maximum": 20,
     "default": 8
    }
   },
   "required": [
    "query"
   ]
  }
 }
}
```

成功时模型收到结构化 JSON（不是散文）：

```json
{"query": "handheld tracking shot", "hits": [{"resource_id": "<snowflake str>", "media_id": "<snowflake str>", "platform_id": "…", "title": "≤200 chars", "description": "≤200 chars or null", "layer": "text|semantic|visual", "score": 0.6123, "author": "…", "created_at": "ISO", "shot": null}], "legs": {"text": 1, "semantic": 1, "visual": 0}, "vector_leg": "ok", "visual_leg": "ok", "reranked": false, "total": 2, "truncated": false}
```

走哪条腿由 `layers` 决定：不传（或传空数组）与含 `text` 时走 hybrid（文本 + 语义 + visual 三条腿）；只含 `semantic` 不含 `text` 时直接跑文档向量腿（`SearchService.semantic_only`），只含 `visual` 时直接跑镜头帧腿（`SearchService.visual_only`），因为 hybrid 里文本命中填满一页时两条向量腿都会 `skipped_full_page`；只含未建成的层（`camera` / `transcript`）时什么都不调，返回空。`visual` 命中带 `shot = {"shot_id": "<snowflake str>", "start_ms": 41000, "end_ms": 52000}`（该播放的时刻），其余命中 `shot` 为 null；`legs` 里 `visual` 键只在那条腿真跑了时出现。`legs` 按**最终给模型的命中**重算，键表示「这条腿跑了」，被 `layers` 滤掉的层不出现。`query` 超过 500 字符（与搜索 API 的 schema 同一上限）被截断而不是拒绝，此时 `truncated: true`。

文本腿的查询归一化：只删「至少一侧是 CJK 字符」的空白（`search_service.normalize_query_spaces`）——「日本 夜景」按「日本夜景」匹配，「Morning Routine 2024」保留空格。整个查询是一个子串，所以 skill 教模型一次只放一个关键词或一个完整短语。

失败一律是工具结果不是异常：`query must be a non-empty string` / `layers must be a list of layer names` / `unknown layer(s): …` / `library search failed: <ExceptionClass>`；会话路上没权限是 `this agent is not permitted to search the library`；未注入 handler 的轮次是 `LibrarySearch is not available for this turn.`。

#### Token effect

schema 紧凑 JSON 约 830 字符、约 210 token，恒定，每次请求都带。结果按命中条数线性增长、有界：每条 title / description 各截到 200 字符，`limit` 上限 20，最坏约 13k 字符（约 4k token），默认 8 条约 1.5k token。结果进工具消息，随对话历史累积，由 `ContextCompactor` 与其它工具结果一样压缩。

#### KV Cache effect

`tools` 数组是稳定前缀的一部分：本 schema 的任何改动（描述、枚举、参数顺序）会让资源库聊天路的前缀复用一次性失效，之后逐轮不变；会话 @agent 路按有无 `read_team_resources` 分成两个前缀族（本来就因 `ResourceFetch` 而分开，这不新增失效）。工具结果是 append-only 的工具消息，不回写更早的 token。本模块不为它计指纹（对所有 agent 恒等）。⚠️ provider 端是否真的命中缓存不在本模块契约内。

### 工具结果：超时（任何工具，两条路都有）

#### What the model sees

2026-09-09（harness 二期 2b-1 Task 4）起每个工具调用都有墙钟上限（`runner/tool_timeouts.py`：Skill 30s / ResourceFetch 200s（高于其自身 180s 的抽帧截止，让它自己的「frame extraction timed out」结果仍可达）/ GenerateImage 600s / GenerateVideo 60s（#2398 起只提交，渲染在 workflow 里跑）/ Delegate 900s / `skill.*` 同 Skill、`agent.*` 同 Delegate、其余 MCP 名 120s / 其他 60s；**AskUser、FinishIssue 不计时**——它们只写一行 transcript，切断会留下 runner 没看见的停靠问题；`config.yml TOOL_TIMEOUTS` 按工具名覆盖）。超时**不是** run 停止：模型收到的是那次调用的工具结果，正文是这一行 JSON（`timeout_s` / `elapsed_s` / `tool` / `message` 里的数值随实际值变化，其余字面量固定）：

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

### `<referenced_outputs>`（仅当本轮有被引用的产出版本）

#### What the model sees

人在 issue 回复里 @ 了一个**已登记产出的某一版**（三期 3a Task 4）。框里只有坐标，**没有内容**。真实渲染结果逐字如下：

```markdown
<referenced_outputs>
  <output kind="script_shot" ref="9" version="2" title="S3 · Shot #1"/>
  <output kind="generated_media" ref="88213" version="1"/>
</referenced_outputs>

Each <output/> above is a CITATION the human made — one specific version of an object, not its content. The content is NOT in this context: read the object itself with the tools you already have.
```

三条形状约定：

- **框里每一行都是自闭合的 `<output/>`，没有正文也没有子元素。** 这不是「内容写得比较少」，是契约：引用给的是坐标，正文要模型自己用既有工具去读。`tests/services/ai/prompts/test_referenced_outputs_frame.py` 按行正则钉住这一条——把内容塞进框的实现在那里转红。
- **`kind` 是产出的四类之一**（`generated_media` / `script_shot` / `script_scene` / `script_chapter`，见 `../../deliverables/kinds.py`）。四类之外在发帖口就被拒，进不到这里。
- **`title` 为空时整个属性省略，不渲染成空串。** `title=""` 读起来像「它的标题就是空字符串」，缺席才是「登记时没给标题」（与 `<available_resources>` 的 `primary_resource_id` 同一条规则）。标题是**发帖那一刻**从登记表抄下来的快照，对象事后改名不会追改这条引用——引用记录的是人当时指的那个东西。

**所有属性值都过 `escape_frame_attr`**，包括看起来机器生成的那些：`title` 来自登记行，而那个值本身是模型或用户写的。框名 `referenced_outputs` 登记在 `../../../boundary/frame_markers.py` 的 `OWNED_FRAMES` 里，所以标题里的字面 `</referenced_outputs>` 关不掉框，换行也被压平、伪造不出第二条目录行。

**本轮没有引用时整块返回空字符串**，系统消息与这个框存在之前逐字相同——全文 pin 因此没有 diff。若哪天 pin 有了 diff，说明框被无条件渲染了，那是缺陷，不是刷新快照的理由。

#### Token effect

- **每条 `<output/>` 约 25-40 token**。标题在登记口就被截到 `TITLE_MAX = 120` 字符（`../../deliverables/kinds.py`），纯 ASCII 下约 30 token，全中文最坏接近 120。
- **条数上限 8**（`../chat/output_ref_resolver.py` 的 `MAX_OUTPUT_REF_ATTACHMENTS`）。封的是**数据库往返**——每个被引对象一次 `lineage_for`（同一对象引多版合并成一次）。⚠️ 与 `MAX_ASSET_REF_ATTACHMENTS` 数值相同但是两个常量，两者封的成本不是一回事。超出是**类型化 400**（`output_ref_limit_exceeded`），不是静默截断——后者正是 binary 桶那条 Known Limitation 记着的缺口。
- **不随产出大小增长。** 引用一段五万字的剧本和引用一行分镜花的 token 一模一样，因为框里本来就没有内容。这是选这个形状而不是「把被引版本贴进上下文」的全部理由。
- 框后那一句固定文案约 45 token，同样只在本轮有引用时出现。

#### KV Cache effect

它和 `<available_resources>` 一样**拼在系统消息尾巴上，位于 `<!-- CACHE_BOUNDARY -->` 之后**，所以逐轮变化不影响稳定前缀。

**放在边界之后是刻意的，不是顺手。** 引用逐条评论都不同（这一条 @ 了 v2，下一条什么都没 @），放进边界之前的前缀里，等于每引用一次就让整个身份 + skill 清单的前缀失效一次——`<available_workers>` 之所以能待在边界之前，正因为它**不**这样逐轮变。

本模块不缓存登记表内容：框是每轮从本轮附件重新拼的，附件里的坐标与标题快照来自发帖口那一次校验。

### `[link-summary]` 链接摘要块（仅当本轮用户消息含 URL）

#### What the model sees

聊天路径从**本轮**用户消息里抽 URL（`link_injection.extract_urls`），抓取后每个 URL 渲染成一个块，块之间空一行，整体前置到请求指令最外层（顺序见 `app/services/ai/chat/README.md`）。成功时（`render_block`）：

```text
[link-summary url={url}]
title: {title}
description: {description}
content: {content}
[/link-summary]
```

`{content}` 是 `neutralize_external_text` 包裹后的正文摘录（随机 id 包裹，形状见 `../../../boundary/README.md`），抓不到正文时是 `(no body extracted)`；没有标题写 `(no title)`，没有描述写 `(none)`。`{title}` / `{description}` 取自页面的 `<title>` 与 meta，**在包裹之外**。#2472 起它们各自先截断（`LINK_TITLE_MAX = 200` / `LINK_DESC_MAX = 500` 字符，超出补 `…`），再经 `escape_frame_prose` 压成一行并实体转义，伪造的 `[/link-summary]`（容许括号内空白）也在这一步改写成 `[\/link-summary]`（`link-summary` 登记在 `frame_markers.BRACKET_FRAMES`，方括号闭合由 `escape_frame_body` 负责，fh5 起本模块不再自带私有 defuser，输出逐字节不变）。`{content}` 过 `escape_frame_body`：方括号闭合与我们拥有的尖括号闭合（如 `</system-reminder>`）都被改写；fh5 之前正文只改写方括号闭合。

失败时（`render_failure_block`，`{reason}` 是 `异常类名: 消息` 的第一行、截到 200 字符，再经 `escape_frame_prose`，方括号闭合在其中一并改写）：

```text
[link-summary url={url} error=true]
We tried to fetch this URL but failed: {reason}.
The agent should not invent its contents.
[/link-summary]
```

#### Token effect

每轮最多 3 个 URL；单个抓取超时 15 秒、整体 30 秒；抓取上限 2 MiB（`DEFAULT_FETCH_CAP_BYTES`）；正文摘录 8 KiB 字符（`DEFAULT_BODY_EXCERPT_CHARS`，约 2k token）；标题 200、描述 500 字符。块只在本轮存在、不持久化，下一轮就没有了。

#### KV Cache effect

在缓存边界之后的请求指令里，**不影响稳定前缀**。因为不持久化，下一轮模型看不到页面内容，只看到自己上一轮基于它写的答复。

### 工具 schema 与指令：`FinishIssue`（仅 issue 触发）

#### What the model sees

issue 触发的轮次（`issue_dispatch` / `issue_dispatch_auto` / `issue_reply`）在 tools 里追加 `FinishIssue`（在 `AskUser` 之前）。`options` 的子 schema 是 `question.OPTIONS_JSON_SCHEMA`，与 `AskUser.options` 同一个对象（见上文 `AskUser` 块）：

```json
{
  "type": "function",
  "function": {
    "name": "FinishIssue",
    "description": "Declare the outcome of your work on this issue. Call this once when you are done with the turn: use 'completed' if the task is finished, 'needs_input' if you are blocked and need a human decision or information, or 'continue' if you made progress but need another turn to finish. Always include a short 'reason'.",
    "parameters": {
      "type": "object",
      "properties": {
        "outcome": {
          "type": "string",
          "enum": [
            "completed",
            "needs_input",
            "continue"
          ],
          "description": "completed | needs_input | continue"
        },
        "reason": {
          "type": "string",
          "description": "One sentence: what was done, or what you need from a human, or what remains."
        },
        "options": "<OPTIONS_JSON_SCHEMA>"
      },
      "required": [
        "outcome"
      ]
    }
  }
}
```

同时在**整条系统消息末尾**追加两个换行加 `FINISH_ISSUE_INSTRUCTION`，逐字如下：

```text
You are working an assigned issue. Before you end this turn you MUST call the FinishIssue tool exactly once to declare the outcome:
- 'completed' — the task is done and ready for a human to review.
- 'needs_input' — you are blocked and need a human decision or information; state precisely what you need in 'reason'.
- 'continue' — you made real progress but need another turn to finish.
Always include a one-sentence 'reason'. Do not end the turn without calling FinishIssue.
```

已知 `issue_id` 的 issue 轮次（两个生产调用方都传；与 `SetAcceptanceCriteria` 工具同一个注入块，见上文该工具的块）再追加一个换行加 `ACCEPTANCE_CRITERIA_INSTRUCTION`，逐字如下，所以系统消息以 `FINISH_ISSUE_INSTRUCTION + "\n" + ACCEPTANCE_CRITERIA_INSTRUCTION` 结尾：

```text
If the issue has no acceptance criteria yet, call SetAcceptanceCriteria once before you start working, stating checkable outcomes (scenes, shots, images, word count). A person's criteria are locked; work to them.
```

轮次结束时模型若没有声明结果，`forced_finish_declaration` 另发**一次独立请求**：系统消息是 `You just worked on an assigned issue but your turn ended without declaring an outcome.` 加两个换行加上面第一段指令（只用 `FINISH_ISSUE_INSTRUCTION` 这个核心，不带 `ACCEPTANCE_CRITERIA_INSTRUCTION`——那次请求的 tools 只有 `FinishIssue`），tools 只有 `FinishIssue`，消息只有一条 user：

```text
Your last message on this issue was:

{assistant_text}

Call FinishIssue now to declare the outcome that best matches what you just did.
```

adapter 接受 `tool_choice` 时强制 `{"type": "function", "function": {"name": "FinishIssue"}}`，否则不强制。

#### Token effect

schema 约 200 token（不含 `options` 子 schema），核心指令约 110 token，`ACCEPTANCE_CRITERIA_INSTRUCTION` 约 45 token（仅已知 `issue_id` 的轮次），都固定。强制声明请求 `max_tokens = 300`、温度 0.2；`{assistant_text}` 是上一轮完整的最终文本，**没有上限**。

#### KV Cache effect

指令追加在 `# Runtime` 行之后，不占稳定前缀；但 tools 列表多了 `FinishIssue`，所以 issue 轮次与聊天轮次是两个缓存族（同上文 tools 顺序的说明）。强制声明是**独立请求**，与轮次本身没有共享前缀。

### 工具 schema：`GenerateImage` / `GenerateVideo`（按能力授予）

#### What the model sees

聊天路径在 agent 有 `media.image` / `media.video` 授予且全站开关 `media_kill_switch_engaged()` 未拉下时，把对应 schema 追加到 tools（`ai_library_chat_service.py`）。逐字如下：

```json
{
  "type": "function",
  "function": {
    "name": "GenerateImage",
    "description": "Generate an image from a text prompt. The image is saved to the user's Generations library and a reference is returned. Use when the user asks you to create / draw / render an image.",
    "parameters": {
      "type": "object",
      "properties": {
        "prompt": {
          "type": "string",
          "description": "What to depict."
        },
        "aspect_ratio": {
          "type": "string",
          "description": "Optional, e.g. '16:9', '1:1'. Default 16:9."
        },
        "model": {
          "type": "string",
          "description": "Optional model override."
        },
        "provider": {
          "type": "string",
          "description": "Which image provider to use: a catalog model name, or a model id / provider key the user enabled under Settings → AI (e.g. 'doubao'). Without it, resolution falls back to the platform catalog, which may have no image model enabled."
        }
      },
      "required": [
        "prompt"
      ]
    }
  }
}
```

```json
{
  "type": "function",
  "function": {
    "name": "GenerateVideo",
    "description": "Generate a short video from a source image. Asynchronous: this call only submits the job and returns a task_id at once. Rendering can take up to ~27 minutes; the finished video (its generated_media_id and url) or the failure reason arrives later as an inbox message. Do not poll, and do not re-submit the same request while it is rendering. The video is saved to the user's Generations library.",
    "parameters": {
      "type": "object",
      "properties": {
        "prompt": {
          "type": "string",
          "description": "Motion / scene description."
        },
        "source_image_url": {
          "type": "string",
          "description": "URL of the image to animate."
        },
        "model": {
          "type": "string",
          "description": "Optional model override."
        },
        "provider": {
          "type": "string",
          "description": "Optional provider override."
        }
      },
      "required": [
        "prompt",
        "source_image_url"
      ]
    }
  }
}
```

#### Token effect

两份合计约 450 token，固定。

#### KV Cache effect

在 tools 列表里，属于请求前缀的一部分。授予变化、或全站开关拉下 / 恢复，都会改变 tools 列表，让该 agent 的请求换到另一个缓存族。真正的拦截在 `HighRiskCapabilityGateHook`，这里只决定给不给模型看。

### 工具 schema：写剧本工具组（按 write_level 授予）

#### What the model sees

`PromptComposer._build_tools` 对**每一条**组装路径都按 agent 的 `write_level` 过滤后追加（`screenwriting_specs.screenwriting_tool_specs`）：`ListScenes` / `ReadScene` 要 `read`，`ProposeEdit` 要 `propose`，`CreateShot` / `UpdateShot` / `ApplyEdit` 要 `write`；`GenerateShotImage` 不看 write_level，只在 `media.image` 授予且全站开关未拉下时出现。顺序固定如下，每行一个工具，逐字：

```json
{"type": "function", "function": {"name": "ListScenes", "description": "List the scenes you can work on, in script order. Returns for each: scene_id (the handle to pass to other tools), scene_no_in_episode (the human-facing scene number, unique within its episode), INT/EXT, location, time of day, and whether the scene has any written content yet. Start here — you cannot address a scene without its scene_id.", "parameters": {"type": "object", "properties": {"episode_id": {"type": "string", "description": "Optional: restrict to one episode. Omit to list everything in reach."}, "limit": {"type": "integer", "description": "Optional max rows (capped server-side)."}}, "required": []}}}
{"type": "function", "function": {"name": "ReadScene", "description": "Read one scene's content. Returns its heading, its scene_no_in_episode, its existing shot cards, and its elements as an ordered list of {element_id, type, text}. element_id values are the anchors ProposeEdit addresses; content_version is the concurrency token to quote back when proposing an edit.", "parameters": {"type": "object", "properties": {"scene_id": {"type": "string", "description": "Opaque scene handle from ListScenes/ReadScene. Never construct or guess one."}}, "required": ["scene_id"]}}}
{"type": "function", "function": {"name": "CreateShot", "description": "Add one shot card to a scene's storyboard. The shot's number is assigned by the server (next in that scene) — do not pass one. Returns the created card including shot_label, the human-facing '<scene_no>-<shot_no>' reference.", "parameters": {"type": "object", "properties": {"scene_id": {"type": "string", "description": "Opaque scene handle from ListScenes/ReadScene. Never construct or guess one."}, "shot_type": {"type": "string", "description": "Shot size, e.g. 'WIDE', 'MEDIUM', 'CLOSE UP', 'OTS'."}, "camera_angle": {"type": "string", "description": "e.g. 'EYE LEVEL', 'LOW ANGLE', 'HIGH ANGLE'."}, "camera_movement": {"type": "string", "description": "e.g. 'STATIC', 'PAN LEFT', 'DOLLY IN', 'HANDHELD'."}, "focal_length": {"type": "string", "description": "Lens, e.g. '24mm', '50mm', '85mm'."}, "lighting": {"type": "string", "description": "Lighting note for this shot."}, "description": {"type": "string", "description": "One sentence describing what the shot shows."}}, "required": ["scene_id"]}}}
{"type": "function", "function": {"name": "UpdateShot", "description": "Revise an existing shot card's parameters or description. Only the fields you pass change. Cannot move, renumber, delete, or mark a card as rendered.", "parameters": {"type": "object", "properties": {"shot_id": {"type": "string", "description": "Shot handle from ReadScene's shots list."}, "shot_type": {"type": "string", "description": "Shot size, e.g. 'WIDE', 'MEDIUM', 'CLOSE UP', 'OTS'."}, "camera_angle": {"type": "string", "description": "e.g. 'EYE LEVEL', 'LOW ANGLE', 'HIGH ANGLE'."}, "camera_movement": {"type": "string", "description": "e.g. 'STATIC', 'PAN LEFT', 'DOLLY IN', 'HANDHELD'."}, "focal_length": {"type": "string", "description": "Lens, e.g. '24mm', '50mm', '85mm'."}, "lighting": {"type": "string", "description": "Lighting note for this shot."}, "description": {"type": "string", "description": "One sentence describing what the shot shows."}}, "required": ["shot_id"]}}}
{"type": "function", "function": {"name": "ProposeEdit", "description": "Propose a revision to specific elements of a scene, for the writer to accept or reject. This does NOT change the script — it returns a reviewable proposal anchored to the element_ids you name, having checked that the proposal could be applied right now. Quote the content_version you got from ReadScene so a proposal the writer has already overtaken comes back flagged stale.", "parameters": {"type": "object", "properties": {"scene_id": {"type": "string", "description": "Opaque scene handle from ListScenes/ReadScene. Never construct or guess one."}, "element_ids": {"type": "array", "items": {"type": "string"}, "description": "element_id values from ReadScene naming the passage you are rewriting. Ignored when the writer attached a selection to this turn — theirs wins."}, "edits": {"type": "array", "description": "The replacement text, bound to the element it replaces. One entry per element you are changing.", "items": {"type": "object", "properties": {"element_id": {"type": "string"}, "text": {"type": "string"}}, "required": ["element_id", "text"]}}, "rationale": {"type": "string", "description": "One sentence: why this change."}, "base_content_version": {"type": "integer", "description": "The content_version ReadScene returned for this scene. This is the proof you are rewriting the text you actually read: if the writer changed those same elements meanwhile, the edit is refused instead of overwriting them."}}, "required": ["scene_id", "element_ids", "edits"]}}}
{"type": "function", "function": {"name": "ApplyEdit", "description": "Write a revision into the script, replacing the text of the elements you name. Read the scene first and pass the content_version it returned as base_content_version — it is REQUIRED. If the writer changed those same elements while you were working, nothing is written and you get their current text back to rebase on; if they changed something else in the scene, your edit is applied on top of their work. Element type is preserved — you can rewrite a line of dialogue, not turn it into an action line.", "parameters": {"type": "object", "properties": {"scene_id": {"type": "string", "description": "Opaque scene handle from ListScenes/ReadScene. Never construct or guess one."}, "element_ids": {"type": "array", "items": {"type": "string"}, "description": "element_id values from ReadScene naming the passage you are rewriting. Ignored when the writer attached a selection to this turn — theirs wins."}, "edits": {"type": "array", "description": "The replacement text, bound to the element it replaces. One entry per element you are changing.", "items": {"type": "object", "properties": {"element_id": {"type": "string"}, "text": {"type": "string"}}, "required": ["element_id", "text"]}}, "rationale": {"type": "string", "description": "One sentence: why this change."}, "base_content_version": {"type": "integer", "description": "The content_version ReadScene returned for this scene. This is the proof you are rewriting the text you actually read: if the writer changed those same elements meanwhile, the edit is refused instead of overwriting them."}}, "required": ["scene_id", "element_ids", "edits", "base_content_version"]}}}
{"type": "function", "function": {"name": "GenerateShotImage", "description": "Dispatch AI image generation for one existing shot card, using its cinematography tags, description, and scene heading as the prompt. This call is ASYNCHRONOUS: it only confirms the generation was dispatched — the produced image lands on the shot some time after this call returns, not in its result. It costs real money per call, so do not call it again for the same shot_id just because you have not seen the result yet; wait and re-read the shot instead.", "parameters": {"type": "object", "properties": {"shot_id": {"type": "string", "description": "Shot handle from ReadScene's shots list or CreateShot's result. Never construct or guess one."}}, "required": ["shot_id"]}}}
```

#### Token effect

七个全给时约 2k token（约 7.3k 字符），固定；按 write_level 少给几个。

#### KV Cache effect

在 tools 列表里，属于请求前缀。改任何一个描述、改过滤规则、改 agent 的 write_level，都会换缓存族。执行期的强制在 `AgentRunner._dispatch_screenwriting`（没有能力门的 runner 直接拒绝，见 `../runner/README.md`），这里只是展示过滤。

## Known Limitations and Deferred Work

- **`LibrarySearch` 只搜调用者自己的库，不含团队库**。`SearchService.hybrid_search` 只按 `user_id` 限定、没有 team 参数；会话 @agent 路因此搜的是**召唤者个人的库**，而结果会贴进团队频道——所以那条路与 `ResourceFetch` 共用 `read_team_resources` 门。补团队库要先给 hybrid 的两个 RPC 加 scope，再把门换成真正的团队可读判定。
- **`LibrarySearch` 结果里的 title / description 是外部文本，未经 `neutralize_external_text`**（裁决：与 `ResourceFetch` 同级）。它们是抓来的标题与简介，在结构化 JSON 字段里、截到 200 字符，skill 正文提醒「当数据不当指令」——这是第二层，不是结构防护。要升级就在 `_hit_dict` 一处包裹。
- **会话 @agent 路上，没有 `read_team_resources` 的 agent 仍装着 `library-search` skill，但工具不存在**。skill 绑定按 agent 走（`seed_loader.AGENT_SKILL_SLUGS`），工具按会话里的权限挂；模型照 skill 去调只会拿到 `LibrarySearch is not available for this turn.`。要消掉得让 skill 清单也按工具可用性过滤，本期不做。
- **会话 @agent 路的命中是召唤者个人库的，贴进团队频道后别人点开会 403/404**。卡片里的 `resource_id` 只对召唤者可读；其他成员看得到标题（已经在频道里了），点进详情页拿不到。补团队库之后这条随之缩小，但个人条目的情况仍在。
- **`LibrarySearch` 的 `camera` / `transcript` 层尚未建成**（向量分层 PR 4 / 二期）。枚举里先有，传了不报错只是空。`visual` 层自 mig 507 起是真的（每支视频最佳镜头帧，`shot` 带时间码）；覆盖率看 `GET /search/vectors/status` 的 `visual` 行，没索引过的视频在这条腿上就是搜不到，skill 正文教模型把「没索引」与「没匹配」分开说。带 `text` 的层过滤是 hybrid 合并**之后**做的：向服务要满 20 条再筛，仍可能不足 `limit`。
- **身份三段无长度上限**。一个 `agent_md` 写到 200k 字符的 agent 会把每一轮请求都撑爆，而且因为它在缓存边界之前，代价逐轮重复。skill 正文有 64k 上限（`../skills/`），身份文档没有对应的护栏。
- **两个指纹都不覆盖 `request_instructions`、`<available_resources>` 与 `# Runtime` 行**。它们是缓存键，不是"这次请求的输入摘要"——`_dynamic_fingerprint()` 只加了记忆内容，因为缓存隔离只需要防跨用户串味。**别拿它判断"两轮输入是否相同"**：改了 request instructions、换了 @-mention 的资源、跨了一分钟，动态指纹都可能一模一样。
- **`Skill` 的 inputSchema 现在同时承载 skill 装载、内建 `todo`、内建 `task` 三类参数**，靠 description 里的「skill='…' only」区分。这是裁决不是遗漏：三者共用一个工具名是既有契约（`AgentRunner` 按 `tool_name == "Skill"` 分派），拆成三个工具会改动分派处与所有 pin。代价是 schema 约 300 token 且每次请求都带。
- **`_build_tools()` 只决定给模型看什么，不是执行期的强制**。写权限的真正拦截在 `AgentRunner._dispatch_screenwriting`；把这里的过滤当成权限校验是 A4 评审记过的错误。
- **binary 附件超过 8 条时被静默截断**（`chat_attachment_resolver.py` 的 `capped = requests[:MAX_ATTACHMENTS_PER_TURN]`）。第 9 条起既不解析也**不产出 `attachment_failures` 条目**，只写一条 warning 日志——用户贴了 12 张图，其中 4 张从未到达模型而界面上没有任何提示。与「触发路径必须类型化失败回显」相悖，是 P5 之前就存在的缺口。⚠️ `asset_ref` 那一侧**不是**这样（`MAX_ASSET_REF_ATTACHMENTS` 超出即类型化回显）——两者数值相同、行为相反，别读串。
- **`resource_ref` 的条数仍然没有服务端上限**，这是**裁决而不是遗漏**：不论多少条都是一次批量查询，封它只会拿走一条能用的路（`@` 选择器不限制暂存条数）。代价是系统消息里 `<resource>` 那一半的体积仍由客户端决定——今天的写方只有我们自己的 composer UI，直接打 API 的调用方可以把目录撑长。真出现问题时该封的是**渲染出来的字符数**，不是条数。
- **issue 回复框不支持资产引用**（P5 裁决 H）。`frontend/components/Todolist/IssueReplyBox.tsx` 走的是另一条发送路径，本期只接了聊天面板一侧——「两个入口只接一个」这类缺口在本仓已经出现过多次，所以显式记在这里而不是留在源码 TODO。
- **资产的主图可能「有」却「取不到」，此时条目被降级渲染**。`has_image` 由解析器用**系统作用域**读 `resources` 算出（资产的文件行是经资产可读的，不是经调用者的 team 成员关系），而 `ResourceFetch` 只认本轮可访问集合——两者会不一致，最典型的是系统预设资产，它的文件落在用户不属于的 scope 里。`ai_library_chat_service._merge_asset_primaries` 在这种情况下把条目改写成 `has_image="false"` 且**省掉 `primary_resource_id`**（即上面那条「没有图可取」的形状），并向用户回一条 `asset_no_primary_image`。宁可少给一张图，也不给模型一个用了就失败的 id。
- **`audio` 资产的主资源取不到时，用户端没有回显**（同上那条的副作用）。裁决 C 的 reason 词表里，`asset_no_primary_image` 明确只对「本该有图的类型」成立，所以音频只写日志、不进 `attachment_failures`——模型仍拿到一致性提示词，只是听不到那段音频，而用户不会被告知。要补就得再给词表加一个值（词表现在是五个：四个来自 `asset_ref_resolver`，第五个 `attachment_limit_exceeded` 由 chat service 的 `asset_ref` 条数上限产出）。
- **被转进收件箱的评论，附件只以文本清单进框**（2026-09-23 FH2 T1 起）。一条评论在 root run 忙时会进 `agent_run_inbox`；`render_inbox_message`（`../runner/inbox.py`）现在在框内列出每个附件的 kind / 名字 / id，但 `output_ref` 仍**不会**渲染成 `<referenced_outputs>`，图片也不作为像素注入，而且**清单 id 不进本轮 `ResourceFetch` 白名单**（`_available_refs` 在请求开始时算定；没有引用的轮次连工具都不注册），调了只会拿到 `resource not referenced in this turn`——模型只知道文件存在。领取后仍然取不到：这条评论已被本 run 领走，不会作为下一轮请求的附件再来。像素注入与「领取时把清单 id 并入本 run 白名单（并在无引用轮次注册 ResourceFetch、过一遍访问校验）」是同一张留票：都要把 hook 与本轮的工具/vision 状态接起来。
- **聊天面板一侧的引用是被「拒绝」而不是「校验」的**（3a 修复轮 1）。`AILibraryChatService.chat` 碰到 `output_ref` 附件直接回 400 `output_ref_unresolvable`（消息说 citations require an issue context），轮次一次都不开始。理由是引用在 3a 里按定义就是 issue 作用域的——解析器校验的正是「这一版的 run 属于本 issue」——而聊天会话没有 issue 可比；在那里编一套「拿 session 的 run 当 issue 用」的第二套归属语义，比拒绝更糟。⚠️ 所以聊天面板**今天不能引用产出**，这是本期的范围边界，不是缺陷。引用哪天变成非 issue 作用域，改的是 `refuse_citations_without_issue` 一个函数。
- **legacy 路径（issue 没有 assignee agent）根本不转发附件**，所以那条路上的引用既不被校验也不到达任何人。这对所有附件 kind 都成立，不是 `output_ref` 引进的；在那里加校验只会给出「引用有效」的假保证，因为它随后照样被丢掉。
- **`script_chapter` 今天没有生产者**，所以引用它必然是 `output_ref_unresolvable`。没有特判——登记表说没有就是没有。
- **`link_injection` 失败会落显式占位块**，不是静默跳过——但占位块的文案目前只有英文，与 UI 的 i18n 口径不一致。
- **`[link-summary]` 是方括号框**。它同时登记在 `OWNED_FRAMES` 与 `BRACKET_FRAMES`，所以 `escape_frame_body` / `escape_frame_prose` 两种闭合拼写都改写；新增往块里写外部文本的地方过这两个函数之一即可。新加方括号框必须登记进 `BRACKET_FRAMES`（`test_frame_escape_wiring.py` 会扫出未登记的），且不许在别的模块再写私有的方括号闭合正则（同一文件里的源码扫描会拒绝）。块头的 `url=` 仍未转义，来源是本轮用户消息里的 URL 正则匹配（不含空白与 `]`）。#2472 之前标题与描述未转义、无上限，记录在此供对照。非 HTML 页面只有响应头信息。
- **`Delegate` 让模型「use status_query」，但模型没有能发 `status_query` 的工具**。`status_query` 是收件箱消息类型（`inbox_processor` 有处理器），而模型手里只有 `Delegate` / `Skill`；描述与 `queued` / `timeout` 结果里的 `note` 都这么说。改描述会让全站前缀失效一次，所以记在这里而不是顺手改（fh5 T4 发现，留票）。
- **强制声明的 `tool_choice` 从不穿过 `LLMFallbackChain`**（链的 `call` 不收这个参数），所以生产上那次强制请求实际是「不强制」。已记票（fh4 计划留票 E）。
- **`{assistant_text}` 进强制声明请求时没有上限**。
