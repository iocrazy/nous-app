# boundary — 不可信输入的收口层

外部世界（抓来的网页、yt-dlp 的 description、字幕、用户输入的文件名和剧本正文）在进入 LLM 提示词、文件系统、HTTP 客户端之前，从这里过一道。

| 模块 | 挡什么 |
|------|--------|
| `external_text.py` | 提示词注入 —— 整份外部文档，随机 id 包裹 |
| `frame_markers.py` | 提示词注入 —— 属性值/框内散文的闭合标记，以及逐行框里的伪造行 |
| `system_note.py` | 提示词注入 —— `claude` 协议上中段 system 消息的 `<system_note>` 外框，只单框转义自己的闭合标记 |
| `url_guard.py` / `ssrf_proxy.py` / `pinned_dns.py` | SSRF |
| `path_guard.py` | 路径穿越 |
| `max_bytes.py` | 超大响应体 |
| `safe_regex.py` | ReDoS |
| `log_redact.py` / `secret_compare.py` | 凭证泄露 / 时序侧信道 |

## Model Experience

本包不直接组装提示词——它提供被 `../services/ai/prompts/` 调用的**变换**。模型可见面通过这些函数的输出体现：

### `neutralize_external_text()` 的输出

#### What the model sees

原文被包进带随机后缀的块，随机后缀让内容无法伪造闭合：

```markdown
<EXTERNAL_CONTENT_{16位随机hex}>
…原文，其中已知的指令字面量被改写成 [external-quoted: {原文}] …
</EXTERNAL_CONTENT_{同一个随机hex}>
```

被改写的字面量包括 "ignore previous instructions" 一族、ChatML/Llama/Gemma 的特殊 token（`<|im_start|>`、`[INST]`、`<start_of_turn>` …）、以及裸的 `Human:` / `Assistant:` 角色前缀。**改写不删除**——模型仍然看得见那些字，只是看见的是被引用的形态。

4 个以上连续换行折叠成 2 个（防"用空白把系统提示词冲出视野"）。

#### Token effect

调用方给 `max_chars` 软上限，超出软截断并追加 `\n[…TRUNCATED…]`。硬上限 1,000,000 字符，超过直接 raise `ExternalTextRejectedError`——那是调用方用错了边界，不该悄悄截断了事。`[external-quoted: ]` 的包裹会让命中的片段略微变长。

#### KV Cache effect

**每次调用产生新的随机 marker id，所以同一份文本两次中和的结果不同**。任何把中和结果放进稳定前缀的用法都会让前缀逐轮失效。当前调用方（translate / summarize / link_injection）都把它放在请求尾部的动态区，是对的；**新调用方要保持这个位置**。

### `escape_frame_attr()` / `escape_frame_body()` / `escape_frame_prose()` 的输出

#### What the model sees

`escape_frame_attr` 把 `&<>"` 转成实体、换行压成空格——用于 XML 属性值（文件名、slug、model 名）。普通文件名不受影响：`Q3 report (final).md` 原样通过。

`escape_frame_body` 只把**我们自己拥有的**框的闭合标记（`OWNED_FRAMES`）改写成 `<\/frame>`；不属于我们的标签（`</div>`、`</think>`）原样保留，因为剧本正文可能合法地谈到它们，而只有我们自己的框才赋予权威。

方括号框（`BRACKET_FRAMES`，今天只有 `link-summary`）的闭合 `[/name]`（容许括号内空白、不分大小写）同样被改写，统一成小写的 `[\/name]`；尖括号拼写 `</link-summary>` 照旧也被改写。`[loop_guard]`、`[Earlier conversation summary]` 这类只有开没有闭的标签不是框，原样保留。`BRACKET_FRAMES` 在导入时断言是 `OWNED_FRAMES` 的子集。

`escape_frame_prose` 用于**逐行框里的一段散文**（今天唯一的调用方是 `<available_resources>` 里 `<asset>` 的一致性提示词）。它先压平 `\r\n\t`，再走一遍 `escape_frame_body`，最后把 `&<>` 转成实体——引号**不**转义，因为这是元素正文不是属性值。

为什么需要第三个：`escape_frame_body` 保留换行与 `<` 是**刻意的契约**，对自由排版的框正确；但 `<available_resources>` 是模型**按行读**的目录，一行一条 `<resource … />`。一段允许换行且不转义 `<` 的用户文本因此可以排出一条与真行**字节级无法区分**的兄弟目录行（终审 I1 在真渲染器上复现过）。它关不掉框、伪造 id 也进不了 ResourceFetch 白名单，所以不是提权——它伪造的是「这些条目是系统列出来的」这层权威。压平杀掉伪造的行，实体转义杀掉留在同一行里的伪造元素，两条都需要。

**选哪一个的判据是「这段文本落进的框是不是按行读的」**，不是「它有多长」。

`escape_frame_close(text, frame, *, bracket=False)` 是单框版本：只把**那一个**自有框的闭合标记改写成 `<\/frame>`（`bracket=True` 时方括号拼写 `[/frame]` 也改写成 `[\/frame]`），其余一概不动。它给**外框**用——外框的内容里合法地带着别的自有框（`<system_note>` 包着摘要自己的 `</conversation_summary>`），那些内框由各自的渲染方转义，外框只需保证里面没有东西能关掉它自己。传一个不在 `OWNED_FRAMES` 里的名字会 raise。

#### Token effect

可忽略。转义只在命中时增加个位数字符，普通文本零变化——这是它与 `neutralize_external_text` 分工的原因：属性值和单行散文不值得为它付随机包裹那几十个 token。

#### KV Cache effect

**确定性**：同一输入永远得到同一输出，没有随机成分（三个函数都是）。所以转义后的文本可以安全地放进稳定前缀（`<available_skills>` 的 slug 就在前缀里）。这是它与 `neutralize_external_text` 的关键差别。⚠️ `escape_frame_prose` 今天的调用方在缓存边界**之后**，那是它的调用方的性质，不是本函数的限制。

### `<system_note>`：中段 system 消息在 `claude` 协议上的形状

#### What the model sees

Anthropic Messages API 只有顶层 `system` 参数，没有中段 system 角色。消息列表里的 `role=system` 消息（压缩摘要、循环守卫警告、分叉会话持久化的摘要行）由 `services/ai/adapters/claude.py` 原位转成一条 user 轮，文本是 `system_note.py::render_system_note(content)` 的输出：

```text
<system_note>
{content}
</system_note>
```

`{content}` 里只有 `</system_note>` 被改写成 `<\/system_note>`（`escape_frame_close`）。内容里别的不可信片段在各自的生产方转义过：摘要正文走 `summary_frame.py` 的 `escape_frame_body`，循环守卫的工具名走 `loop_guard.render_warning` 里的 `escape_frame_body`。多段内容只保留文本段，按换行拼起来。

adapter 随后把相邻同角色的轮合并成一个轮：`str` 内容变成 text 块，合并出的 user 轮里 `tool_result` 块提到最前、其余按原顺序跟在后面。所以一条夹在两个工具结果之间的 note，在 Claude 看来落在这一轮所有工具结果之后。

OpenAI 兼容 adapter 不经过这里，原样发 `role=system`。

#### Token effect

外框固定多约 10 个 token，加上 `{content}` 本身。转义只在命中时多一个字符。消息条数不变或变少（合并只会减少轮数）。

#### KV Cache effect

**确定性**：同一条 system 消息每次渲染成同一段文本，合并规则也是确定的，所以这一层不引入任何逐轮变化。note 所在的位置就是原消息的位置：压缩摘要在列表开头（压缩一发生，它之后的一切都换了，那是压缩本身的代价），循环守卫警告在尾部（append-only）。改外框文字或合并规则会让走 `claude` 协议、带中段 system 消息的会话一次性失效。

## Known Limitations and Deferred Work

- **`escape_frame_body` 只处理闭合标记，不处理开标记**。内容可以插入一个假的 `<available_skills>` 开标记；它关不掉真框，但可能让模型误以为出现了嵌套结构。判断是代价（把剧本里每个 `<` 都转义）大于收益。⚠️ 这一条**不适用于 `escape_frame_prose`**：它把每个 `<` 都转义了，所以开标记也伪造不了——代价换来的是逐行框的完整性，那笔账在那个位置上算得过来。
- **谁该用 `escape_frame_prose` 靠人判断**。`OWNED_FRAMES` 的完整性有扫描守卫兜底，「这个框是不是逐行的」没有。今天只有一个逐行框，新增一个而误用了 `escape_frame_body`，没有任何测试会说话。
- **`OWNED_FRAMES` 靠登记维持完整性**。新框忘了登记就没有防护——`tests/services/ai/prompts/test_frame_escape_wiring.py` 的扫描守卫是唯一的兜底，而它只扫三个已知文件；**在别处新建提示词框，守卫扫不到**。
- **`_INJECTION_PATTERNS` 是字面量表，天然滞后**。新模型的新特殊 token 要手工补进去。它是第二层防御，不是主防御——主防御是那个随机 id。
- **子进程环境未擦洗**（2026-08-22 实测）：`app/` 下 35 处 spawn 零处擦洗（2 处传 `env=` 的也是 `os.environ` 全量复制）。凭证会随完整环境进入 yt-dlp / ffmpeg / node 等第三方二进制，而 yt-dlp 处理的是攻击者可控的 URL。见 CLAUDE.md「防御模式」节。这是本包该覆盖而尚未覆盖的边界——本包目前只挡"进提示词"，不挡"进子进程"。
