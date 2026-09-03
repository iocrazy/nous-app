# 画布 Library 面板 — 设计（提示词库 + 素材库统一调用）

**日期**：2026-09-03 · **状态**：用户已拍板方案与四项决策，spec 待用户过目 · **范围**：`frontend/features/canvas-core`（新目录 `library/`）、`frontend/components/assets`、`components/resources`、`components/generated` 的复用组件；后端一个只读聚合搜索端点

Mockup（4 屏 + 三方案 + 分期）：https://claude.ai/code/artifact/f2ea2142-e0a4-4e93-ba1d-48e806068e6d
侦察报告（18 条入口 + 15 条实证痛点）：`.superpowers/recon/2026-09-03-canvas-library-recon.md`（在 `feat-canvas-fluency-w1w2` worktree，git-ignored；本 spec §1 摘录了结论）

## 1. 起因与证据

用户：「有没有更好的调用资源库的方式」。对画布做只读侦察，逐条核实：

| # | 现状（file:line） | 后果 |
|---|---|---|
| 1 | 四个入口都叫 **Library**，指四个不同的库：`PromptNodeView.tsx:494`（带提示词的上传件）、`:733`（`@` 的 Library tab = 全部上传件）、`AttachedComposerPanel.tsx:239`（同库、只填正文）、`CanvasComposer.tsx:665`（工作流 JSON）；真正的资产库反而叫「Asset」（`TopNodeBar.tsx:102`） | 用户无法从名字推断会打开什么 |
| 2 | 「加参考图」拾取器 `query` 写死为 `""`（`PromptNodeView.tsx:641`），`ResourcePickerSuggestion` 无输入框 | 喂给模型的参考图只能在前 50 条里按类型滚 |
| 3 | Generated 收件箱从画布**不可达**（canvas-core 没有任何文件读 `generatedService` 列表） | 复用昨天的产出要绕：收件箱 → Save To Uploads → 回画布 → 加参考图 → 滚 |
| 4 | 画布只认 `dataTransfer.files` 拖入（`CanvasSurface.tsx:160-168`）；没有任何侧栏/抽屉；所有拾取器单选即关（`AssetPickerDialog.tsx:127-129`、`PromptNodeView.tsx:302`、`useCanvasMentionPicker.ts:170`） | 五张参考图 = 五轮完整的打开 → 找 → 选 |
| 5 | `@` 选中写 `resource_refs`（文本上下文，`PromptNodeView.tsx:167-180`）；加参考图写 `manual_refs`（真正的 i2i 输入，`:287-305`）；两者渲染同一个组件，界面不说明 | 两个长得一样的拾取，后果相反 |
| 6 | `@` 的搜索走 `/api/v1/resources/search`，kind 词表无 character/prop | 资产不能 `@`；IC 对标项「@Character/@Scene/@Prop」（`2026-06-04-…-reference-catalog.md:83`）仍未建 |
| 7 | 资产的文件勾选与 loadout 在**节点上**（`AssetNodeView.tsx:339-352, 441-480`），max_refs 裁剪结果只在跑完后可见（`:374, :401`） | 选中前不知道会送哪些文件 |
| 8 | `lite` 画布无 Asset 入口（`DragCreateMenu.tsx:178-183`）；`storyboard` 无顶栏无 composer | 两种画布够不到资产 |

IC 的对应行为（行为规格，非代码）：一个全局素材库；提示词模板库是**独立的**右侧面板（来源下拉、搜索、分组 chip、存当前 / 新模板、列表 + 预览、正向 / 完整应用），由节点上的书架图标打开；素材库由节点上的图片图标打开——两者分开。`@` 弹层有输入图 / 资产库两个来源。

## 2. 目标 / 非目标

**目标**
1. 画布上只有**一个**叫 Library 的东西：一个岛式面板，两页——**Prompts**（提示词库）与 **Media**（素材：Assets / Uploads / Generated）。
2. 节点头部的两个按钮**保留**且各管一事（与 IC 一致）：书架 → Prompts 页，图片 → Media 页；两者打开同一面板并带着该节点为目标。
3. Media 页统一：搜索常驻、类型 chip、多选、悬停预览（按当前模型的 max_refs 实时显示会送/被砍的文件）、拖到空白建节点、拖到 prompt 节点加参考。
4. Generated 从画布可达，默认范围 This canvas。
5. `@` 调色板复用同一索引：分组 Assets / Input images / Uploads / Generated；首行写明这次拾取的后果；资产可 `@`。
6. 每一处拾取都在界面上说明后果（参考图 vs 提及 vs 覆盖正文）。

**非目标**
- 不改资产库、上传库、收件箱各自的页面。
- 不做资产的新建/编辑（面板只读 + 「Save current」存提示词模板）。
- 不做跨画布拖放、不做收藏夹（Recent 用本地最近使用即可）。
- 工作流库（底栏「Library」）**不进面板**：它导入整张图，不是素材；本期只改名 Workflows。
- 不改生成链路：参考图仍走 `manual_refs` + 资源参考桥；资产仍走 bundle（P4 已上线）。

## 3. 设计

### 3.1 面板（岛式）
- 位置：画布右侧浮层，`top/right/bottom = 14px`，宽 340px；Prompts 页 600px（列表 + 预览两栏）。圆角、阴影，样式沿用 `.canvas-island`。**不挤压画布**，底下画布照常可平移；面板内滚动，`nowheel nopan nodrag`。
- 开关：顶栏 `Library` 胶囊、快捷键 `L`（与现有 `useCanvasShortcuts` 同表登记；输入框聚焦时不触发）、✕、Esc。宽度与最近页记在 `localStorage`（键 `canvas.library.v1`）。
- 小屏（视口宽 < 1100px）：改为底部抽屉，高 45vh，其余同。
- 目标节点：从节点按钮打开时携带 `target = { nodeId, kind: 'prompt' }`，面板顶部一条绿色 target 条「Adding references to <node title>」/「Applying to <node title>」，✕ 可解除（转为浏览模式）。节点被选中并高亮，面板关闭时取消高亮。
- 画布种类：`smart` 及四个实体画布全量；`lite` 与 `storyboard` 也能开（只提供 Media 页；`storyboard` 无 prompt 节点即无目标模式）。

### 3.2 Prompts 页
- 来源分段：**System**（内置预设，种子化的 `assets` 类型 `prompt`、`scope_id IS NULL`）/ **Mine**（当前团队 scope 的 prompt 资产）/ **This project**（`asset_project_refs` 里挂到当前项目的 prompt 资产）。原「带提示词的上传件」（`fetchPromptAssets` 的 resources 行）本期作为 Mine 的第二数据源合并显示，标 `Upload` 角标；不迁移数据。
- 搜索（300ms 去抖）+ 分组 chip（用资产的 `tags`：Angle / Storyboard / Character / Product / Lighting / … 按数据动态列出，最多 8 个 + More）。
- 列表 + 预览：列表行 = 标题 + 首行 + 分组；预览 = 正向 / 负向 / 参数建议（来自 prompt 资产的 `metadata.params`，无则不显示该段）。
- 动作：**Insert positive**（把正向插到目标节点光标处；无目标时插到面板顶部提示「Select a prompt node」）/ **Apply all**（覆盖正文 + 负向 + 参数；覆盖前若正文非空弹一次确认）/ **Save current**（把目标节点的正文 + 负向存为 Mine 的新 prompt 资产，弹小表单：标题、分组）/ **New**（同表单，空白）。
- 复用：`AssetPromptPicker` 的数据钩子（`fetchPromptAssets`、tag chip）抽成 hook 供面板用；旧的 420px 模态组件在本期结束时删除。

### 3.3 Media 页
- 三库分段（计数常显）：**Assets** / **Uploads** / **Generated**。
  - Assets：类型 chip Character / Location / Prop / Costume / Audio（不含 Prompt——归 Prompts 页）；Scope 切换 This project / All library（`library:'all'` 与今天 `AssetPickerDialog` 相同的显式退出）。
  - Uploads：Image / Video / Audio / Doc；走 `useResourceSearch`。
  - Generated：范围 chip **This canvas**（默认）/ Today / All；默认隐藏中间件（mask / brush / reference 角色，同收件箱规则）；走 `/api/v1/generated` 列表。
- 网格：justified 自适应（复用 `#2092` 的网格组件），多选（点选加 `⌘`、连选 `⇧`），单击 = 选中，双击 = 立即执行默认动作。
- 页脚：`N selected · M files`（M = 目标模型 max_refs 裁剪后真正会送的数量；无目标时不显示 M）+ **Place on canvas** + **Add as references**（有目标时为主按钮；无目标时只有 Place）。
- 悬停预览（400ms）：大图、就绪状态、loadout 下拉（资产）、被几张画布引用、**文件表**（文件 / 槽位 / 当前模型下 Sends 或 Cut · max_refs）；换模型的后果一句话。数据来自 `GET /assets/{id}` + 现有 `useModelCapabilities`。
- 拖放：`dataTransfer` 新增 MIME `application/x-nous-library`（JSON：`{ items: [{ store, id, url?, kind }] }`）。落点判定在 `CanvasSurface` 的 `onDrop`：
  - 空白处 → 建节点：asset → `asset` 节点（沿用 `AssetPickerDialog.onPick` 的建节点逻辑）；upload / generated → `media` 节点（沿用 `dropCreate`），多项按 `assetPlacement` 的 lane 布局。
  - prompt 节点上 → 加参考（`manual_refs`，经 `mediaImport` 铸造 durable URL）；按住 `⌥` 松手 → 作为提及插入正文（asset → `@` chip 走 bundle；图片 → 图片 chip）。节点在拖入悬停时整体高亮并显示后果文案（Add as reference / Insert as mention）。
  - `media` 节点上 → 追加为该卡片的一项（与文件拖入同）。

### 3.4 `@` 调色板
- 一个组件替换 `CanvasMentionPicker` + `MentionImageGrid`：首行写明后果（「Mentioning an asset sends its reference files at run. Mentioning an image adds it as a reference. Plain text stays text.」）；分组 **Assets / Input images / Uploads / Generated · this canvas**；`⇥` 切组；未就绪资产可见但标 Not ready。
- 资产 `@` = 内联 chip（已拍板：Run 时按 bundle 自动投递，主槽为参考；缩略图进输入图条）。图片 `@` = 现有 `promptImageRef` chip。
- 贴身 composer 的 `@` 与 prompt 节点共用此组件（补齐它今天缺的库来源）；两处弹出方向统一为**向下**（贴身 composer 的现状），prompt 节点空间不足时翻转。
- 统一索引：前端 hook `useLibrarySearch(query, { stores, kinds, scope })` 并行请求三个列表端点并合并；不新增后端聚合端点（三库权限模型不同，服务端聚合会引入第四套过滤规则）。

### 3.5 入口收敛
| 库 | 原入口 | 现在 |
|---|---|---|
| Assets | Asset 胶囊、Project Assets、drag-create Asset 卡 | 面板 Media 页；drag-create 卡保留（打开面板并聚焦搜索）；Project Assets 等价于 Scope=This project + 全选 + Place |
| Uploads | `@` Library tab、加参考图弹层 | 面板 Media 页；节点图片按钮打开的目标模式 |
| Generated | 无 | 面板 Media 页 |
| Prompts | Library 胶囊、贴身 composer Library、资源页 Send to Canvas | 面板 Prompts 页；Send to Canvas 不动 |
| Workflows | 底栏 Library | 底栏改名 **Workflows**，不进面板 |

删除：`AssetPickerDialog`（被面板取代；drag-create 卡改为打开面板）、`AssetPromptPicker` 模态、`CanvasMentionPicker`、`MentionImageGrid`。保留但改指：`TopNodeBar` 的 Asset / Project Assets 胶囊合并为 `Library`。

### 3.6 状态与数据
- 面板状态在 `library/libraryStore.ts`（zustand，独立于画布 store）：`open / page / mediaStore / query / kinds / scope / selection / target / width`。选中项只存 `{store, id}`，不复制行。
- 不新增表、不新增列。「Save current」写 `POST /api/v1/assets`（type=prompt）。
- 生成链路零改动：参考图仍是 `manual_refs`；资产 chip 的 bundle 投递沿用 P4。

### 3.7 错误与空态
- 三库任一请求失败：该库 tab 显示错误行与 Retry，其它库照常；不整面板报错。
- 目标节点在面板打开期间被删除：target 条自动解除并 toast「Target node was removed」。
- 拖到不接受的落点（例如 output 节点）：落点不高亮、松手无动作、面板选中态保留。
- 参考图配额已满（max_refs）：页脚主按钮禁用并写明「3 / 3 references used · remove one on the node」。

## 4. 验收（可证伪）
1. **命名**：canvas-core 与相关组件里可见文案「Library」只出现在面板与顶栏胶囊（测试：grep i18n 键与 JSX 文案）。
2. **搜索**：从节点图片按钮打开的 Media 页有输入框，输入后列表按查询过滤（组件测试）。
3. **多选拖放**：选中 3 项拖到 prompt 节点松手 → 该节点 `manual_refs` 增加 3 条 durable URL；拖到空白 → 生成 3 个节点（store 级测试 + `onDrop` 单测，用真实 `dataTransfer` 形状）。
4. **⌥ 提及**：同一拖放按住 ⌥ → 正文追加 chip、`manual_refs` 不变。
5. **Generated 可达**：面板 Generated 默认列出本画布产出（mock `/api/v1/generated` 用真实响应形状）。
6. **后果可见**：`@` 调色板首行与页脚按钮文案在快照/文本断言里存在；加参考图与提及走不同的数据字段（断言 `manual_refs` vs 正文 chip）。
7. **预览裁剪**：目标模型 max_refs=3、资产 4 文件 → 预览文件表 3 行 Sends、1 行 Cut。
8. **Prompts 两动作**：Insert positive 只改正文并保留原文；Apply all 覆盖正文 + 负向 + 参数。
9. **旧组件删除**：`AssetPickerDialog` / `AssetPromptPicker` / `CanvasMentionPicker` / `MentionImageGrid` 文件不存在且无引用（tsc 通过）。
10. **真栈**：部署后 `npm run e2e:prod` 走查含「打开 Library → Media → 拖一张到 prompt 节点」一步。

## 5. 决策记录
| 决策 | 选了 | 备选 | 理由 |
|---|---|---|---|
| 面板形态 | 岛式浮层 | 贴边挤压画布 | 用户拍板；与 `.canvas-island` 一致；不改视口尺寸 |
| 提示词与素材 | 同一面板两页 | 合成一个库 | 用户拍板，与 IC 一致：书架 = 提示词、图片 = 素材 |
| 节点两个按钮 | 保留 | 去掉书架 | 用户指出 IC 的两分是对的；我最初的合并提议撤回 |
| Generated 默认 | This canvas | Today | 用户拍板 |
| 拖到 prompt 默认 | 加参考图；⌥ = 提及 | 弹二选一 | 用户拍板；参考图是 90% 用法 |
| 索引 | 前端并行三路 | 后端聚合端点 | 三库权限模型不同，服务端聚合会造第四套过滤 |
| 工作流库 | 不进面板，改名 | 进面板 | 它导入整张图，不是素材 |
| 带提示词的上传件 | 作为 Mine 的第二源显示，不迁数据 | 迁成 prompt 资产 | 本期不动数据；迁移另立 |

## 6. 分期（各自可独立上线）
- **P1 · 一处实现**：`useLibrarySearch` + `LibraryGrid`（搜索/chip/多选/后果头/页脚）；替换加参考图弹层与 `@` 的 Library tab；`@` 分组含 Assets / Generated；四个「Library」改名。
- **P2 · 面板**：岛式面板壳 + Media 页 + 拖放 MIME + 悬停预览 + Scope；顶栏 `Library` 胶囊与 `L`；删除 `AssetPickerDialog`。
- **P3 · Prompts 页 + 键盘**：Prompts 页（三来源、预览、四动作）；贴身 composer 接同一套；⌘K「Add from library…」；lite / storyboard 开面板；删除 `AssetPromptPicker` / 旧 mention 组件。

## 7. 开放问题（不阻塞 P1）
- System 预设提示词的种子内容由谁写：先复用 IC 截图里的十类作为骨架，文案本期用占位英文，待用户提供。
- 「Save current」是否也把负向存进去：本期存（prompt 资产已有 `negative` 字段）。
