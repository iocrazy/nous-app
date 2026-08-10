# 分镜三视图主工作面 + Agent 面板收尾 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把设计稿（artifact 24b61005「分镜三视图 + 悬浮 Agent 面板」）落地为两个 PR：PR-A 三视图升级为分镜节点主工作面（视图一场次卡 + 视图三分镜列表表格 + 主面接线）；PR-B Agent 悬浮面板收尾（胶囊换绿 + 四组件单测补齐）。

**Architecture:** PR-A 在 workspace 侧新建组件（复用 `editor/sceneService` 数据层与 `vocab` 字典，不搬 editor 的局部样式 token——两套设计语言不混用）；三视图从「总览附属条」升为 storyboard 节点点击后的主面，深入编辑用场次卡「打开」深链。PR-B 是纯前端小 PR。撤销（五件之五）已拍板延后立项（后端前置：shot 归属列 + run 级逆操作）。

**用户拍板（2026-08-09）**：三视图升主工作面 ✅；撤销延后立项 ✅。

**盘点权威事实**（实施者按此为准，别重新考古）：
| 事实 | 位置 |
|---|---|
| 分段控件（有 `actions` 右插槽给导出用） | `frontend/components/workspace/EpisodeViewTabs.tsx`（换皮面 :42-47,:69） |
| 三视图接线现状：storyboard=入口按钮(:606-639 `episode-view-entry`)、canvas=真组件(:588-593)、shotlist=占位(:594-605) | `frontend/components/workspace/ProjectWorkspace.tsx` |
| 主面门：`showSurfacePanel = showOverview && surfaceViews.length > 0` | `ProjectWorkspace.tsx:509`，视图集来自 `:490-508` |
| 场次数据 `listScenes(scriptId)` / 镜头 `listShots(sceneId)` / `autoStoryboard(sceneId)`（返回 task_id，轮询范式 StoryboardView:153-200） | `frontend/editor/sceneService.ts:74,:312,:446-455` |
| `SceneDoc` 缺 `scene_number` 类型（运行时已透传，`...rest` 展开）；heading_int_ext/location_text/time_of_day 齐 | `frontend/editor/types.ts:27-38`，`sceneService.ts:63-72` |
| Shot 字段：shot_number/shot_type/camera_angle/camera_movement/focal_length/description/thumbnail_url/image_url/status | `sceneService.ts:267-284`；**无时长字段（设计稿表格也没有时长列，不加）** |
| 景别/角度/运动枚举 | `frontend/editor/storyboard/vocab.ts:11-13` |
| 既有场次列 UI（可参考结构，样式别搬——editor 局部 token） | `frontend/editor/storyboard/StoryboardView.tsx:358-431`，headingLine :79-85 |
| 表格可抄范式（都是 ink-* 旧色阶，新表格用 content-*/island-*/line） | `pages/TeamAiUsagePage.tsx:373-401`（有 groupBy+colSpan 空态） |
| 下载原语 | `frontend/utils/download.ts`（URL/blob）；CSV 编码器不存在需新写 |
| Snowflake id 铁律：比较/分组 key 两侧 `String()` 强转 | `StoryboardView.tsx:16-17`、`sceneService.ts:54-62` |
| Agent 面板四件已落地（A7 #1696）：AgentIdentityHeader/ContextCapsule/ToolActivityChips/TurnWriteSummary + QuickActions + shotFocusBus | `frontend/components/agentActivity/` |
| 胶囊现用 info-*（钢蓝），设计稿绿；--ok 三件套双主题齐备有守卫测试 | `ContextCapsule.tsx:56,70,73,88`；`index.css:236-250,:351-365`；守卫 `index.css.test.ts:155-170` |
| agentActivity 四组件无独立单测（仅 surfaces.test.tsx 间接覆盖） | 测试基线节 |
| e2e stubs 无 /scenes /shots /auto-storyboard 路由 | `e2e/helpers/stubs.ts` |
| e2e 最相关断言：ws-ep-storyboard → storyboard-view | `e2e/projects-workspace.spec.ts:321-323` |

## Global Constraints

- **workspace 设计语言**：新组件一律 `content-*/island-*/line/accent-*` + 语义色 token（ok/warn/danger/info/agent），**禁**引入 editor 的 `--ink/--surface` 局部 token、禁旧色相类名（indigo/amber/emerald…）。
- UI 文案英文 + i18n en/zh 齐平（camelCase key）。
- Snowflake id：一切 map key/比较 `String()` 双侧强转。
- tsc 基线 59 个既有漂移不许新增；vitest 全量绿；相关 e2e 绿。
- commit/PR 中文；每 Task 独立 commit；TDD。
- PR-A 分支 `feat/storyboard-triview`、PR-B 分支 `feat/agent-panel-polish`，均从 origin/master 拉。
- 行号基于 2026-08-09 盘点，内容锚定为准。

---

## PR-A: 三视图主工作面（`feat/storyboard-triview`）

### Task 1: 视图一「分镜」场次卡组件

**Files:**
- Create: `frontend/components/workspace/EpisodeSceneBoard.tsx`
- Create: `frontend/components/workspace/EpisodeSceneBoard.test.tsx`
- Modify: `frontend/editor/types.ts`（SceneDoc 补 `scene_number: string | null`）
- Modify: `frontend/public/locales/en.json` / `zh.json`

**Interfaces:**
- Produces: `<EpisodeSceneBoard scriptId={string} onOpenScene={(sceneId: string) => void} />` — 自取数（listScenes + 并行 listShots，范式抄 StoryboardView:105-122），纵向卡片流。
- 每张场次卡：clap 条纹头（CSS repeating-linear-gradient，双主题用 `var(--line)` 系）+ 元数据格 2×2（SCENE=真实 `scene_number ?? 序号` / I-E=`heading_int_ext ?? '—'` / LOCATION=`location_text ?? '—'` / D-N=`time_of_day ?? '—'`，label 用 `text-[10px] uppercase tracking-wider text-content-3`）+ 「Open」（调 `onOpenScene`）与「Auto Storyboard」（`autoStoryboard(sceneId)` + 两击确认 3s 窗口 + `usePoll` 轮询 shots 增长——逻辑搬 StoryboardView:153-200，别 import 它的组件）+ 镜头卡简版列表（`SHOT {scene_number}-{shot_number}` mono 徽标 + description 截断 + thumbnail 缩略,空态两档：有剧本无镜头「Run Auto Storyboard to hand this scene to the agent」/空场景「Scene has no content yet — the agent will ask first」）。
- data-testid：`ep-scene-card-${sceneId}`、`ep-scene-open-${sceneId}`、`ep-scene-auto-${sceneId}`、`ep-scene-shots-${sceneId}`。

- [ ] Step 1 RED：单测（mock sceneService 模块，范式抄 `editor/__tests__/storyboardView.test.tsx:18-46`）：渲染 N 卡、元数据格取真实 scene_number、Open 回调、Auto 两击确认才 dispatch、空态两档。跑 FAIL（组件不存在）。
- [ ] Step 2 GREEN 实现 → 单测过。
- [ ] Step 3 i18n 补 key（`projects.sceneBoard.*`：open/autoStoryboard/confirmAuto/noShots/emptyScene…，en/zh 齐平）。
- [ ] Step 4 Commit（中文）。

### Task 2: 视图三「分镜列表」表格 + CSV 导出

**Files:**
- Create: `frontend/components/workspace/EpisodeShotListTable.tsx`
- Create: `frontend/components/workspace/shotListCsv.ts`（纯函数编码器）
- Create: 对应两个 test 文件
- Modify: locales

**Interfaces:**
- `<EpisodeShotListTable scriptId={string} />` 自取数（同 Task 1 范式；可提一个共享 hook `useSceneShots(scriptId)` 放 `frontend/components/workspace/useSceneShots.ts` 两视图共用——Task 1 若已建则本任务改为消费）。
- 表格七列：Frame(thumbnail_url||image_url 56×34 圆角占位)/Shot(`{scene_number}-{shot_number}` mono)/Type/Angle/Move/Lens(focal_length mono)/Description(truncate)。**无时长列**。
- 组头行：每场一行 `bg-island-2 font-medium`，内容 `{scene_number} — {INT/EXT}. {location} · {D/N}　({shots 数})`，colSpan=7。空场也出组头（0）。
- `buildShotListCsv(groups): string`：纯函数，列头 Scene,Shot,Type,Angle,Movement,Lens,Description；值含逗号/引号/换行按 RFC4180 双引号转义；UTF-8 BOM 前缀（Excel 中文）。
- Export 按钮（表格自带右上或由父挂 EpisodeViewTabs `actions` 插槽——本任务先内置于组件顶部，Task 3 接线时挪插槽）：Blob(text/csv) + `URL.createObjectURL` + a[download]（范式参考 utils/zipExport.ts:102），文件名 `shotlist-{scriptId}.csv`。
- data-testid：`ep-shotlist-table`、`ep-shotlist-group-${sceneId}`、`ep-shotlist-row-${shotId}`、`ep-shotlist-export`。

- [ ] Step 1 RED：`shotListCsv.test.ts`（转义/BOM/空组）+ 组件测（分组行/七列/空态 colSpan/导出点击生成 blob——mock URL.createObjectURL）。FAIL。
- [ ] Step 2 GREEN → 过。
- [ ] Step 3 i18n（`projects.shotList.*`：export/headers…）。
- [ ] Step 4 Commit。

### Task 3: 主工作面接线

**Files:**
- Modify: `frontend/components/workspace/ProjectWorkspace.tsx`
- Modify: `frontend/components/workspace/ProjectWorkspace.test.tsx`（补 surface panel 覆盖——盘点确认现为零）
- Modify: `frontend/e2e/projects-workspace.spec.ts`

**行为定义（用户拍板「升主工作面」）：**
1. `handleSelectNode` 对 `surface==='storyboard'` 的节点：不再 `handleOpenWorkView`，改为进入 Overview 模块并把 surface 面板置顶展开（视图默认 'storyboard'）。`script` 节点行为不变（仍进编辑器）。
2. storyboard 视图：入口按钮（:606-639）替换为 `<EpisodeSceneBoard scriptId={...} onOpenScene={sceneId => handleOpenWorkView('storyboard', {sceneId})} />`——scriptId 从当前集解析（既有 `get_or_create_for_episode` 的前端对应：查 ProjectWorkspace 现有 script 解析链，若无则经 `scriptService`/episodes 数据取该集 script id；确认后如实接）。深链带 sceneId 的打开方式若 handleOpenWorkView 不支持参数，退化为打开编辑器后 shotFocusBus 式滚动可后续做——本期打开到该剧本即可，报告注明。
3. shotlist 视图：占位（:594-605）替换为 `<EpisodeShotListTable scriptId={...} />`，Export 按钮挪到 EpisodeViewTabs `actions` 插槽。
4. `showSurfacePanel` 门保持在 Overview 内（本期不做整页独立路由——「主工作面」语义 = 点节点必达 + 面板成为该节点内容主体），但 storyboard 面板容器高度放开（`min-h` 提升与 canvas 一致）。

- [ ] Step 1 RED：ProjectWorkspace.test 补——点 storyboard 节点后 `episode-view-tabs` 可见且 active=storyboard、`ep-scene-card-*` 渲染（mock services）；shotlist tab 切换渲染表格。FAIL。
- [ ] Step 2 GREEN 接线。
- [ ] Step 3 e2e：`e2e/helpers/stubs.ts` 补 `/scripts/*/scenes*`、`/scenes/*/shots*`、`/scenes/*/auto-storyboard` 路由 stub；`projects-workspace.spec.ts` 的 ws-ep-storyboard 断言从「跳 storyboard-view」改为「展开 ep-scene-card」+ 新增 shotlist 表格断言。相关 4-5 套 e2e 全跑绿。
- [ ] Step 4 Commit。

### Task 4: 全量验证 + 发 PR-A

- [ ] vitest 全量 + tsc 基线 59 比对 + eslint 触碰文件 + e2e 全套（5 个相关 spec）。
- [ ] PR 描述：设计稿链接、升主工作面的行为变化（storyboard 节点点击不再直跳编辑器）、CSV 导出为客户端能力、时长列按设计稿实况不存在故未加。

---

## PR-B: Agent 面板收尾（`feat/agent-panel-polish`）

### Task 5: 胶囊换绿 + 四组件单测补齐

**Files:**
- Modify: `frontend/components/agentActivity/ContextCapsule.tsx`（:56,:70,:73,:88 四处 `info-*` → `ok-*`）
- Create: `ContextCapsule.test.tsx` / `AgentIdentityHeader.test.tsx` / `QuickActions.test.tsx` / `TurnWriteSummary.test.tsx` / `shotFocusBus.test.ts`

- ToolActivityChips 的 KIND_TONE 分色**保留**（比设计稿单一绿更细,不倒退——报告注明）。
- 撤销不做（拍板延后立项）；TurnWriteSummary 头部那段「为什么没有 undo」注释保留并补一行指向延后立项决定。
- [ ] Step 1 RED：五个测试文件（胶囊：渲染场景标签/signal/关闭回调/**断言用 ok-* 类**；身份头：名字/职责/头像 fallback；QuickActions：三档 context 的 chips 集合、点击只填不发；TurnWriteSummary：N 张卡文案/镜号/焦段/点击发 shotFocusBus；bus：pub/sub/无监听降级）。胶囊颜色断言 FAIL（现在是 info-*），其余新测试按现行为 GREEN 起步即可（补测型,报告注明哪些是 RED 哪些是 pin）。
- [ ] Step 2 GREEN（换 token）→ 全过 + `index.css.test.ts` 守卫照常绿。
- [ ] Step 3 Commit + vitest 全量 + tsc 基线 → 发 PR-B（描述注明：五件之五撤销延后立项及后端前置清单）。

## Self-Review 记录

- 设计稿覆盖：视图一场次卡（Task 1 全要素含 clap/元数据格/打开/自动分镜/空态两档）、视图三表格+分组+导出（Task 2）、主面升级（Task 3, 拍板）、面板五件=4 已有+胶囊绿（Task 5）+撤销延后（拍板,记 memory）。时长列：设计稿表格本就没有，Lens 页签的时长是空占位——不加字段。
- 不做：整页独立路由（主面语义按「点节点必达」实现）、editor StoryboardView 改动（保留给编辑器 rail）、结构化上下文 payload（§5.3 另立）、后端任何改动。
- 类型一致性：`EpisodeSceneBoard(scriptId,onOpenScene)`、`EpisodeShotListTable(scriptId)`、`buildShotListCsv(groups)`、`useSceneShots(scriptId)` 各任务引用一致。
