# boundary — 不可信输入的收口层

外部世界（抓来的网页、yt-dlp 的 description、字幕、用户输入的文件名和剧本正文）在进入 LLM 提示词、文件系统、HTTP 客户端之前，从这里过一道。

| 模块 | 挡什么 |
|------|--------|
| `external_text.py` | 提示词注入 —— 整份外部文档，随机 id 包裹 |
| `frame_markers.py` | 提示词注入 —— 属性值/框内散文的闭合标记 |
| `url_guard.py` / `ssrf_proxy.py` / `pinned_dns.py` | SSRF |
| `path_guard.py` | 路径穿越 |
| `max_bytes.py` | 超大响应体 |
| `safe_regex.py` | ReDoS |
| `log_redact.py` / `secret_compare.py` | 凭证泄露 / 时序侧信道 |

## Model Experience

本包不直接组装提示词——它提供被 `../services/ai/prompts/` 调用的**变换**。模型可见面通过那两个函数的输出体现：

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

### `escape_frame_attr()` / `escape_frame_body()` 的输出

#### What the model sees

`escape_frame_attr` 把 `&<>"` 转成实体、换行压成空格——用于 XML 属性值（文件名、slug、model 名）。普通文件名不受影响：`Q3 report (final).md` 原样通过。

`escape_frame_body` 只把**我们自己拥有的**框的闭合标记（`OWNED_FRAMES`）改写成 `<\/frame>`；不属于我们的标签（`</div>`、`</think>`）原样保留，因为剧本正文可能合法地谈到它们，而只有我们自己的框才赋予权威。

#### Token effect

可忽略。转义只在命中时增加个位数字符，普通文本零变化——这是它与 `neutralize_external_text` 分工的原因：属性值和单行散文不值得为它付随机包裹那几十个 token。

#### KV Cache effect

**确定性**：同一输入永远得到同一输出，没有随机成分。所以转义后的文本可以安全地放进稳定前缀（`<available_skills>` 的 slug 就在前缀里）。这是它与 `neutralize_external_text` 的关键差别。

## Known Limitations and Deferred Work

- **`escape_frame_body` 只处理闭合标记，不处理开标记**。内容可以插入一个假的 `<available_skills>` 开标记；它关不掉真框，但可能让模型误以为出现了嵌套结构。判断是代价（把剧本里每个 `<` 都转义）大于收益。
- **`OWNED_FRAMES` 靠登记维持完整性**。新框忘了登记就没有防护——`tests/services/ai/prompts/test_frame_escape_wiring.py` 的扫描守卫是唯一的兜底，而它只扫三个已知文件；**在别处新建提示词框，守卫扫不到**。
- **`_INJECTION_PATTERNS` 是字面量表，天然滞后**。新模型的新特殊 token 要手工补进去。它是第二层防御，不是主防御——主防御是那个随机 id。
- **子进程环境未擦洗**（2026-08-22 实测）：`app/` 下 35 处 spawn 零处擦洗（2 处传 `env=` 的也是 `os.environ` 全量复制）。凭证会随完整环境进入 yt-dlp / ffmpeg / node 等第三方二进制，而 yt-dlp 处理的是攻击者可控的 URL。见 CLAUDE.md「防御模式」节。这是本包该覆盖而尚未覆盖的边界——本包目前只挡"进提示词"，不挡"进子进程"。
