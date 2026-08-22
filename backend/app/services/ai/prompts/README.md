# prompts — 系统消息组装

把 agent 的三份身份文档、绑定的 skill 清单、可派发的 worker 清单、记忆/图谱召回，装配成一条系统消息，并算出用于 provider 前缀缓存的指纹。

- `prompt_composer.py` — `PromptComposer.compose()` 是唯一入口，八条 dispatch 路径都走它
- `link_injection.py` — 用户消息里的 URL → 抓取 → 中和后的块，前插到 request instructions
- `render_available_resources()` — @-mention 的资源清单（模块级函数，供 chat 层按轮调用）

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

### `<available_resources>`（仅当本轮有 @-mention）

#### What the model sees

一行一个自闭合元素，后跟 ResourceFetch 的用法说明：

```markdown
<available_resources>
  <resource id="…" kind="video" mime="…" scope="…" size="12MB" updated="…" name="…" status="transcript:ready summary:none" />
</available_resources>

Use the ResourceFetch tool to load any of these on demand:
  ResourceFetch(resource_id, mode?, args?)
```

`status` 属性只对 video/audio 出现——对一个 markdown 文件说 `transcript:none` 是模型要读过去的纯噪声。（源码注释里说它还会 churn cache fingerprint；实际上这一块不进任何一个指纹，那句话指的是渲染文本本身。）

**所有属性值都过 `escape_frame_attr`**，因为 `name` 是用户可自由改的文件名；见 `../../../boundary/frame_markers.py`。

#### Token effect

与本轮 @-mention 的资源条数成正比，每条约 30-60 token。**资源正文不在这里**——这一块只是目录，正文要模型主动调 `ResourceFetch` 才进上下文。没有 @-mention 时整块返回空字符串，这样无 mention 的轮次系统消息缓存键不变。

#### KV Cache effect

它拼在 request instructions 里，位于缓存边界**之后**，所以逐轮变化不影响稳定前缀。

## Known Limitations and Deferred Work

- **身份三段无长度上限**。一个 `agent_md` 写到 200k 字符的 agent 会把每一轮请求都撑爆，而且因为它在缓存边界之前，代价逐轮重复。skill 正文有 64k 上限（`../skills/`），身份文档没有对应的护栏。
- **两个指纹都不覆盖 `request_instructions`、`<available_resources>` 与 `# Runtime` 行**。它们是缓存键，不是"这次请求的输入摘要"——`_dynamic_fingerprint()` 只加了记忆内容，因为缓存隔离只需要防跨用户串味。**别拿它判断"两轮输入是否相同"**：改了 request instructions、换了 @-mention 的资源、跨了一分钟，动态指纹都可能一模一样。
- **`_build_tools()` 只决定给模型看什么，不是执行期的强制**。写权限的真正拦截在 `AgentRunner._dispatch_screenwriting`；把这里的过滤当成权限校验是 A4 评审记过的错误。
- **`link_injection` 失败会落显式占位块**，不是静默跳过——但占位块的文案目前只有英文，与 UI 的 i18n 口径不一致。
