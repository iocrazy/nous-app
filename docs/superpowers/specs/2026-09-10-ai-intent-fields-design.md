# AI 意图从「标签」改为「显式字段」——快捷指令选择页四项单选 + 搜索即创建

日期：2026-09-10 · 状态：已定稿，待实施（计划见 `docs/superpowers/plans/2026-09-10-ai-intent-fields.md`）

## 1. 背景与问题

转录 / 总结 / 解析三个 AI 工作流的**触发意图**目前以三枚系统标签承载：
`Transcript` / `Summary` / `Analyze`（mig 183 建，mig 220 归入 `Pipeline` 标签组）。
下载链在 `backend/app/tasks/download_helpers.py` 里读资源上的标签名决定派不派工作流
（`chain_transcript_summary_for_tags`、`chain_summary_for_tags`、`maybe_chain_ai_pipeline`）。

写入方有三处，体验各不相同：

| 入口 | 现状 |
|---|---|
| iOS/macOS 快捷指令（`ShortcutsTagsPage` + `temp_token_router`） | 用户要在几百个标签里找到「转录 / 总结 / 解析」三枚勾上；没有评级；搜不到标签时没有创建入口，只能点右上角「+」重输一遍；选中后搜索词不清空 |
| 网页 FloatingParse | 已经是三个独立按钮，但底下仍把它们翻成标签 id 塞进 `tag_ids` |
| Chrome 插件 `MediaHub Push` | 从列表勾标签；有「搜索即创建」条（带 EN/ZH 对译与「=」同名按钮） |

用户诉求（2026-09-10）：

1. 选择页上评级、转录、总结、**解析**做成单选，不再靠标签判定。
2. 之前"通过标签里的转录 / 总结 / 解析执行工作流"的路径改成读这些单选。
3. 选择页补上插件那条「搜索即创建」（含中英对译）。
4. 搜索命中后点选标签即清空搜索内容。

## 2. 方案取舍

**A（采用）：显式字段进，服务端映射到 Pipeline 系统标签，下载链不动。**
请求带 `transcribe / summarize / analyze / rating` 四个字段；服务端把三个布尔映射为
`type='system'` 的 Transcript / Summary / Analyze 标签 id 并入 `tag_ids`，`rating`
在资源建好后写 `resources.rating`。链路、数据、Task Center 全部沿用现状。

**B（不采用，留作后续）：新建 `resources.ai_intents` JSONB，三处链路改读它。**
更干净（资源不再顶着 Transcript 标签），但要加迁移、改三处读取、兼容期双读。
入口体验才是本次诉求，A 零迁移、零链路改动即可满足；B 在想撤掉那三枚标签时再做。

## 3. 契约

### 3.1 抓取请求（`POST /api/v1/media/fetch`、`POST /api/v1/media/fetch/batch`）

`MediaFetchRequest` 与 `BatchFetchRequest` 各新增四个可选字段：

| 字段 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `transcribe` | bool | `false` | 映射 `Transcript` 系统标签 |
| `summarize` | bool | `false` | 映射 `Summary` 系统标签（下载链现有语义：Summary 本身会先触发转录） |
| `analyze` | bool | `false` | 映射 `Analyze` 系统标签 |
| `rating` | int \| null，0–5 | `null` | 写 `resources.rating`；`null` 不写 |

- 布尔接受 pydantic v2 默认可强转的字符串（`"1"/"0"/"true"/"false"`），快捷指令传文本也能用。
- `rating` 接受 `""` → `null`（快捷指令空值），数字字符串 → int；越界 422。
- 旧的 `tags` / `tag_ids` 通道原样保留。若调用方仍把 `Transcript` 等名字放进 `tags`，行为与今天一致（不禁止，不去重以外不做特殊处理）。

**映射规则**（纯函数 `intent_tag_names`）：

```
(transcribe, summarize, analyze) → ["Transcript"?, "Summary"?, "Analyze"?]
```

**解析规则**（`resolve_intent_tag_ids`）：只按 `type = 'system'` 且 `name IN (...)` 查
`tags`，**绝不创建**。查不到的名字打 WARNING（说明种子缺失，是部署问题）并跳过，不阻断抓取。

### 3.2 单链路

`handle_media_fetch_dispatch` 现已把 `tags` 名字解析成 `effective_tag_ids` 再转发给
`parse_workflow`。本次在同一处把意图 id 并入（去重），并新增 `parse_workflow(rating=...)`
关键字参数；工作流在 `attach_tags_step` 之后新增 `set_rating_step(resource_id, rating)`，
`rating is None` 时不调用。

### 3.3 批量链路

`media_batch_router._attach_tags_after_save` 已经在轮询等资源出现后挂标签。本次把意图 id
并入 `t_ids`，并在同一次轮询命中时写 `rating`。

### 3.4 快捷指令临时令牌（`/api/v1/auth/temp-token/{token}/selection`）

`SelectionRequest` 新增 `rating` / `transcribe` / `summarize` / `analyze`（同 3.1 类型与默认）。
Redis 里的令牌数据新增 `"options"` 键：

```json
{"user_id": "...", "scopes": [...], "selection": ["tagA", "tagB"],
 "options": {"rating": 3, "transcribe": true, "summarize": false, "analyze": true}}
```

旧令牌没有 `options` 键时按全默认读（`rating=None`、三个布尔 `false`）。

`GET …/selection` 返回：

| 参数 | 返回 |
|---|---|
| `format=json`（默认） | `{"tags": [...], "rating": 3, "transcribe": true, "summarize": false, "analyze": true}` |
| `format=text` | 标签逗号串（**不变**，现有快捷指令零改动） |
| `format=text&field=rating` | `"0"`–`"5"`（`null` → `"0"`） |
| `format=text&field=transcribe` / `summarize` / `analyze` | `"1"` 或 `"0"` |
| `field` 不在四者之内 | 400 |

### 3.5 快捷指令选择页（`frontend/pages/ShortcutsTagsPage.tsx`）

- 标签区上方新增四排单选：评级（无 / 1 / 2 / 3 / 4 / 5）、转录（否 / 是）、总结（否 / 是）、解析（否 / 是）。默认「无 / 否」。任何一项改动与标签改动一样立即 `POST …/selection`，请求体带**全部**字段（标签 + 四项），不分两次。
- **隐藏 `group_name === 'Pipeline'` 的标签**（常用区与分组区都隐藏），它们已由单选承载。
- **搜索即创建条**：搜索无结果时显示「创建「{query}」」按钮，下方一行 `EN:` / `ZH:` 可编辑对译输入框 + 「=」按钮（点击把对译置为与搜索词相同）。对译由现有 `handleInputChange` 用的 MyMemory 接口延迟 600 ms 取得；用户一旦手动编辑或点「=」，后到的自动翻译不再覆盖。创建走现有 `POST …/temp-token/{token}/tags`：中文输入 → `name_zh = 词`、`name = 对译或词`；非中文输入 → `name = 词`、`name_zh = 对译或 null`。成功后重新拉标签列表、自动选中新标签、清空搜索。409 沿用现有的结构化提示。
- **选中即清空搜索**：`search` 非空时点中任一标签，`toggle` 之后 `setSearch('')`。

### 3.6 FloatingParse（`frontend/components/TopicInspiration/FloatingParse.tsx`）

三个按钮改为本地布尔状态，`parseShareLink` / `parseBatchLinks` 的 `FetchOptions` 新增
`transcribe? / summarize? / analyze?`，只在为 true 时放进请求体。不再查 `Transcript` 等标签 id、
不再把它们混进 `tag_ids`。`aiTagId` / `toggleAiIntent` 删除。

### 3.7 Chrome 插件（`chrome-extension/popup.js`）

独立后续 PR：标签列表隐藏 Pipeline 组，增加转录 / 总结 / 解析三个开关与评级，随 push 请求
发 3.1 的四个字段。本 spec 只定契约，不在本次计划内。

## 4. 不做的事

- 不改 `download_helpers` 三个链路读取标签的逻辑。
- 不加数据库迁移。
- 不删除 / 停用 Transcript / Summary / Analyze 三枚系统标签（网页资源卡与 mig 220 仍依赖）。
- 不改 `format=text` 的默认输出。

## 5. 测试口径

- 后端 pytest：字段强转与越界；`intent_tag_names` 全排列；`resolve_intent_tag_ids` 只查 system、缺失只告警；`set_rating_step` 调 `update_resource`；`temp_token_router` 选项存取、`field=` 四值、非法 field 400、旧令牌无 `options` 的默认值；`_attach_tags_after_save` 并入意图 id 并写 rating。
- 前端 vitest：`ShortcutsTagsPage` 隐藏 Pipeline 组、四项单选触发带全字段的 POST、无结果时出现创建条并按语言拆 `name/name_zh`、选中后搜索清空；`FloatingParse` 把布尔意图传给 `parseShareLink`。
- 真栈验收（部署后）：用快捷指令调试令牌走一遍 → 选择页勾「转录 + 解析 + 评级 4」→ `GET …/selection?format=text&field=transcribe` 得 `1` → `POST /media/fetch` 带四字段 → 资源上出现 Transcript / Analyze 系统标签、`resources.rating = 4`、Task Center 出现转录与解析任务。

## 6. 快捷指令侧要改什么（用户操作）

「获取选择结果」那一步由一次改为四次 `GET …/selection?format=text&field=<名>`，把得到的
`1/0` 与 `0–5` 分别填进「获取 URL 内容」的请求体字段 `transcribe` / `summarize` /
`analyze` / `rating`；标签仍用原来的 `format=text` 那次请求填 `tags`。
