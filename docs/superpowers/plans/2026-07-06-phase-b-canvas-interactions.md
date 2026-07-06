# Phase B — 画板交互增强（NodesView Interaction Batch）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 Phase B 编辑器的节点画布（`frontend/editor/nodes/NodesView.tsx`）从"极简投影"升级为具备常见无限画布手感的画板：框选群移、MiniMap、吸附网格+对齐参考线、键盘体系、chapter 拖拽持久化、scene 卡片分镜缩略图。

**Architecture:** 全部在既有 @xyflow/react ^12.10.1 受控画布上做——大半缺口是 xyflow 内置能力没开（SelectionMode/MiniMap/snapGrid/multiSelectionKeyCode/onlyRenderVisibleElements），对齐参考线是唯一需要自实现的纯函数+overlay。后端唯一改动：`ScriptChapterUpdate` 暴露 `position_x/position_y` 两个 Optional 字段（`script_chapters` 表列已存在，`svc.update_chapter` 是 model_dump 透传，零 repo 改动）。

**设计输入（两份侦察报告，scratchpad `ic-survey.md` + `canvas-inventory.md`）：**
- Infinite-Canvas（`/Volumes/program/project-code/github-repos/Infinite-Canvas`）交互思路参考——⚠️ LICENSE 禁商用，**clean-room 铁律：只借鉴行为，零代码移植**。借鉴：框选消歧（点击 vs 拖动阈值/相交即选）、光标锚定缩放（xyflow 内置已满足）、minimap 可点击视口。**明确否定**：它没有对齐参考线（此项参照 xyflow 官方 helper-lines example 的思路自实现，xyflow 是 MIT）；它的 Shift 刀模式/放大镜/拖线造节点不做。
- 仓内 `features/storyboard/Canvas.tsx` 有全套富交互先例可对照行为预期，但 **NodesView 是 clean-room（`sceneNodeMapper.ts` 头注释禁 import 旧 store），只对照行为、不 import 它的任何东西**。

**明确不做（defer）：** 画布级 undo/redo（位置是装饰性数据，PATCH 已持久；元素级历史归 script_ops 台账）、连线编辑/拖线造节点（边是 mapper 单向投影）、Alt 拖复制 scene（复制场景=真实剧本 op，归 Phase 5）、右键上下文菜单（本批先键盘+控件，菜单等 Phase 5 协同一起设计）、旧 ScriptCanvas / storyboard Canvas 两套画布不动。

## Global Constraints

P1-P4 全部 Global Constraints 继承：i18n en/zh 键集全等（含复数后缀）、零 emoji、双主题（禁 zinc）、`npx vitest run editor/ --no-file-parallelism` 是唯一判定门、#1006 int-vs-str（sceneId/chapterId 比较必 String() 双侧收敛）、flush-on-unmount（防抖写切视图必冲刷）、**Vercel 限流期间 PR 照常合并但生产部署延迟——CI 监控忽略 Vercel check，视觉过场进"待 Vercel 窗口"队列（task #49）**。无新 workflow、无新表、无 flag（画布交互无成本面，且 nodes 视图已在 VITE_FEATURE_SCRIPT_V2 之内）。

## File Structure

```
frontend/editor/nodes/NodesView.tsx            (modify: 开内置能力+接快捷键+群移持久)
frontend/editor/nodes/alignmentGuides.ts       (new: 纯函数 computeAlignmentGuides + snap)
frontend/editor/nodes/GuideOverlay.tsx         (new: 参考线 SVG overlay)
frontend/editor/nodes/useCanvasShortcuts.ts    (new: 键盘 hook——nudge/zoom/fit/select-all/esc)
frontend/editor/nodes/SceneFlowNode.tsx        (modify: shot 缩略图条)
frontend/editor/nodes/sceneNodeMapper.ts       (modify: chapter 位置读取已有——仅补 width 常量导出)
frontend/editor/sceneService.ts                (modify: +updateChapterPosition)
frontend/editor/nodes/nodesView.test.tsx       (modify)
frontend/editor/nodes/alignmentGuides.test.ts  (new)
backend/app/schemas/script.py                  (modify: ScriptChapterUpdate +position_x/y)
backend/tests/.../test_script_canvas*.py       (modify: PUT chapter position round-trip)
frontend/public/locales/{en,zh}.json           (modify)
```

**PR 切分：单 PR（PR-N1）**——前端为主+两行 schema 后端，无迁移无 workflow，切开反而制造 stacked 等待。

---

### Task 1: 选择系统——框选、多选、群移批量持久

**Files:** `NodesView.tsx`、`nodesView.test.tsx`

**行为契约（决策记录）：**
- 空白左拖 = 平移（保持现状）；**Shift+空白拖 = 框选**（xyflow 惯例 `selectionKeyCode` 默认 Shift，不学 Infinite-Canvas 的 R/Ctrl）；`selectionMode={SelectionMode.Partial}`（相交即选，手感宽松——调研✓）。
- Ctrl/Cmd+点击 toggle 加选（`multiSelectionKeyCode`）。
- 群移：多选后拖任意一个，整组移动（xyflow 内置）；**onNodeDragStop 时把 `nodes.filter(n=>n.selected)` 里所有 sceneNode 逐个走既有 per-scene debounce 持久**（复用现 500ms debounce map，键是 sceneId 所以天然并行不互踩）。
- `onSelectionDragStop` 同样触发批量持久（xyflow 群拖走这个回调而非 onNodeDragStop——两个都接，内部统一 `persistPositions(nodes: Node[])`）。

- [ ] **Step 1: 失败测试**：`nodesView.test.tsx` 加 3 个用例——(a) ReactFlow 收到 `selectionMode='partial'`+`selectionKeyCode='Shift'`+`multiSelectionKeyCode`（平台探测 Meta/Control）props；(b) 模拟 `onSelectionDragStop` 带 2 个 selected sceneNode → `updateSceneMeta` 对两个 id 各调一次（fake timers 推 debounce）；(c) chapterNode 混在选中集里时不为它调 updateSceneMeta（Task 3 前保持只持久 scene）。ReactFlow 在测试里已有 mock 模式（现测试文件的既有 mock 范式，从中扩展）。
- [ ] **Step 2: 跑测试确认失败**：`npx vitest run editor/nodes/ --no-file-parallelism`
- [ ] **Step 3: 实现**：NodesView 加 props + 抽 `persistPositions(changed: Node[])`（内部即现 onNodeDragStop 逻辑泛化：`for (const node of changed.filter(n => n.type === 'sceneNode'))` 走 debounce map），`onNodeDragStop` 改为：单节点时 `persistPositions([node])`，节点在选中集时 `persistPositions(selectedNodes)`；接 `onSelectionDragStop={(_, nodes) => persistPositions(nodes)}`。
- [ ] **Step 4: 全绿**：同命令。
- [ ] **Step 5: Commit** `feat(editor): canvas rubber-band selection + group-drag persistence`

### Task 2: 导航与手感——MiniMap、snapGrid、对齐参考线、性能、键盘

**Files:** `NodesView.tsx`、`alignmentGuides.ts`、`GuideOverlay.tsx`、`useCanvasShortcuts.ts`、`alignmentGuides.test.ts`、`nodesView.test.tsx`、locales

**行为契约：**
- `<MiniMap pannable zoomable>` 右下角，节点色随主题（chapter=accent 靛紫/scene=muted），`maskColor` 双主题 CSS 变量。Controls 保留。
- `snapGrid={[8,8]}` + `snapToGrid`（8px 温和吸附，不做开关——YAGNI）。
- **对齐参考线（唯一自实现）**：`computeAlignmentGuides(dragged: Rect, others: Rect[], tolerance=5)` 纯函数——对 others 的 left/centerX/right 与 top/centerY/bottom 六轴在容差内匹配，返回 `{ vertical?: number, horizontal?: number, snappedX?: number, snappedY? : number }`（最近命中轴）；`onNodeDrag` 时算并 set state，`GuideOverlay` 用两条绝对定位线渲染（flow 坐标→screen 用 `useReactFlow().flowToScreenPosition`）；`onNodeDragStop` 清空。**guide 命中时把 node position snap 到 snappedX/Y**（一次性，优先于 snapGrid）。
- `onlyRenderVisibleElements` 开启。
- 键盘（`useCanvasShortcuts`，挂在画布容器 keydown，**输入框聚焦时全部旁路**——`(e.target as HTMLElement).closest('input,textarea,[contenteditable]')` 早退）：方向键=选中 scene 微移 1px（Shift+方向=10px）并走 persistPositions；`+`/`-`=zoomIn/zoomOut；`f`=fitView；Ctrl/Cmd+A=全选（preventDefault）；Esc=清空选中。无 Delete（画布不做删除——场景删除走编辑器，防误删）。
- i18n：MiniMap/快捷键无文案；若加 aria-label 用 `t('editor.nodes.minimap')` en/zh 双补。

- [ ] **Step 1: 纯函数失败测试**（`alignmentGuides.test.ts` 穷举）：六轴各一命中、容差边界 5/6px、多候选取最近、无命中返回空、snappedX/Y 数值正确（dragged 对齐后坐标）。
- [ ] **Step 2: 确认失败** → **Step 3: 实现 `alignmentGuides.ts`** → **Step 4: 绿**。
- [ ] **Step 5: 组件层失败测试**：ReactFlow 收到 `snapGrid/[8,8]`、`snapToGrid`、`onlyRenderVisibleElements` props；MiniMap 渲染存在；键盘 hook 单测（jsdom dispatchEvent：ArrowRight 触发 persist 且 input 聚焦时不触发、Ctrl+A preventDefault、Esc 清选中）。
- [ ] **Step 6: 实现接线** → **Step 7: 全绿 + `npm run build`**。
- [ ] **Step 8: Commit** `feat(editor): canvas minimap, snap grid, alignment guides, shortcuts`

### Task 3: chapter 拖拽持久化（补掉已知缺口）

**Files:** `backend/app/schemas/script.py`、后端 wiring 测试、`sceneService.ts`、`NodesView.tsx`、`nodesView.test.tsx`

**Interfaces:**
- Produces（后端）：`PUT /api/v1/scripts/chapters/{chapter_id}` body 接受 `{position_x?: number, position_y?: number}`（`ScriptChapterUpdate` 加 `position_x: Optional[float] = None` / `position_y: Optional[float] = None`——`update_chapter` 是 `model_dump(exclude_none=True)` 透传，repo/service 零改动；列已存在于 `script_chapters`）。
- Produces（前端）：`sceneService.ts` 导出 `updateChapterPosition(chapterId: string, pos: {position_x: number; position_y: number}): Promise<void>`——PUT 上述端点，走既有 `getAuthHeaders()` 范式。

- [ ] **Step 1: 后端失败测试**：现 script_canvas 测试文件加用例——PUT chapter body `{"position_x": 120.5, "position_y": -40}` → 200 且透传进 `svc.update_chapter` 的 dict 含两键；负例：非数字 422。
- [ ] **Step 2: 确认失败** → **Step 3: schema 加两字段** → **Step 4: `uv run pytest -q -k "canvas or chapter"` 绿 + lint（black/isort/flake8 对改动文件）**。
- [ ] **Step 5: 前端失败测试**：NodesView 拖 chapterNode → debounce 后 `updateChapterPosition` 被调（String(chapterId)）；scene 拖动仍走 updateSceneMeta 不串线。
- [ ] **Step 6: 实现**：`persistPositions` 移除 `type!=='sceneNode'` 早退，分流 sceneNode→updateSceneMeta / chapterNode→updateChapterPosition（chapter 的 debounce map 用 `ch-` 前缀键隔离）；node id `ch-<id>` 剥前缀取 chapterId。
- [ ] **Step 7: 全绿** → **Step 8: Commit** `feat(editor): chapter node drag persistence`

### Task 4: scene 卡片分镜缩略图（P3 tie-in）

**Files:** `SceneFlowNode.tsx`、`sceneNodeMapper.ts`、`nodesView.test.tsx`、locales

**行为契约：** scene 的 shots 中存在 `image_url` 非空的 shot 时，卡片头部渲染一条 72px 高缩略图（第一张有图的 shot，`object-cover`，双主题边框）；无图时布局与现状完全一致（零回归）。图 URL 是同源 durable `/api/v1/generated-media/{id}/cover`（P3 L5 契约，`<img src>` 免 token）。数据来源：NodesView 已拉 scenes——shots 不在 scene row 上，**mapper 入参扩一个可选 `shotCoverByScene?: Map<string,string>`**，NodesView 挂载时 `listShots(scriptId)`（sceneService 已有 P3 的 shots API；若无 script 级列表则逐 scene 并发拉——以实际 service 为准，实现者先读 `editor/sceneService.ts` 现有 shots 函数签名）一次性构建 map；失败静默降级无图（console.error 不 toast）。

- [ ] **Step 1: 失败测试**：mapper 传 shotCoverByScene 时 sceneNode data 带 `coverUrl`；SceneFlowNode 有 coverUrl 渲染 img[src=coverUrl]、无则不渲染 img；listShots 拒绝时画布仍渲染。
- [ ] **Step 2: 确认失败** → **Step 3: 实现** → **Step 4: 全绿 + build + i18n 键集守卫（若加 alt 文案）**。
- [ ] **Step 5: Commit** `feat(editor): scene node shot cover thumbnails`

### Task 5: 收口

- [ ] opus 全分支终审（重点：clean-room 边界零 Infinite-Canvas 代码、群移持久的 debounce 竞态、guide snap 与 snapGrid 叠加次序、chapter/scene 分流 #1006 String 收敛、缩略图零回归分支）。
- [ ] ship：merge origin/master → `(cd backend && uv run pytest -q -k "canvas or chapter or scene")` + `npx vitest run editor/ components/i18n-rendering.test.tsx --no-file-parallelism` + build → 读 origin/master 版本 bump → push → PR → CI 监控（忽略 Vercel checks）→ squash merge → destroy worktree。
- [ ] 真机过场进 task #49 队列（与 P3 分镜/P4 版本面板视觉过场同批，等 Vercel 窗口）。
- [ ] memory 台账更新。

## Self-Review 已做

- 侦察双报告全覆盖：必做 1-6 → Task 1/2（undo 除外，defer 决策记录）；值得做 7-11 → 全 defer（连线/复制/lightbox 均触及投影只读边界或 Phase 5）；对齐参考线另参照 xyflow MIT 范式 → Task 2。
- 缺口清单 9 项：1→T1、2→T2、3→T2、4→T2、5 defer（决策记录）、6→T4、7→T3、8 defer、9→T2。
- 类型一致性：`persistPositions(Node[])` T1 定义 T3 扩展；`updateChapterPosition` 契约 T3 内自洽；`computeAlignmentGuides` 签名 T2 内自洽。
- 无占位符；后端改动最小面（schema 两行+测试）。
