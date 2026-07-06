# Phase B / Phase 1 剧本编辑器前端 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 按 spec v3 §3 + UI 定稿 R2-A Final 落地文字剧本编辑器（元素化编辑 + Tab 状态机 + If-Match 乐观并发 + 双制式渲染 + copilot 结构化编辑），对接已上线的 scenes/episodes/ops API（v0.25.126）。

**Architecture:** 三层分离——(1) 纯 TS 逻辑核（`editorMachine.ts` 元素状态机 + `sceneService.ts` API 客户端 + `useSceneSync.ts` op 队列/并发），全部可无 DOM 单测；(2) 渲染层（Hollywood/Asian 两套**布局引擎**，同一 content_json 输入）；(3) 页面壳（R2-A Final：岛式 chrome + 横向模式化工具条 + 场景导航 + Writing 面板 + 召唤式 copilot）。入口藏 `VITE_FEATURE_SCRIPT_V2` flag，旧 ScriptEditorPage 不动。

**Tech Stack:** React 19 + TypeScript + Vite 7 + TailwindCSS + vitest（现有 `npm run test`）+ react-router-dom。禁 TipTap（用户裁决）；不引入新重依赖。

**Spec:** `docs/superpowers/specs/2026-07-05-phase-b-script-storyboard-design.md`（v3 §3.1-3.7、§7、§8）
**UI 定稿:** R2-A Final（2026-07-05 用户拍板）：靛紫岛式 chrome + 暖纸纹页 + **顶部横向模式化工具条**（Script=8 元素 / Outline=文本样式）+ 双主题（深色=夜稿）+ 页边距元素色标 + 键盘提示 footer。

## Global Constraints

- **UI 全英文 + i18n key**（`frontend/public/locales/en.json` + `zh.json`，key camelCase）；岛式铁律：**零 emoji / 导航不消失 / 密度不减 / 禁 zinc**。
- **flag `VITE_FEATURE_SCRIPT_V2`**，默认关；关= v2 路由 404 回旧编辑器；所有新 UI 藏 flag 后（trunk-based flag-dark）。
- **元素级 anchor-op 写 + If-Match 乐观并发**（spec §7）：禁整块 replace；客户端生成 `el_<8hex>` id；ops 端点契约=If-Match 头（缺→428）/409 `{code:"version_conflict",current_version,elements}`/422 `{code:<op_error_code>}`（PR #1052 已上线，逐字对接）。
- **异步派发端点返回扁平 `{"success":true,"task_id"}`** → 前端用 `handleResponse`；数据端点信封 `{success,data}` → `unwrapResponse`（#1019 坑，`frontend/utils/apiHelpers.ts`）。
- **fullscreen 路由必须局部包 ToastProvider + TaskManagerProvider**（`pages/ScriptEditor/index.tsx` 的既有教训注释，2026-07-05 #1012）。
- ELEMENT_TYPES 与后端一致：`action|dialogue|character|paren|transition|comment|subtitle`（scene 头不是元素，是 scene 行头字段）。
- 每 PR ≤1 天；worktree `bash scripts/worktree-manager.sh create <branch>`；lint = `npm run typecheck && npm run build`（前端无独立 lint gate，build 即闸）；commit 无 attribution 尾注。
- **无人用面必须真机验证**（六连环教训）：每 PR merge 部署后用 Playwright/Vercel preview 真看一遍（UI 早期视觉过场，feedback_ui_early_visual_ux_pass）。

## File Structure

```
frontend/
├── editor/                                    # 新目录：v2 编辑器全部代码（高内聚）
│   ├── types.ts                               # ScriptElement / SceneDoc / ElementOp / 类型守卫
│   ├── editorMachine.ts                       # 纯函数状态机：Tab/Enter 转移表 → (新状态, ops[])
│   ├── opBuilder.ts                           # 锚计算 + op 构造 + inverse 预览（客户端側）
│   ├── sceneService.ts                        # episodes/scenes/ops API 客户端（If-Match/409/422 typed）
│   ├── useSceneSync.ts                        # op 队列 hook：乐观应用/串行 flush/409 分流/save 状态
│   ├── useEditorState.ts                      # 编辑器 reducer hook（光标元素/选区/模式）
│   ├── render/
│   │   ├── HollywoodLayout.tsx                # 布局引擎 1（元素→排版行）
│   │   ├── AsianLayout.tsx                    # 布局引擎 2
│   │   └── layoutShared.ts                    # 行组件公共（色标 gutter/焦点环/data-el-id）
│   ├── components/
│   │   ├── EditorShell.tsx                    # 三区布局壳 + 主题切换 + flag 内页面入口
│   │   ├── ElementToolbar.tsx                 # 横向模式化工具条（Script/Outline 槽位切换）
│   │   ├── SceneBlock.tsx                     # scene 容器（徽章+头行三下拉+::手柄）
│   │   ├── SceneRail.tsx                      # 左侧场景导航（编号+INT/EXT+location+摘要行）
│   │   ├── WritingPanel.tsx                   # 右面板（Format 切换/Statistics/CAST）
│   │   ├── MentionCombobox.tsx                # @ 实体选择器（ARIA combobox）
│   │   ├── SaveIndicator.tsx                  # saved✓/saving…/retrying/conflict
│   │   ├── ConflictBar.tsx                    # 409 文本分歧提示条（保留我的/采用对方）
│   │   ├── EmptyStates.tsx                    # 冷启动屏 + 空 scene 内联提示
│   │   └── CopilotCard.tsx                    # 召唤式 copilot（结构化元素编辑）
│   └── __tests__/                             # vitest（逻辑核全覆盖 + 渲染快照）
├── pages/ScriptEditor/index.tsx               # (modify) flag 分流 v2/旧版
frontend/public/locales/{en,zh}.json           # (modify) editor.* keys
```

**PR 切分**：**PR-F1** = Task 1-3（逻辑核，零 UI，纯 vitest）；**PR-F2** = Task 4-6（壳+渲染+工具条，flag-dark 可视）；**PR-F3** = Task 7-9（Asian 引擎/@提及/409&保存&空态）；**PR-F4** = Task 10-11（copilot 结构化编辑 + 虚拟化/a11y/E2E 收口）。

---

### Task 1: `types.ts` + `sceneService.ts` — API 客户端与错误类型

**Files:**
- Create: `frontend/editor/types.ts`
- Create: `frontend/editor/sceneService.ts`
- Test: `frontend/editor/__tests__/sceneService.test.ts`

**Interfaces (Produces):**

```typescript
// types.ts
export type ElementType = 'action' | 'dialogue' | 'character' | 'paren' | 'transition' | 'comment' | 'subtitle';
export interface ScriptElement { id: string; type: ElementType; text: string; character_id?: string | null }
export interface SceneDoc {
  id: string; script_id: string; chapter_id: string | null;
  heading_int_ext: string | null; location_text: string | null; time_of_day: string | null;
  content_version: number; elements: ScriptElement[]; sort_order: number;
}
export type ElementOp =
  | { op: 'insert'; element_id: string; before_id?: string | null; after_id?: string | null; payload: { type: ElementType; text: string; character_id?: string | null } }
  | { op: 'update'; element_id: string; payload: Partial<{ type: ElementType; text: string; character_id: string | null }> }
  | { op: 'delete'; element_id: string }
  | { op: 'move'; element_id: string; before_id?: string | null; after_id?: string | null };

// sceneService.ts
export class VersionConflictError extends Error { constructor(public currentVersion: number, public elements: ScriptElement[]) { super('version_conflict'); } }
export class OpRejectedError extends Error { constructor(public code: string, public detail: unknown) { super(code); } }
export async function listScenes(scriptId: string): Promise<SceneDoc[]>            // GET /scripts/{id}/scenes → unwrapResponse
export async function createScene(scriptId: string, data: Partial<SceneDoc>): Promise<SceneDoc>
export async function updateSceneMeta(sceneId: string, data: Partial<Pick<SceneDoc,'heading_int_ext'|'location_text'|'time_of_day'>>): Promise<SceneDoc>
export async function deleteScene(sceneId: string): Promise<void>
export async function applyOps(sceneId: string, ops: ElementOp[], expectedVersion: number): Promise<{ content_version: number; elements: ScriptElement[] }>
  // POST /scenes/{id}/elements/ops, header 'If-Match': String(expectedVersion)
  // 409 → throw VersionConflictError(body.current_version, body.elements)
  // 422 → throw OpRejectedError(body.code, body.detail)
export async function moveScene(sceneId: string, args: { chapter_id?: string | null; before_scene_id?: string | null; after_scene_id?: string | null }): Promise<SceneDoc>
export async function listEpisodes(projectId: string): Promise<{ id: string; title: string; sort_order: number }[]>
export async function convertToScenes(scriptId: string, chapterId: string): Promise<string>  // → task_id（扁平信封，handleResponse）
export function newElementId(): string  // 'el_' + crypto.randomUUID().replace(/-/g,'').slice(0,8)
```

实现约定：走 `getAuthHeaders()`（from `../services/parserService`）+ `API_BASE = import.meta.env.VITE_API_URL + '/api/v1'`；数据端点 `unwrapResponse`，convert 派发 `handleResponse`（先读 `frontend/utils/apiHelpers.ts` 与 `frontend/services/scriptService.ts` 对齐惯例）。**id 一律 string**（Snowflake bigint 精度，绝不 parseInt）。

- [ ] **Step 1: 失败测试** — `sceneService.test.ts` 用 vitest `vi.stubGlobal('fetch', ...)` 模拟（先读 `frontend/services/scriptService.test.ts` 的既有 mock 范式并沿用）。用例（每个独立 test）：
  1. `applyOps` 发出的请求含 `If-Match: 3` 头且 body 为 `{ops:[...]}`
  2. 200 信封 → 返回 `{content_version, elements}`
  3. 409 响应体 `{success:false,code:'version_conflict',current_version:5,elements:[...]}` → throw `VersionConflictError` 且 `currentVersion===5`、`elements` 透传
  4. 422 `{code:'missing_anchor'}` → throw `OpRejectedError` code 透传
  5. `convertToScenes` 解析扁平 `{success:true,task_id:'x'}` → 返回 `'x'`（**不是** `.data.task_id`）
  6. `newElementId()` 匹配 `/^el_[0-9a-f]{8}$/` 且两次调用不同
  7. `listScenes` 对 bigint id 保持 string（mock 返回 `"id":"324520385049690"`，断言 `typeof === 'string'`）
- [ ] **Step 2: RED** — `cd frontend && npx vitest run editor/__tests__/sceneService.test.ts`，预期 module not found
- [ ] **Step 3: 实现** types.ts + sceneService.ts
- [ ] **Step 4: GREEN + `npm run typecheck`**
- [ ] **Step 5: Commit** `feat(editor): scene service client — If-Match ops, typed 409/422 errors`

---

### Task 2: `editorMachine.ts` — Tab/Enter 状态机（纯函数）

**Files:**
- Create: `frontend/editor/editorMachine.ts`、`frontend/editor/opBuilder.ts`
- Test: `frontend/editor/__tests__/editorMachine.test.ts`

**Interfaces:**
- Consumes: `types.ts` 的 `ScriptElement/ElementType/ElementOp`、`newElementId`
- Produces:

```typescript
export interface CursorState { sceneId: string; elementId: string | null; field: 'element' | 'heading_int_ext' | 'location' | 'time' }
export interface MachineResult { cursor: CursorState; ops: ElementOp[]; localElements: ScriptElement[] }
export function onEnter(elements: ScriptElement[], cursor: CursorState): MachineResult
export function onTab(elements: ScriptElement[], cursor: CursorState): MachineResult      // 无 ops：只换当前元素 type（update op）或移动焦点
export function onShiftTab(elements: ScriptElement[], cursor: CursorState): MachineResult
export function cycleType(t: ElementType): ElementType   // toolbar 点击/Tab 共用：action→character→dialogue→paren→transition→comment→subtitle→action
export function insertAfter(elements: ScriptElement[], afterId: string | null, type: ElementType): MachineResult
```

**转移表（spec §3.2 D7 逐格实现；scene 头字段不在 elements 内，机器对 heading 的转移返回 field 切换、ops 空）：**

| 当前 | Enter | Tab | Shift-Tab |
|------|-------|-----|-----------|
| heading 任一字段 | 插入新 action（首元素位）并 cursor 进入 | field 循环 int_ext→location→time→（进入首 element/新 action） | field 反向循环 |
| action | 新 action（after 当前） | 当前元素 type→character（update op） | 当前元素 type→循环表前一个 |
| character | 新 dialogue（after 当前） | type→paren | type→action |
| dialogue | 新 dialogue | type→paren | 光标回前一个 character（无 ops；若无则 type→character） |
| paren | 光标进下一 dialogue（无则新建 dialogue） | type→transition | type→dialogue |
| transition | 新 action | type→comment | type→paren |
| comment / subtitle | 新 action | type→循环下一个（subtitle→action） | 前一个 type |

（@选择器开/关的 Character 分支属 Task 8 的 combobox 层，机器只管"type 是 character"这一态。Backspace 行首=删除空元素+光标上移：`onBackspaceAtStart(elements, cursor): MachineResult`——空文本元素→delete op；非空→no-op 交给浏览器。）

- [ ] **Step 1: 失败测试** — 转移表每格一个 test（≥20 个），每个断言三件：ops 形状（含锚正确：`after_id === cursor.elementId`）、localElements 结果、cursor 落点。附加：`onEnter` 生成的 insert op 的 element_id 匹配 `/^el_/` 且 localElements 里同 id；`onBackspaceAtStart` 空元素产 delete op 且 cursor 移上一元素；所有函数**不 mutate 传入数组**（deep snapshot 对比）。
- [ ] **Step 2: RED** → **Step 3: 实现**（纯 TS，无 React import）→ **Step 4: GREEN + typecheck**
- [ ] **Step 5: Commit** `feat(editor): keyboard state machine — Tab/Enter transition table with anchored ops`

---

### Task 3: `useSceneSync.ts` — op 队列 + 409 分流 + 保存状态

**Files:**
- Create: `frontend/editor/useSceneSync.ts`
- Test: `frontend/editor/__tests__/useSceneSync.test.ts`（`@testing-library/react` renderHook —— 先确认 devDeps 已有；没有则 `npm i -D @testing-library/react`）

**Interfaces:**
- Consumes: `sceneService.applyOps/VersionConflictError`、`types.ts`
- Produces:

```typescript
export type SaveState = 'saved' | 'saving' | 'retrying' | 'conflict' | 'offline';
export interface SceneSync {
  elements: ScriptElement[];            // 乐观视图
  version: number;
  saveState: SaveState;
  conflict: { mine: ScriptElement[]; theirs: ScriptElement[] } | null;
  dispatchOps(ops: ElementOp[], optimistic: ScriptElement[]): void;   // 入队并立即应用乐观视图
  resolveConflict(choice: 'mine' | 'theirs'): void;
  flush(): Promise<void>;               // 分支切换守卫用（spec §3.6）
}
export function useSceneSync(scene: SceneDoc): SceneSync
```

**行为规格（spec §3.4 D4/D5，逐条测试）：**
1. dispatchOps → 乐观 elements 立即更新，saveState='saving'，ops 入 FIFO 队列，**串行** flush（同 scene 绝不并发两个 applyOps）。
2. 成功 → version=返回值，队列空则 saveState='saved'。
3. **409 且服务端 elements 与本地乐观结果 byte-identical**（JSON.stringify 对比，元素顺序+文本）→ 静默采纳 current_version 重放队列剩余 ops，用户无感。
4. **409 且文本分歧** → saveState='conflict'，暴露 `conflict{mine,theirs}`，队列冻结；`resolveConflict('mine')` = 以 theirs 为基底重算 ops（把 mine 的元素以 insert-upsert 全量重放）后继续；`'theirs'` = 丢弃本地未决、采纳服务端。
5. 网络错误（fetch reject）→ saveState='retrying'，指数退避 1s/2s/4s 重试同一批；`navigator.onLine === false` → 'offline'，`online` 事件恢复 flush。
6. 422 → console.error + 丢弃该批 op + 以服务端为准重拉（`listScenes` 单场景 GET `/scenes/{id}`）——**绝不静默吞**（CLAUDE.md catch 规范）。

- [ ] **Step 1: 失败测试**：mock `applyOps` 依次 resolve/reject 编排上述 6 条，每条独立 test（fake timers 控退避）。重点用例：连续 dispatch 三批 op 时请求串行（mock 里断言"上一个未 resolve 前不发下一个"）；409-identical 自动重放后 saveState 回 'saved'。
- [ ] **Step 2: RED** → **Step 3: 实现**（useRef 队列 + useEffect online 监听）→ **Step 4: GREEN + typecheck** → **Step 5: Commit** `feat(editor): scene sync hook — serial op queue, 409 split, save states`

**→ PR-F1 ship**：`npm run test && npm run typecheck && npm run build` 全绿；整分支终审后合并（纯逻辑，不可见，无需 flag）。

---

### Task 4: `EditorShell.tsx` + flag 路由分流 — R2-A Final 三区壳

**Files:**
- Create: `frontend/editor/components/EditorShell.tsx`、`frontend/editor/useEditorState.ts`
- Modify: `frontend/pages/ScriptEditor/index.tsx`（flag 分流）
- Modify: `frontend/public/locales/en.json`、`zh.json`（`editor.*` keys）
- Test: `frontend/editor/__tests__/EditorShell.test.tsx`

**Interfaces:**
- Consumes: Task 1 `listScenes/listEpisodes`、Task 3 `useSceneSync`
- Produces: `<EditorShell scriptId={string} />`；`useEditorState()` reducer：`{ mode: 'script'|'outline'|'cover'; format: 'hollywood'|'asian'; theme: 'light'|'dark'; cursor: CursorState|null; activeSceneId: string|null }` + actions `setMode/setFormat/toggleTheme/setCursor/setActiveScene`。

**布局规格（R2-A Final mockup `ui-r2a-final.html` 为视觉基准，spec §3.1 优先级）：**
- 三区 grid：左栏 260px 可折叠（Episode selector 只读显示 Ep 1——多集 UI 是 Phase 2；SceneRail 占位）＋中央纸页列（max-width 820px 居中，纸纹背景+圆角+阴影）＋右 WritingPanel 300px 可折叠。窄屏（<1280px）右面板先折叠、<1024px 左栏折叠为图标条（**导航不消失**——保留窄条与展开按钮）。
- 顶部：Script/Outline/Cover tab（Outline/Cover Phase 1 显示但 Outline 渲染 chapter 只读列表、Cover 占位卡）；右侧 SaveIndicator 槽位 + 主题切换按钮。
- 主题：CSS variables 双套（`data-theme` 挂在 shell 根 div，不污染全局），深色=夜稿（深墨纸+微光琥珀），**禁 zinc 色板**，从 mockup 摘色值。
- flag 分流：`index.tsx` 中 `import.meta.env.VITE_FEATURE_SCRIPT_V2 === 'true' ? <EditorShell/> : <ScriptEditorPage/>`，Provider 包装保持现状（教训注释保留原文）。
- i18n：所有 chrome 文案走 `t('editor.…')`（en+zh 同 PR 落齐）；纸页内剧本内容是用户数据不翻译。

- [ ] **Step 1: 失败测试** — RTL 渲染 `<EditorShell scriptId="1"/>`（mock listScenes 返回 2 场景）：断言三区 landmark 存在（`role="navigation"` 场景栏 / `role="main"` 纸页 / `role="complementary"` 右面板）；`data-theme` 切换按钮点击后翻转；mode tab 点击切换 `aria-selected`；flag=false 时 index.tsx 渲染旧组件（用 `vi.stubEnv`）。
- [ ] **Step 2: RED** → **Step 3: 实现** → **Step 4: GREEN + typecheck + build** → **Step 5: Commit** `feat(editor): v2 shell behind VITE_FEATURE_SCRIPT_V2 — three-zone layout, dual theme`

---

### Task 5: `SceneBlock.tsx` + `HollywoodLayout.tsx` — 纸页渲染与行编辑

**Files:**
- Create: `frontend/editor/render/HollywoodLayout.tsx`、`render/layoutShared.ts`、`components/SceneBlock.tsx`
- Test: `frontend/editor/__tests__/HollywoodLayout.test.tsx`、`__tests__/SceneBlock.test.tsx`

**Interfaces:**
- Consumes: Task 2 machine、Task 3 sync、Task 4 shell/state
- Produces: `<SceneBlock scene={SceneDoc} index={number} sync={SceneSync} />`；`<HollywoodLayout elements sync cursor onCursor />`；`layoutShared.ts` 导出 `ElementLine`（每行：`data-el-id`、类型色标 gutter tick、focus ring、contentEditable span）。

**规格：**
- Scene 容器：编号徽章、头行三下拉（INT/EXT：INT|EXT|INT/EXT；time：DAY|NIGHT|DAWN|DUSK|CONTINUOUS；location 自由文本），改动走 `updateSceneMeta`（防抖 600ms）；左缘 `::` 手柄（拖拽实装在 Task 10，此处渲染+`aria-grabbed` 占位）。
- 行编辑：每元素一个 contentEditable 行（**不用 textarea**，Courier 系等宽 `font-family: 'Courier Prime','Courier New',monospace`）；`onInput` 防抖 500ms → `dispatchOps([{op:'update',element_id,payload:{text}}], optimistic)`；keydown 拦 Tab/Shift-Tab/Enter/行首 Backspace → 调 Task 2 机器 → `dispatchOps(result.ops, result.localElements)` + 焦点移动（`requestAnimationFrame` 后 focus `[data-el-id]`）。IME：`compositionstart/end` 期间不触发机器（组字中 Enter 交给浏览器）。粘贴：`onPaste` 取纯文本按 `\n` 拆成多个 action insert ops。
- Hollywood 排版（spec D8 表列 1，逐行 CSS）：scene 头全大写左对齐加粗；action 全宽；character 居中偏左（`margin-left:38%`）大写；dialogue 窄列（`margin:0 22%`）；paren 居中括号斜体；transition 右对齐大写；comment 左侧 3px 色线批注块；subtitle 居中斜体。每行左 gutter 4px 类型色 tick（色值从 mockup：action=slate、dialogue=indigo、character=violet、paren=muted、transition=amber、comment=teal、subtitle=gray——具体十六进制实现时从 `ui-r2a-final.html` 摘）。

- [ ] **Step 1: 失败测试**：
  - HollywoodLayout 快照：3 类元素输入 → 断言类名/文本/`data-el-id`（渲染快照 spec §8）
  - keydown Tab 在 action 行 → sync.dispatchOps 收到 update op type=character（mock sync）
  - Enter 在 dialogue 行 → insert op after 当前 id，且新行获得焦点（`document.activeElement`）
  - 输入文本防抖后发 update op（fake timers）
  - compositionstart 后 Enter 不产 op
  - 粘贴两行文本 → 2 个 insert action op
- [ ] **Step 2: RED** → **Step 3: 实现** → **Step 4: GREEN + typecheck** → **Step 5: Commit** `feat(editor): scene blocks + Hollywood layout engine with live element editing`

---

### Task 6: `ElementToolbar.tsx` + `SceneRail.tsx` + `WritingPanel.tsx`（Statistics）

**Files:**
- Create: `components/ElementToolbar.tsx`、`components/SceneRail.tsx`、`components/WritingPanel.tsx`
- Test: `__tests__/ElementToolbar.test.tsx`、`__tests__/WritingPanel.test.tsx`

**规格：**
- **ElementToolbar（UI 定稿核心）**：横向浮动 pill 居纸页上方；`mode==='script'` → 8 项（Scene 是"插入新 scene 块"动作，其余 7 项=把光标元素 type 切换/无光标则设定下一插入型）；`mode==='outline'` → Body/H1/H2/H3/Quote/Bold/Italic/Rule 8 项**渲染但 disabled**（Outline 编辑是 Phase 2，槽位切换本身必须可见——这是定稿的模式化槽位概念）；活动项高亮跟随光标元素 type；全键盘可达（roving tabindex）。
- **SceneRail**：编号+INT/EXT 徽章+location+首行 action 摘要（≤40 字符截断）；点击滚动到对应 SceneBlock（`scrollIntoView`）+ setActiveScene 高亮；当前视口场景自动高亮（IntersectionObserver）。
- **WritingPanel Statistics**：纯前端派生实时计数——Scenes（场景数）/Words（全元素 text 空白切分）/Characters（distinct character 元素 text）/Locations（distinct location_text）；CAST 列表（distinct character + 色点）。Format 切换（Hollywood/Asian）UI 就位，`format==='asian'` 在 Task 7 前 disabled。**页数统计不做**（依赖分页引擎，spec 明示随 E12 预算，Phase 1 砍——记入 plan 偏差）。
- 键盘提示 footer：`Tab cycles element · Enter new element · @ mention`。

- [ ] **Step 1: 失败测试**：toolbar 项数随 mode 切换（8 script 项 vs 8 outline 项 disabled）；点击 Character 项 → dispatchOps update type；rail 点击场景 2 → main 容器收到 scrollIntoView（mock）；Statistics 对 fixture（2 scene/7 元素/2 角色/2 地点）算出 `2/词数/2/2`。
- [ ] **Step 2-5**: RED → 实现 → GREEN + typecheck + build → Commit `feat(editor): mode-slot toolbar, scene rail, live statistics panel`

**→ PR-F2 ship**（flag-dark；merge 后 Vercel preview + `VITE_FEATURE_SCRIPT_V2=true` 本地起服务真机视觉过场，对照 `ui-r2a-final.html` 逐区核）。

---

### Task 7: `AsianLayout.tsx` + Format 持久化 + 渲染快照矩阵

**Files:**
- Create: `render/AsianLayout.tsx`
- Modify: `components/WritingPanel.tsx`（解 disable）、`EditorShell.tsx`（format 持久化）
- Test: `__tests__/AsianLayout.test.tsx`、`__tests__/formatMatrix.test.tsx`

**规格：** spec D8 表列 2 逐行：scene 头=`N. Location 时间 / INT-EXT` 编号行；action=`△` 前缀行；character=`名字:` 标签行；dialogue=缩进于标签下；paren=斜体括号；transition=右对齐；comment=竖线引用斜体；subtitle=居中斜体。**同一 elements 数组输入**，与 Hollywood 引擎并列文件（布局引擎非 CSS 主题）。format per-script 持久化：`PATCH /api/v1/scripts/projects/{id}`（复用 `scriptService.updateScriptProject`）写 `metadata.format`——实现前先 `curl` 确认 update 端点接受 metadata jsonb 键（若不接受则 localStorage `editor.format.<scriptId>` 降级并在 PR 描述记录）。

- [ ] **Step 1: 失败测试**：8 元素类型 × 2 引擎 = 16 快照矩阵（`formatMatrix.test.tsx` 参数化）；切 format 后重渲染断言 `△` 前缀出现；刷新（重挂组件）后 format 记忆。
- [ ] **Step 2-5**: RED → 实现 → GREEN → Commit `feat(editor): Asian layout engine + per-script format persistence`

---

### Task 8: `MentionCombobox.tsx` — @ 实体选择器 + 降级 chip

**Files:**
- Create: `components/MentionCombobox.tsx`
- Modify: `render/layoutShared.ts`（行内 chip 渲染）、`components/SceneBlock.tsx`（@ 触发）
- Test: `__tests__/MentionCombobox.test.tsx`

**规格：** 行内输入 `@` → 锚定光标弹 combobox（ARIA `role="combobox"` + `aria-expanded` + `listbox`，↑↓ 导航 Enter 选中 Esc 关闭）；候选=当前脚本 distinct character 元素名（Phase 1 实体即 CAST 派生，无独立实体表——spec §2.4）；选中 → 文本插入 `@Name` 并给该 span `data-mention="Name"` 高亮 chip 样式；**降级**：mention 的名字不在 CAST → 渲染为普通名字 chip（灰样式，不报错）。character 元素行首输入直接触发同一 combobox（laper 行为：Character 选择器）。

- [ ] **Step 1: 失败测试**：输入 @ 弹出 + aria 属性；↓↓Enter 选第二候选文本落行内；Esc 关闭无副作用；未知名字渲染灰 chip；character 行聚焦即开选择器、Tab 关闭并回 action（接 Task 2 机器的 character-selector-open 分支）。
- [ ] **Step 2-5**: RED → 实现 → GREEN → Commit `feat(editor): @ mention combobox with CAST candidates and fallback chips`

---

### Task 9: `SaveIndicator` + `ConflictBar` + `EmptyStates` 接线

**Files:**
- Create: `components/SaveIndicator.tsx`、`components/ConflictBar.tsx`、`components/EmptyStates.tsx`
- Modify: `EditorShell.tsx`（接 sync 状态）
- Test: `__tests__/saveAndConflict.test.tsx`、`__tests__/emptyStates.test.tsx`

**规格（spec §3.4 D4/D6 逐条）：**
- SaveIndicator 常驻顶栏：saved ✓（静音绿点）/ saving…（脉冲）/ retrying（琥珀）/ offline-queued（灰+队列数）；aria-live="polite" 通告变化。
- ConflictBar：saveState='conflict' 时纸页顶部非阻塞横条 `This line was changed elsewhere: Keep mine / Take theirs`（Compare 按钮 Phase 1 砍——spec 给了三选但 diff 视图依赖版本 UI，降为两选+PR 描述记录偏差）→ 调 `sync.resolveConflict`。
- EmptyStates：零 scene → 冷启动屏主按钮 `Create Story`（调 createScene + 插入空 action 元素 + 光标就位 Tab 就绪）；空 scene 块 → 内联灰字 `Tab to start an Action, or add a Character`；**Import Script 不出现**。

- [ ] **Step 1: 失败测试**：mock sync 各 saveState → 指示器文案/aria-live；conflict 态点 Keep mine → resolveConflict('mine')；零场景渲染冷启动屏、点 Create Story 后 createScene 被调且焦点在新行；空 scene 显示内联提示。
- [ ] **Step 2-5**: RED → 实现 → GREEN → Commit `feat(editor): save indicator, 409 conflict bar, cold-start empty states`

**→ PR-F3 ship**（真机过场：双开两个浏览器 tab 同 scene 互写，验证 409 分流真实表现）。

---

### Task 10: 场景拖拽重排 + chapter 回退渲染 + Convert 入口 + 虚拟化 + a11y 收口

**Files:**
- Modify: `components/SceneBlock.tsx`（`::` 手柄拖拽）、`components/SceneRail.tsx`、`EditorShell.tsx`
- Create: `components/ChapterFallback.tsx`
- Test: `__tests__/reorderAndFallback.test.tsx`

**规格：**
- 拖拽：HTML5 DnD（`draggable` on 手柄），drop indicator 线；落点 → `moveScene(sceneId,{before_scene_id/after_scene_id})`；**键盘路径**（spec §3.5）：手柄聚焦后 Alt+↑/↓ 触发同一 moveScene。
- **存量 chapter 回退**（spec §6 P1 明项）：scene 的 `chapter_id` 非空且脚本还有无 scene 的 chapters（`fetchScriptProject` 已返回 chapters）→ 纸页里这些 chapter 渲染为只读 prose 卡（`content` 纯文本列）+ 卡右上 `Convert to scenes` 按钮 → `convertToScenes(scriptId, chapterId)` → toast + TaskManager 轮询（`useTaskCompletion` 惯例，完成后重拉 listScenes）。
- 虚拟化（spec 性能预算）：场景数 >30 时启用简单窗口化——只挂载视口±5 个 SceneBlock，其余渲染定高占位（高度=元素数×行高估算缓存）；`10× scenes 性能冒烟`：100 场景 fixture 渲染 <1.5s（vitest bench 或 jsdom 计时断言宽松阈值）。
- a11y 收口（spec §3.5）：Esc 退出行编辑态 → Tab 恢复正常焦点序（shell 级 `data-editing` 开关）；全组件可见焦点环审计；409/AI 事件 aria-live 已在 Task 9。

- [ ] **Step 1: 失败测试**：拖 scene 1 到 scene 3 后 → moveScene 收到 `after_scene_id=scene3`；Alt+↓ 同效；chapter prose 卡渲染 + Convert 点击调用 convertToScenes；100 场景 fixture 只挂载 ≤11 个 SceneBlock（`screen.getAllByTestId('scene-block')`）；Esc 后根节点 `data-editing="false"`。
- [ ] **Step 2-5**: RED → 实现 → GREEN → Commit `feat(editor): drag/keyboard scene reorder, chapter fallback + convert, windowed rendering`

---

### Task 11: `CopilotCard.tsx` — 召唤式结构化元素编辑

**Files:**
- Create: `components/CopilotCard.tsx`、`frontend/editor/copilotService.ts`
- Modify: `EditorShell.tsx`（选区召唤）
- Test: `__tests__/copilot.test.tsx`

**规格（spec §3.2/§3.4 copilot 状态机，Phase 1=结构化元素编辑 only）：**
- 召唤：选中 scene 内元素（点击行号 gutter 多选/shift-click）→ 浮出卡片 `Attached · Scene N · M elements`；无选区不出现（非常驻）。
- Phase 1 后端没有专用 copilot 端点 → **前端本地实现两个快捷动作**（spec 的 Polish/Summarize 快捷条目）：
  - `Polish format`：本地规则清理（行首尾空白/连续空 action 合并/character 大写化）→ 生成 update ops → 走 `sync.dispatchOps`（actor 仍是用户 token；服务端 actor 字段由后端 auth 决定——'copilot' actor 属 Phase 2 端点）。
  - `Summarize outline`：只读——把选中元素文本拼接调用现有 `expandChapter`？**不**——Phase 1 无对应端点，此按钮渲染 disabled + tooltip `Coming with node view (Phase 2)`。
- 状态机接线：idle/attached/applying（ops 飞行=sync saving）/done（`N edits this turn` + Undo）/failed（元素不动+错误消息）。**Undo**=应用本回合 ops 的客户端逆（opBuilder 生成 inverse：insert→delete/update→update 回原值快照——本地快照回放，不依赖服务端 script_ops）。
- 自由文本输入框渲染但 disabled（`Coming in Phase 2` placeholder）——调和器明确移出 Phase 1（spec §9）。

- [ ] **Step 1: 失败测试**：无选区无卡片；选 2 元素 → 卡片标题 `Scene 1 · 2 elements`；Polish 对 fixture（首尾空格+小写 character）产出正确 update ops；done 态显示 `2 edits this turn`；Undo 后 dispatchOps 收到逆 ops（文本回原值）；失败（sync mock throw）→ failed 态元素未变。
- [ ] **Step 2-5**: RED → 实现 → GREEN → Commit `feat(editor): summoned copilot card — structured element edits with client-side undo`

**→ PR-F4 ship + 收口**：
- [ ] 全量 `npm run test && npm run typecheck && npm run build`
- [ ] merge 部署后 Playwright 真机 E2E（`scratchpad` 脚本）：signup → 建项目/脚本 → flag 开 → Create Story → 打字+Tab 切换 → 双 tab 并发触发 409 → Convert chapter → 截图存档；**UI 视觉/UX 过场**（feedback_ui_early_visual_ux_pass）对照 `ui-r2a-final.html`。
- [ ] Vercel 环境变量 `VITE_FEATURE_SCRIPT_V2` 保持未设（flag-dark 上线）；开闸=用户 go-live 决策点。
- [ ] 更新 memory（project_phase_b_p1_data_foundation 追加前端完成态 + 遗留）。

## Self-Review 已做

- **Spec 覆盖**：§3.1 布局层级→Task 4；§3.2 工具条/Tab 表/@/双制式/Statistics/copilot→Task 6/2/8/7/6/11；§3.4 409 UX/保存面/copilot 状态机/空态/Episode 只读→Task 3/9/11/9/4；§3.5 a11y→Task 8(combobox)/10(焦点逃逸+键盘重排)/9(aria-live)；§3.6 重排+flush 守卫→Task 10/3(flush)；§3.7 视觉基线→Task 4/5（mockup 为准）；§6 P1 行"存量 chapter 回退+Convert/懒加载虚拟化/flag"→Task 10/10/4。
- **显式砍项（偏差，PR 描述必须记录）**：页数统计（依赖分页引擎 E12，spec 自己标注随预算）；409 三选之 Compare（依赖 diff 视图，降两选）；Summarize/自由文本 copilot（Phase 2 端点未建，渲染 disabled 保留槽位）。Import Script/多集 UI/节点视图=spec 明示不做。
- **类型一致性**：`SceneSync.dispatchOps(ops, optimistic)` 在 Task 3 定义、5/6/11 消费同签名；`VersionConflictError.currentVersion` 在 Task 1 定义、3 消费；`MachineResult` 在 Task 2 定义、5 消费。
- **无占位符**：全任务有具体测试用例与行为规格；色值/文案指向 `ui-r2a-final.html` 与 i18n key（实现时摘取是明确指令而非 TBD）。
