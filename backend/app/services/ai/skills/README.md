# skills — 惰性可读的 Skill 工具

Skill 正文**不进**系统消息，只有 slug + description 进（见 `../prompts/README.md`）。模型判断某个 skill 适用时调 `Skill(skill=…, file=…)`，正文才在那一刻进上下文。这是"渐进披露"：绑 20 个 skill 的成本是几百 token，不是几十万。

- `skill_tool_service.py` — `Skill()` 工具的执行体，回 body_md 或子文件内容
- `mcp_tool_registration.py` — 把 skill / agent 注册成 MCP 工具描述符

## Model Experience

### `Skill()` 工具的返回体

#### What the model sees

一个 JSON 对象，作为 tool result 进入下一轮请求。三种形状：

命中 skill 本体（不带 `file`）：

```json
{"skill": "<slug>", "description": "<描述>", "prompt": "<SKILL.md 正文>"}
```

命中子文件（带 `file`）：

```json
{"skill": "<slug>", "file": "<路径>", "description": "…", "prompt": "<文件内容>", "file_type": "<类型>"}
```

二进制引用（PDF/图片，`content` 为 NULL）—— 不返回空串，返回可执行的说明：

```json
{"skill": "…", "file": "…", "prompt": "<二进制说明>", "file_type": "binary-ref", "binary_url": "<URL>", "note": "<同上>"}
```

说明原文逐字：

```markdown
This file is a binary reference (PDF/image/etc.) and cannot be returned inline. Use the URL with your own file-reader tool, or report the limitation back to the user if you cannot fetch it.
```

未知 slug / 未知文件返回 `{"error": "..."}`——**显式错误，不是空结果**。这条是刻意的：空串会被模型静默忽略，然后它开始编造 skill 的内容。

#### Token effect

单次返回上限 **64000 字符**（`MAX_SKILL_CONTENT_CHARS`，与 `resource_fetch_tool` 的 doc 上限同一个数）。超出时截断并追加逐字说明：

```markdown
[... truncated, {N} chars remaining; this skill content exceeds the inline cap — reference a specific file/section if you need more]
```

告诉模型"被截断了、可以点名要子章节"，而不是让它以为自己看到了全文——**截断必须自曝**，静默截断会让模型对着半截内容自信作答。

一轮里可以调多次 `Skill()`，每次的结果都留在历史里累积，直到压缩层介入。

#### KV Cache effect

**Append-only**：tool result 追加在历史末尾，位于系统消息之后，不改动已有前缀，因此不使 KV 缓存失效。但它确实把后续每一轮的请求都变长了——同一个 skill 在一次会话里被调两次，正文就在历史里躺两份。

## Known Limitations and Deferred Work

- **重复调用不去重**：同一个 skill 在一次会话里调 N 次，正文进历史 N 次。系统提示词里那句"Never call Skill more than once per turn"是靠模型自觉，没有机制拦截。
- **64k 上限是按字符不是按 token 数**。CJK 内容的实际 token 数可以是英文同等字符数的两倍以上，所以对中文 skill 而言这个上限比预期宽松得多。
- **`binary_url` 直接给模型**，能不能取到取决于模型自己有没有 file-reader 工具；本模块不代取，也不校验 URL 是否仍然有效。
