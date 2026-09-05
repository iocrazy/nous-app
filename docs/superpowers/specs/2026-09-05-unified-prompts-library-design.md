# 统一提示词库（Library P3）— 设计

**日期**：2026-09-05
**前置**：`2026-09-03-canvas-library-panel-design.md`（P1/P2 已上线，PR #2102 / #2105）
**设计稿**：`2026-09-05-canvas-library-prompts-page-mockup.html`（两个界面并排，用户已认可）
**状态**：用户已认可方向（2026-09-05），待实施计划

## 1. 起因与证据

P2 上线后资产库的「提示词」tab 与画布面板的 Prompts 页都是空的，而用户的提示词实际全长在图片身上。2026-09-05 对生产库实查：

| 提示词在哪 | 行数 | 说明 |
|---|---|---|
| `assets` 里 `asset_type='prompt'` | 0 | 资产库「提示词」tab 与旧 spec §3.2 读的表，从没有人创建过 |
| `resources.gen_prompt*`（单图） | 12 | 7 行带 `gen_params`（画布生成 / A1111 元数据提取），5 行只有 `gen_prompt_json`（AI 打标 caption） |
| `resources.slide_prompts`（图集逐张） | 1 | 每张一条 `{en, zh, neg_en, neg_zh}`，按 slide 文件名键控 |
| 没有任何提示词的资源 | 1568 | |

这是**建模分裂**而不是显示 bug：「提示词资产」被定义成独立模板实体，而真实语料是「图片附带的提示词」。两边互不相通，一边空、一边只能当图片看（资源卡上的 `PromptBadge`）。

同一份数据还有三个写入方，写完谁也不记得是谁写的：`upload_postprocess`（PNG 元数据提取）、`promote_generated_media_service`（画布生成落库）、`backfill_resource_gen_params`（回填）写「生成参数级」的提示词；`caption_asset` / `caption_slide` 写 AI caption；`ResourceDetailPage` 的 PATCH 写用户手打的。三者复用价值差很多，现在只能从「哪几列碰巧非空」反推。

## 2. 目标 / 非目标

**目标**

1. 一个概念：**提示词 = 任何能驱动生成的文字**，不论它今天存在哪。三种存放处归一成一种条目（§3.1），一个后端端点，两个界面都只读它。
2. 资源库「提示词」页替代空 tab：文字为主、图片为辅，按形态与来源筛选，可存为模板、可送到画布（§3.3）。
3. 画布面板 Prompts 页：选、看、插入、全量应用、存为模板；图集逐张插入（§3.4）。
4. 来源（typed / extracted / captioned）成为**存储列**并回填，不再推断（§3.2）。
5. 删除旧 420px 模态 `AssetPromptPicker` 及其专属数据路径（浏览器直连 Supabase 的 `fetchPromptAssets`）。

**非目标**

- 不迁移数据。12 行图片提示词不会变成 `assets` 行；「存为模板」是用户主动的提升路径。
- 不改资源库的媒体视图。图片为主、提示词只是角标，那个侧重本来就对。
- 不做逐张的来源记录（`slide_prompts` 条目级 origin）。图集整行一个来源，见 §8。
- 不动 storyboard / classic 画布的面板挂载。它们没有可瞄准的 prompt 节点，落点是分镜卡，另立。
- 不替换贴身 composer 的 `@` 图片网格 `MentionImageGrid`（§3.6 现状修正）。

## 3. 设计

### 3.1 读模型 PromptEntry（后端归一，前端不拼）

```
PromptEntry {
  key:        "template:<assetId>" | "image:<resourceId>" | "album:<resourceId>"
  form:       "template" | "image" | "album"
  origin:     "typed" | "extracted" | "captioned" | null      # template 恒为 "typed"
  title:      string            # template: assets.name；image/album: resources.filename 去扩展名
  tags:       string[]          # template: assets.tags 所有组的值摊平；image/album: []（本期）
  positive_en / positive_zh / negative_en / negative_zh: string | null
  params:     object | null     # resources.gen_params 原样透传；template 为 platform_params 或 null
  thumbs:     [{ url: string, kind: "image" }]   # 0..3 个，见下
  slides:     null | [{ name, url, positive_en, positive_zh, negative_en, negative_zh }]   # 仅 album
  source:     { store: "assets" | "uploads", id: string }
  updated_at: string
}
```

- **缩略图**：template → `cover_file_id` 的 `/api/v1/resources/{id}/cover`，没有则 `examples` 槽前 3 个文件的 cover，再没有则空数组；image → 自己的 cover；album → `/api/v1/media/{media_id}/slides` 列表的前 3 张（走既有 `media_slides_router.list_slides`）。缩略图 URL 是相对路径，前端用 `mediaSrc()` 加前缀，与 `AssetPromptPicker` 今天一样。
- **slides**：`slide_prompts` 的每个键一条，`url` 为 `/api/v1/media/{media_id}/slides/{name}`；**没有文字的 slide 也在列表里**（`positive_*` 全空），前端灰显禁用，这样「6 张里 5 张有」的计数是诚实的。
- **来源**：见 §3.2；template 恒为 typed（人写的就是模板）。
- **分段**：`segment=mine`（scope 下自己的：template 为 `assets.scope_id=scope AND is_system_preset=false AND in_library=true`；image/album 为 `resource_items.scope_id=scope` 且 `has_prompt_expr()` 为真且未回收）；`segment=project`（template 走 `asset_project_refs.project_id`；image/album 走 `canvas_resource_refs` ⨝ `canvases.project_id`，即被该项目任一画布引用过的资源）；`segment=system`（`is_system_preset=true` 的 prompt 资产）。
- **排序**：默认 `updated_at desc`，**但 captioned 排到所有非 captioned 之后**（caption 描述图片，不是做出图片的文字）。
- **端点**：`GET /api/v1/prompts?scope_id=&segment=mine|project|system&project_id=&form=&origin=&q=&limit=&offset=` → `Envelope[{ items: PromptEntry[], total: int, by_form: {template,image,album}, by_origin: {typed,extracted,captioned} }]`；`GET /api/v1/prompts/counts?scope_id=` → `{ mine: int, project: {<project_id>: int}?, system: int }`（只要 `mine` 与 `system`，`project` 需传 `project_id`）。`q` 匹配 title / positive_en / positive_zh / tags，`ILIKE`，转义 `%_\`。
- **权限**：与 `/assets` 同一道闸 `_gate`（scope 成员），读 Resources 的语句必须在 `system_request_scope` 或带 `read_scope()` 的用户 scope 下执行——生产 `SCOPE_ENFORCE_RESOURCES=True`，单测抓不到（见 memory `reference-workflows-reading-resources-need-system-scope`）。

**现状修正**：`fetchPromptAssets`（`resourceService.ts:243`）是浏览器直连 Supabase 查 `resources`，出错吞成 `[]`（RLS 下「拿空列表且不报错」）。它和它唯一的消费方 `AssetPromptPicker` 一起删除；本 spec 之后前端没有任何地方直接查提示词。

### 3.2 来源列 `resources.prompt_origin`

- 迁移 `453_resources_prompt_origin.sql`：`ADD COLUMN prompt_origin text CHECK (prompt_origin IN ('typed','extracted','captioned'))`，可空；`NOTIFY pgrst, 'reload schema'`。
- **规则：来源跟着正向文字的最后一个写入方走。** 每个写 `gen_prompt` / `gen_prompt_zh` / `slide_prompts` 的地方同一批 patch 里写 `prompt_origin`：
  - `resources_crud_router.update_resource`（用户 PATCH，含 `ResourcePromptSection` 的正向 / 逐张编辑）→ `typed`。`prompt_origin` **不进** `ResourceUpdate` 请求体，服务端在 `update_data` 含任一提示词键时自己写。
  - `upload_postprocess`（PNG 元数据）、`backfill_resource_gen_params`、`promote_generated_media_service`（画布生成落库）→ `extracted`。
  - `caption_asset`、`caption_slide` → `captioned`。
- **回填** `backfill_resource_prompt_origin`（DBOS，`dry_run=True` 默认，`run_user_id`，注册进 `_BACKFILLS`）：对 `prompt_origin IS NULL AND has_prompt_expr()` 的行：`gen_params` 非空对象 → extracted；否则 `gen_prompt_json` 非空 → captioned；否则 `slide_prompts` 非空 → captioned（后端唯一写入方是 caption_slide）；否则 → typed。
- 读模型对 `prompt_origin IS NULL` 的行返回 `null`，前端按 captioned 排序、不显示来源标签。回填跑完后生产应为 0 行 null。

### 3.3 资源库 → 提示词页（`/resources/assets/prompt`）

设计稿第 2 节。`AssetsView` 在 `assetType === 'prompt'` 时渲染 `PromptsShelf` 而不是 `AssetShelf`，其余类型不动。

- **筛选行**：形态分段（All / Templates / Images / Albums，带计数）、来源分段（Any origin / Typed / Extracted / Captioned，带计数）、Project 下拉（已有的 `assetFilters` 项目下拉复用）、Sort（Recently updated / Title）。筛选进 URL search params（`form=`、`origin=`、`project=`、`sort=`），与 `AssetShelf` 同一约定。
- **卡片**（文字为主）：标题行 = 标题 + 形态标签 + 来源标签（extracted 附 `params.tool` / `params.model` 的短名）；正文 = 正向前 3 行等宽字体（`line-clamp-3`），负向 1 行灰字；右侧 64px 缩略图（image 一张；album 叠图加张数；template 无图则虚线空框）；页脚 = 参数 chip（ratio / size / steps / cfg / seed / sampler，缺省不画）+ 语言可用性（`EN · 中` / `EN only` / `中 only`）；操作行 = `Send to canvas` / `Save as template`（template 行没有此按钮）/ `Open`（跳资源详情或资产详情）。
- **captioned 卡**：正文灰字，来源标签斜体，不画参数行，脚注「No parameters — this text describes the picture, it did not make it」。
- **图集卡**：通栏，标题行带「N slides · M have text」，默认折叠（叠图）；展开后每张一行（40px 缩略图 + 文字 2 行 + 文件名 + 语言）+ 自己的 `Send`；没文字的行灰显、`Send` 禁用；卡片操作行多一个 `Send all with text`。
- **Send to canvas**：复用 `SendToCanvasModal`（`positive` / `negative` 按当前语言侧）；图集逐张 Send 传该张的文字；`Send all with text` 逐张调用同一路径生成多个 prompt 节点。
- **Save as template**（§3.5）。
- **计数一致**：侧栏 `assets.types.prompt` 与 tab 上的数字都改读 `/prompts/counts` 的 `mine`；`/assets/counts` 的 `prompt` 键不再被前端使用（后端不改，避免 `AssetCounts` 契约漂移）。
- **空态**只在 `total === 0` 且无筛选时出现，文案说明三种来路：「No prompts yet — upload a picture that carries generation metadata, run a caption, or save one from a canvas.」；筛选后为 0 用「No prompts match these filters」；加载失败用 `AssetShelf` 同款的错误行 + Retry，**不得**与空态同形。

### 3.4 画布面板 → Prompts 页

设计稿第 3 节。`LibraryPanel` 在 `page === 'prompts'` 时渲染 `LibraryPromptsPage`（替换占位 `library-prompts-stub`）。

- **宽度**：进 Prompts 页 `setWidth(600)`，回 Media 页 `setWidth(340)`（`width` 已持久化；`setWidth` 在 P2 就为此保留）。<1100px 走既有抽屉。
- **分段**：Mine / This project / System。顺序 Mine 在前（语料是用户自己的图）。System 分段**仅在 `counts.system > 0` 时渲染**（预设种子文案未到位前不显示一个空分段——用户拍板项之一，取「不显示」）。计数只画在 Mine 与 This project 上。
- **搜索**：复用面板的 `library-search`（`query` 已在 store）。
- **后果行**：「Insert positive adds to your text · Apply all replaces body, negative and params」，不随选择变。无目标时：「Select a prompt node to insert or apply」。
- **chip 行**：形态 chip（All / Templates / Images / Albums）在前，细线，然后是分组 chip（来自已加载行的 `tags` 频次前 8 + More）。
- **列表**（左 250px）：行 = 36px 缩略图（album 叠图 + 张数；template 无图为虚线空框）+ 标题 + 首行 + 右侧形态标签；captioned 行灰字。键盘 `↑↓` 移动、`↵` Insert、`⇧↵` Apply all、`→←` 在图集内切 slide、`⇥` 切分段、`/` 聚焦搜索、`Esc` 先取消打开的确认 / 表单再关面板。
- **预览**（右）：标题 + 形态 + 来源 + `EN / 中` 切换（持久化进 store 的 `Persisted`）；缩略图行（56px，选中 slide 高亮）；image/template：Positive（等宽，保留换行）/ Negative / Params 块（Params 块标「applied only by Apply all」）；album：Slides 列表替换 Positive 块，每行自己的 `Insert`，选中 slide 的 Negative / Params 显示在其下。
- **动作行**：`Insert positive`（album 为 `Insert slide <name>`）/ `Apply all`（album 为 `Apply all from <name>`）/ `Save as template…`（template 行不显示）/ 快捷键提示。无目标时前两个禁用并提示「Pick a prompt node first」。
- **页脚**：`Save current…` / `New…` + 「N prompts · M from pictures」。
- **语言缺失**：所选语言侧为空时显示另一侧，标 `EN only` / `中 only`，切换按钮把缺的一侧置灰；Insert / Apply 用显示的那一侧。

**四个动作的语义**

| 动作 | 目标必须 | 做什么 | 撤销 |
|---|---|---|---|
| Insert positive | 有 | 通过 `mentionHandles` 拿到目标节点编辑器句柄，`insertText(positive)`。编辑器未挂载（`EditorGoneError`）→ 退化为 `patchNode(body = body + "\n" + positive)` 并 toast「Added to the end — the card was off screen」。**从不**消费正文里已有的 `@` | 编辑器事务 / patchNode 各自一步 |
| Apply all | 有 | 正文非空先内嵌确认；确认后一次 `setNodes` 写 `body`、`negative_body`、`gen.ratio`（仅当 `params.width/height` 可约成节点支持的比例预设时）；template 的 `platform_params` 本期不映射 | `setNodes` 经 `noteDocumentEditStarting` 进历史，一次 `⌘Z` 全回 |
| Save current | 有 | 预览列换成表单（Title / Group / Positive / Negative 预填自目标节点），`createAsset({asset_type:'prompt', source:'manual', tags:{group:[<Group>]}, prompt_*})` | 资产可在资产库删 |
| New | 否 | 同表单，空白 | 同上 |

**现状修正**：`LibraryTarget` 是刻意的一员联合 `{kind:'prompt'}`；本期不扩展。贴身 composer 见 §3.6。

### 3.5 存为模板（提升路径）

对 image / album 条目：预览列换成表单，Title 预填标题、Group 预填空、Positive / Negative 预填条目文字（图集为勾选 slide 的文字按 `1.` `2.` 编号拼接，可编辑）、Examples = 勾选的缩略图（image 只有一张且默认勾；album 默认全勾有文字的）。提交：

1. `POST /assets`：`{asset_type:'prompt', name, source:'manual', prompt_positive, prompt_negative, prompt_positive_zh, prompt_negative_zh, tags:{group:[<Group>]} }`（Group 为空则 `tags:{}`）。
2. `POST /assets/{id}/files`：image → `{resource_id, slot:'examples'}`；album → 只挂图集资源本身一条（slide 不是 resource，挂不了；缩略图由读模型从 slides 端点取）。
3. `PATCH /assets/{id}`：`{cover_file_id: <resource_id>}`。
4. 成功后列表切到 Mine、选中新行、toast「Saved to Mine」。任一步失败 → toast 带 `GeneratedApiError` 的 code，**不**回滚已建资产（用户可见、可删，比静默半成品好）。

文件不搬家：`asset_files` 只是关联，与资产库「文件只挂关联不搬家」原则一致。资源库提示词页的 `Save as template` 走同一段代码（放在 `frontend/services/promptsService.ts::saveAsTemplate`）。

### 3.6 入口

| 入口 | 行为 |
|---|---|
| prompt 节点书架按钮（`PromptNodeView.tsx:626`） | `openPanel({ page:'prompts', target:{kind:'prompt', nodeId, title}, focusSearch:true })`；删除 `libraryOpen` state 与 `AssetPromptPicker` 挂载 |
| 面板 Prompts 页签 | `setPage('prompts')`，无目标 |
| `⌘K` | `buildCanvasCommands()` 加一条 `{ id:'library-add', title:'Add from library…', hint:'L' }`，`run` = `useLibraryStore.getState().openPanel()`（记住的页），`enabled` = 恒真（读操作） |
| 贴身 composer 书架（`AttachedComposerPanel.tsx:238`） | **现状修正**：composer 的正文是组件本地 state，不是节点，面板的目标机制够不着它；其 run 路径也不消费资产提及。本期把它的书架按钮改为 `openPanel({page:'prompts'})`（无目标，用户手动复制），并删掉它挂的 `AssetPromptPicker`；`MentionImageGrid` **保留**（换成资产感知的 `PromptMentionPicker` 会提供 run 忽略的 chip = 静默 no-op）。composer 接入目标机制另立 |

### 3.7 删除清单

`AssetPromptPicker.tsx`（+ `.test.tsx`）、`loadPromptAsset.ts`（+ `.test.ts`）、`resourceService.fetchPromptAssets` 与 `PromptAsset` 类型（+ `resourceService.promptAssets.test.ts`）、`PromptNodeView.library.test.tsx`（改写为面板入口测试）、`utils/promptTriggerTags.ts` 若仅被 picker 使用。`libraryRemovals.test.ts` 的 `GONE` 加 `smart/nodes/AssetPromptPicker.tsx` 与 `smart/loadPromptAsset.ts`。`i18n` 键 `canvas.assetPromptPicker.*` 从 en/zh 删除。

## 4. 验收（可证伪）

1. **读模型**：对生产库 `GET /prompts?scope_id=<调试账号个人 scope>&segment=mine` 返回 `total ≥ 1` 且每条 `form/origin/thumbs` 与 §3.1 一致；`by_origin` 之和 = `total`。
2. **来源列**：回填 dry-run 报告的分布 = §1 表（7 extracted / 5 captioned / 1 captioned album / 0 typed）；live 后 `SELECT count(*) FROM resources WHERE prompt_origin IS NULL AND (has_prompt)` = 0。
3. **写入方**：在资源详情页改一段正向 → 该行 `prompt_origin='typed'`；对一张 A1111 PNG 走上传 → `extracted`；跑一次 caption → `captioned`。
4. **资源库页**：`/resources/assets/prompt` 不再是「Nothing here yet」；形态 / 来源分段计数之和等于 total；图集卡展开后无文字 slide 的 Send 禁用；captioned 卡不画参数行。
5. **面板 Insert**：瞄准一个正文为 `draft @` 的节点，Insert positive 后正文为 `draft @` + 正向，`@` 仍在；`manual_refs` 不变。
6. **面板 Apply all**：正文非空时先出确认；确认后 body / negative_body 被替换，`⌘Z` 一次全回。
7. **图集逐张**：选中 slide 002 Insert 只插 002 的文字；`→` 切到 003 后 Insert 插 003 的。
8. **Save as template**：从一张 image 存模板后，Mine 分段出现新行、形态 template、缩略图为该图；资产库「提示词」tab 计数 +1；`asset_files` 有一条 `slot='examples'`。
9. **删除**：全仓库 `grep -rn "AssetPromptPicker\|fetchPromptAssets\|loadPromptAsset" frontend --include='*.ts*'` 为空（除 removals 守卫自身）；`canvas.assetPromptPicker` 键不在 en/zh。
10. **命名 / i18n 守卫**：`libraryNaming.test.ts` 与 `libraryI18n.test.ts` 绿，新增 `canvas.library.*` 键 en/zh 都有。
11. **System 分段**：预设数为 0 时面板不渲染 System 分段；插入一条 `is_system_preset=true` 的 prompt 资产后出现。
12. **真栈**：`npm run e2e:prod` 全过；手工按 5–8 各做一次。

## 5. 决策记录

| 问题 | 决定 | 备选 | 理由 |
|---|---|---|---|
| 三种来源怎么合 | 后端读模型归一，两个界面只读它 | 前端各自拼三张表 | 两个消费方读两套表第一天就会漂；spec §3.2 已禁浏览器直查 |
| 来源怎么知道 | 存储列 `prompt_origin`，写入方各自写，回填一次 | 从哪几列非空推断 | 推断是会烂掉的隐式规则；caption 与生成参数的复用价值差太多 |
| 图集插入 | 逐张 | 合并整组 | 用户拍板；合并会丢「哪段文字做出哪张图」 |
| 图片提示词进模板库 | 用户主动 Save as template，文件挂 `examples` 槽 | 脚本迁移 12 行 | 资产库原则「文件只挂关联不搬家」；靠使用长出来的模板库比脚本灌的更值钱 |
| System 分段空时 | 不渲染 | 显示空分段 | 用户拍板项，一个空分段是没有信息的控件 |
| captioned 默认 | 显示、灰显、排最后 | 默认藏在筛选后 | 用户拍板项；藏起来等于「未知折叠成没有」 |
| 媒体视图 | 不动 | 也文字化 | 同一份数据不同侧重是整个设计的前提 |
| composer | 书架开面板（无目标），`MentionImageGrid` 保留 | 扩展 target 联合 | composer 正文是本地 state；run 不消费资产提及，换资产感知的 picker 会造静默 no-op |
| Apply all 的参数 | 只映射可约成节点比例预设的 `width/height` → `gen.ratio` | 全量映射 steps/cfg/seed | 节点没有这些字段；映射不存在的东西是假承诺 |
| Group 存哪 | `assets.tags = {group:[<name>]}` | 新列 | `_tag_match` jsonpath 已按「任一组含此值」匹配，`?tag=` 直接能筛 |

## 6. 分期

- **P3-A · 数据**：迁移 453 + 五个写入方 + 回填 workflow + 读模型端点（`/prompts`、`/prompts/counts`）。独立可上线，前端无感。
- **P3-B · 资源库提示词页**：`PromptsShelf` 替换 `assetType==='prompt'` 的 `AssetShelf`；`promptsService.ts`；计数改读 `/prompts/counts`；Save as template。
- **P3-C · 画布面板 Prompts 页**：`LibraryPromptsPage` + 四动作 + 图集逐张 + 宽度切换 + 键盘 + 语言切换 + `⌘K` + 书架入口 + 删除清单。

三期各自可独立合并上线；B 与 C 都只依赖 A。

## 7. 开放问题（不阻塞）

- System 预设提示词的种子内容仍由用户提供；A 期让端点支持 `segment=system`，种子文件另提 PR。
- `slide_prompts` 条目级来源（一张图集里既有 caption 又有手改）——本期整行一个来源，见 §8。
- 资源库提示词页的「Send all with text」一次生成多个节点的落点排布，沿用 `SendToCanvasModal` 的既有逻辑逐个调用，不做专门排版。

## 8. Known Limitations and Deferred Work

- **图集来源粒度**：`prompt_origin` 是行级。一张图集若被 caption 后又手改了两张，整行仍显示最后写入方。要做条目级需要 `slide_prompts[name].origin`，两处写入方（`caption_slide`、`ResourcePromptSection` PATCH）都要带上，留待有真实需求时。
- **图集存模板的 examples**：只能挂图集资源本身（slide 不是 `resources` 行），模板缩略图因此来自 slides 端点而非 `asset_files`。
- **"This project" 对图片的定义**是「被该项目任一画布引用过」（`canvas_resource_refs`），不是「传到该项目的库里」——后者在 `libraries.scope_type='project'` 上，但上传路径今天不一定写它。两种定义并存时以画布引用为准，因为它回答的是「这个项目用过这段提示词吗」。
- **贴身 composer** 不接目标机制（§3.6）。
