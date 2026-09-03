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

**所有属性值都过 `escape_frame_attr`，`<asset>` 的正文过 `escape_frame_body`**（`name` 是用户可自由改的，一致性提示词整段都是用户写的）；见 `../../../boundary/frame_markers.py`。

⚠️ **`escape_frame_body` 只中和 `OWNED_FRAMES` 里那些框的闭合标记，别的标签一律原样保留**——用户散文里的 `</div>`、`</think>`、`<b>` 都会照原样到达模型。这是**约定的契约不是疏漏**：只有我们自己的框才赋予「这是系统说的」那层权威，把每个尖括号都转义掉会毁掉合法引用标签的文字，换不来任何安全收益。

⚠️ **`asset` 刻意不在 `OWNED_FRAMES` 里**（P5 裁决 A，spec §6.5 已按此修订）：它是我们拥有的框**内部的元素**，不是框。用户提示词里出现字面 `</asset>` 只会截断它自己那一条，后面的文字仍在 `<available_resources>` 内，拿不到 harness 权威；反过来把它登记成框，会把提示词里每一次合法提到该词都糟蹋掉。`tests/services/ai/prompts/test_frame_escape_wiring.py` 的 `ignore` 名单记着这条理由。

#### Token effect

与本轮 @-mention 的条数成正比：

- **每条 `<resource … />`** 约 30-60 token。
- **每条 `<asset …>…</asset>`** = 属性约 25-40 token + 一致性提示词。提示词是这里唯一无自然上限的输入（资产自身提示词 + loadout 的 `prompt_extra` + 每个链接的服装 / 道具 / 场景的提示词拼起来），所以在 `app/services/assets/chat_ref.py` 里**硬截断到 `MAX_CONSISTENCY_PROMPT_CHARS = 600` 字符**。⚠️ `" [truncated]"` 标记是**追加在上限之外**的，被截断的条目正文是 **612** 字符而不是 600——按常量本身算预算会每条少算 12 字符。600 字符在纯 ASCII 下约 150 token，全中文时可以接近 600 token，估上限要按后者。
- **单条有界，总量无界。** 上面两个上限只管住「每一条多大」；**引用型附件（`resource_ref` / `asset_ref`）的条数服务端不限制**——`ChatMessageRequest.attachments` 是没有 `max_length` 的 list。链路上**唯一**存在的条数上限是 `chat_attachment_resolver.MAX_ATTACHMENTS_PER_TURN = 8`，但它只作用于 **binary 桶**（`image` / `video` / `pdf` / `audio`），而分流处按 kind 已把两种引用摘走，所以它**管不到这一块**。⚠️ 那个 8 还是**静默截断**：第 9 条起直接丢弃且不产出任何 `attachment_failures` 条目（既有缺口，非 P5 引入）。所以这一块正确的说法是 `总量 = 客户端发的引用条数 × 单条上限`，而不是「有上限」。已记进下面的 Known Limitations。

**资源正文与资产主图都不在这里**——这一块只是目录，正文/图片要模型主动调 `ResourceFetch` 才进上下文。没有 @-mention 也没有资产时整块返回空字符串，这样无 mention 的轮次系统消息缓存键不变。

#### KV Cache effect

它拼在 request instructions 里，位于缓存边界**之后**，所以逐轮变化不影响稳定前缀——资产条目同样在边界之后，改一个资产的提示词、它的 loadout 或它的链接，只动这段后缀。

本模块不缓存资产内容：每轮从活数据重新组装，所以资产表里的编辑对下一条消息立即可见，没有失效步骤。

## Known Limitations and Deferred Work

- **身份三段无长度上限**。一个 `agent_md` 写到 200k 字符的 agent 会把每一轮请求都撑爆，而且因为它在缓存边界之前，代价逐轮重复。skill 正文有 64k 上限（`../skills/`），身份文档没有对应的护栏。
- **两个指纹都不覆盖 `request_instructions`、`<available_resources>` 与 `# Runtime` 行**。它们是缓存键，不是"这次请求的输入摘要"——`_dynamic_fingerprint()` 只加了记忆内容，因为缓存隔离只需要防跨用户串味。**别拿它判断"两轮输入是否相同"**：改了 request instructions、换了 @-mention 的资源、跨了一分钟，动态指纹都可能一模一样。
- **`_build_tools()` 只决定给模型看什么，不是执行期的强制**。写权限的真正拦截在 `AgentRunner._dispatch_screenwriting`；把这里的过滤当成权限校验是 A4 评审记过的错误。
- **引用型附件的条数没有服务端上限**。`ChatMessageRequest.attachments`（`app/schemas/ai_library_chat.py`）是没有 `max_length` 的 list，而链路上唯一的条数上限 `MAX_ATTACHMENTS_PER_TURN = 8` 只作用于 binary 桶（分流处已按 kind 把 `resource_ref` / `asset_ref` 摘走），所以 `<available_resources>` 这一块的总大小只受客户端约束。今天不构成事故是因为唯一的写方是我们自己的 composer UI；一个直接打 API 的调用方可以塞进任意多条，把系统消息撑到 provider 上限。要封顶就得在 schema 上加，别指望前端。
- **binary 附件超过 8 条时被静默截断**（`chat_attachment_resolver.py` 的 `capped = requests[:MAX_ATTACHMENTS_PER_TURN]`）。第 9 条起既不解析也**不产出 `attachment_failures` 条目**，只写一条 warning 日志——用户贴了 12 张图，其中 4 张从未到达模型而界面上没有任何提示。与「触发路径必须类型化失败回显」相悖，是 P5 之前就存在的缺口，记在这里以免被读成引用路径的行为。
- **issue 回复框不支持资产引用**（P5 裁决 H）。`frontend/components/Todolist/IssueReplyBox.tsx` 走的是另一条发送路径，本期只接了聊天面板一侧——「两个入口只接一个」这类缺口在本仓已经出现过多次，所以显式记在这里而不是留在源码 TODO。
- **资产的主图可能「有」却「取不到」，此时条目被降级渲染**。`has_image` 由解析器用**系统作用域**读 `resources` 算出（资产的文件行是经资产可读的，不是经调用者的 team 成员关系），而 `ResourceFetch` 只认本轮可访问集合——两者会不一致，最典型的是系统预设资产，它的文件落在用户不属于的 scope 里。`ai_library_chat_service._merge_asset_primaries` 在这种情况下把条目改写成 `has_image="false"` 且**省掉 `primary_resource_id`**（即上面那条「没有图可取」的形状），并向用户回一条 `asset_no_primary_image`。宁可少给一张图，也不给模型一个用了就失败的 id。
- **`audio` 资产的主资源取不到时，用户端没有回显**（同上那条的副作用）。裁决 C 把 reason 词表钉死在四个值，其中 `asset_no_primary_image` 明确只对「本该有图的类型」成立，所以音频只写日志、不进 `attachment_failures`——模型仍拿到一致性提示词，只是听不到那段音频，而用户不会被告知。要补就得先给词表加第五个值。
- **`link_injection` 失败会落显式占位块**，不是静默跳过——但占位块的文案目前只有英文，与 UI 的 i18n 口径不一致。
