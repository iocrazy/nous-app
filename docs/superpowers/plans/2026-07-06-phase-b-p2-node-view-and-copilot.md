# Phase B / Phase 2 — 节点视图、Outline 联动、多集 UI、copilot 调和器 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 落地 spec v3 §6 Phase 2 行：节点视图（v2 scenes/chapters 投影进既有画布栈）、Outline↔Script 联动起步、多集管理 UI、copilot 自由文本→ops 调和器；顺带清 P1 前端 follow-up ②-⑥。

**Architecture:** 节点视图不重建画布——复用 `features/script/`（@xyflow/react 12 已装、ScriptCanvas/nodes/dagre layout/Expand/Branch dialogs 全在），新增 SceneNode 类型与 scenes→nodes mapper，挂在 v2 编辑器左栏模块导航的 **Scenes 槽位**（点亮它=节点投影，顶部 Script/Outline/Cover 三 tab 不变——spec §3.1 铁律导航结构不动）。copilot 调和器 = 后端**同步**端点产 ops（LLM 5-15s，前端 applying 态可接受），**不落库**，前端经既有 If-Match/409 通道 dispatch——并发协议零新面。

**Tech Stack:** 前端 React 19 + @xyflow/react ^12.10（已装,MIT）+ vitest；后端 FastAPI + `resolve_script_provider_config` + `scene_ops.apply_ops` dry-run。

**Spec:** `docs/superpowers/specs/2026-07-05-phase-b-script-storyboard-design.md` §2.2（协议/proposal）、§3.3（节点视图）、§5-2（联动）、§6 Phase 2 行
**依赖现状:** P1 全栈已上线（v0.25.132,flag 已开）。`script_scenes` 有 `position_x/y/width/height` 列（mig 339）且 `updateSceneMeta` 白名单含坐标（PR #1052）。`ScriptEditorPage`（旧节点编辑器）继续服务旧路由直至本 plan 后评估退役。

## Global Constraints

- P1 的全部 Global Constraints 继承（If-Match 契约 / handleResponse vs unwrapResponse #1019 / id 全 string / i18n en+zh / 零 emoji 禁 zinc / 每 PR ≤1 天 worktree / build+typecheck editor/ 零新错门槛）。
- **新 UI 无需新 flag**——挂在已开闸的 `VITE_FEATURE_SCRIPT_V2` 面之内；但 copilot 自由文本端点加后端 flag `FEATURE_COPILOT_OPS`（默认 false,LLM 成本面独立开关）。
- **画布 clean-room 铁律**（spec §7）：只复用本仓 `features/script/` 与 @xyflow/react；不引 Infinite-Canvas 代码。
- 后端：ORM 路径、loguru f-string、`verify_scene_access` 守卫、**LLM 产出必须服务端 `apply_ops` dry-run 校验后才返回**（防幻觉 ops 直通前端）。
- **DB 端点 done 前真库 round-trip**；新端点挂 wiring 测试（Phase A 范式）。
- `vercel env` 操作用 `printf` 不用 `echo`（换行毒值,2026-07-06 血泪）。

## File Structure

```
frontend/editor/
├── nodes/                                    # 新目录：节点投影（PR-G1）
│   ├── sceneNodeMapper.ts                    # scenes+chapters → xyflow nodes/edges（纯函数）
│   ├── SceneFlowNode.tsx                     # scene 节点卡（编号+INT/EXT·loc·time+首行摘要）
│   └── NodesView.tsx                         # 画布视图容器（ReactFlow 实例+拖拽持久化+双击跳转）
├── components/
│   ├── EditorShell.tsx                       # (modify) Scenes 槽位点亮→NodesView;Outline tab 真实化
│   ├── OutlineView.tsx                       # 新：章节+场景摘要树（跳转+场景拖拽换序）
│   ├── EpisodePanel.tsx                      # 新：rail Ep selector 真实化（CRUD+改属）
│   ├── RailModules.tsx                       # (modify) Scenes 槽位解禁
│   └── CopilotCard.tsx                       # (modify) 自由文本解禁（PR-G3）
├── copilotService.ts                         # (modify/create) requestCopilotOps API 客户端
backend/app/api/script_scenes_router.py       # (modify) +POST /scenes/{id}/copilot-ops
backend/app/services/storyboard/script/script_ai_service.py  # (modify) +instruction_to_ops prompt
backend/tests/test_copilot_ops_endpoint.py    # 新
```

**PR 切分**：**PR-G1** = Task 1-3（节点视图）；**PR-G2** = Task 4-6（Outline 联动 + 多集 UI + follow-ups）；**PR-G3** = Task 7-9（copilot 调和器 + 收口 E2E）。每 PR 走 SDD（实现→opus 终审→真机过场→ship）。

---

### Task 1: `sceneNodeMapper.ts` — 投影纯函数

**Files:**
- Create: `frontend/editor/nodes/sceneNodeMapper.ts`
- Test: `frontend/editor/__tests__/sceneNodeMapper.test.ts`

**Interfaces:**
- Consumes: `editor/types.ts` 的 `SceneDoc`；`services/scriptService.ts` 的 `ScriptChapter`（读它的真实字段名再写代码）
- Produces:

```typescript
export interface SceneNodeData { scene: SceneDoc; summary: string }   // summary=首个非空 action ≤60 字符
export function mapToFlow(scenes: SceneDoc[], chapters: ScriptChapter[]): {
  nodes: Array<{ id: string; type: 'chapterNode' | 'sceneNode'; position: {x:number;y:number}; data: unknown }>;
  edges: Array<{ id: string; source: string; target: string }>;
}
```

语义（每条一个测试）：
1. chapter 节点 id=`ch-<id>`，复用旧 store `mapChaptersToNodes` 的 data 形状（读 `stores/scriptCanvasStore.ts:15-50` 的 `ChapterNodeData` 逐字段对齐——不 import 它以免耦合旧 store，形状拷贝并注释来源）。
2. scene 节点 id=`sc-<id>`，type='sceneNode'；position 取 `position_x/y`，**双 null → 按 chapter 分组自动排布**（同 chapter 的 scenes 纵向 y=index*140,x=chapter.x+320；无 chapter 的 scenes 从 (0,0) 纵排）。
3. edge：scene.chapter_id 非空 → `ch-X → sc-Y`；chapter 间沿用旧 `parent_chapter_id` DAG（`mapChaptersToEdges` 语义,同样形状拷贝）。
4. 不 mutate 输入；id 全 string。

- [ ] Steps：失败测试（≥6 用例：双类型节点/坐标持久优先/null 坐标自动排布/边生成/孤儿 scene/免 mutate）→ RED → 实现 → GREEN + typecheck → Commit `feat(editor): scene node mapper — project scenes/chapters onto flow graph`

---

### Task 2: `SceneFlowNode.tsx` + `NodesView.tsx` — 画布视图

**Files:**
- Create: `frontend/editor/nodes/SceneFlowNode.tsx`、`frontend/editor/nodes/NodesView.tsx`
- Modify: `frontend/editor/components/EditorShell.tsx`（Scenes 槽位路由）、`components/RailModules.tsx`（解禁 Scenes）
- Test: `frontend/editor/__tests__/nodesView.test.tsx`

**Interfaces:**
- Consumes: Task 1 `mapToFlow`；`sceneService.updateSceneMeta`（坐标持久化——白名单已含 position_x/y，实现前对 `backend/app/repositories/script_scene_repository.py` 的 `_META_FIELDS` 核一遍，若缺坐标列名先补后端小 PR 再继续并报告）；`@xyflow/react` 的 `ReactFlow/Background/Controls`
- Produces: `<NodesView scenes chapters onOpenScene={(sceneId)=>void} />`

规格：
- SceneFlowNode：编号徽章 + `INT · Blank Studio · NIGHT` 头行 + 摘要行 + 元素数 pill；沿用 editor CSS 变量双主题（**不要**引 features/script 的 CSS——那是旧编辑器面）。
- NodesView：ReactFlow 受控 nodes/edges；`onNodeDragStop`（scene 节点）→ `updateSceneMeta(id,{position_x,position_y})` 防抖 500ms；`onNodeDoubleClick`（scene）→ `onOpenScene(sceneId)`；chapter 节点只读展示（动作在 Task 3）。Background dots + Controls；`fitView` 初始。
- EditorShell：`RailModules` 的 Scenes 项解禁 → `railView: 'script' | 'nodes'` 状态（**不动**顶部三 tab）；'nodes' 时中央列渲染 `<NodesView/>`（工具条/纸页隐藏）；`onOpenScene` → `setRailView('script')` + 既有 rail 滚动定位机制 + 首元素 focus。
- 测试：mapToFlow 结果渲染出两类节点（mock ReactFlow?——**不 mock**,xyflow 在 jsdom 可渲染,照仓库旧 ScriptCanvas 测试的做法先查 `features/script` 有无 canvas 测试范式,有则沿用）；拖停触发 updateSceneMeta（fake timers）；双击触发 onOpenScene 且 shell 切回 script 并滚动；Scenes 槽位点击切换视图。

- [ ] Steps：RED → 实现 → GREEN + typecheck + build → Commit `feat(editor): nodes view — scene/chapter flow projection with drag-persist and jump-to-script`

---

### Task 3: chapter 节点动作接通（Expand / Branch / Convert）

**Files:**
- Create: `frontend/editor/nodes/ChapterActionsNode.tsx`（v2 版 chapter 节点，含动作条）
- Modify: `frontend/editor/nodes/NodesView.tsx`
- Test: `frontend/editor/__tests__/chapterNodeActions.test.tsx`

**Interfaces:**
- Consumes: `services/scriptService.ts` 的 `expandChapter({script_id,chapter_id,...})`、`createBranches(...)`（读现有签名）+ `editor/sceneService.convertToScenes`；TaskManager 轮询沿用 EditorShell 的 convert poll 机制（提升为可复用 `useConvertPoll(scriptId)` hook——从 EditorShell 抽出,行为不变）
- Produces: chapter 节点动作条三按钮 Expand / Branch / **Convert to Scenes**，各自 dispatch 后节点显示 busy 态；完成后 scenes/chapters 重拉。

规格：动作用**内联确认**（点击→按钮变 Confirm?二次点击执行,3s 超时回弹）——不复用旧 `ExpandChapterDialog`（其绑定旧 store）；expand/branch 是既有扁平 task_id 端点（#1019 契约,handleResponse）；busy 期间该节点 `aria-busy` + 动作禁用。测试：三动作各自调用正确 service（mock）+ busy 态 + 完成重拉回调。

- [ ] Steps：RED → 实现 → GREEN → Commit `feat(editor): chapter node actions — expand, branch, convert wired into nodes view`

**→ PR-G1 ship**（终审 + 真机过场：节点拖拽持久化刷新不丢、双击跳转、Convert 从节点触发）。

---

### Task 4: `OutlineView.tsx` — Outline↔Script 联动起步

**Files:**
- Create: `frontend/editor/components/OutlineView.tsx`
- Modify: `frontend/editor/components/EditorShell.tsx`（Outline tab 用真组件替换占位）
- Test: `frontend/editor/__tests__/outlineView.test.tsx`

规格（spec §5-2 联动**起步**——同源只读树+跳转+换序,富文本编辑不做）：
- 树结构：chapter 标题行（无 chapter 的 scenes 归 "Unassigned" 组）→ 其下 scene 行（编号+INT/EXT·location·time+首行摘要 ≤60 字符）。
- 点击 scene 行 → 切 Script tab + 滚动定位（复用 onOpenScene 通路）。
- scene 行拖拽换序（同组内,HTML5 DnD 复用 P1 的 drop-indicator 模式）→ `moveScene`；跨 chapter 拖 → `moveScene(...,{chapter_id})` reparent。
- 顶栏 Outline 模式工具条（Body/H1…）保持 disabled（编辑不在本期）。
- 测试：树渲染分组正确（含 Unassigned）；点击行触发跳转回调；组内拖拽 moveScene 锚正确；跨组拖 reparent 参数正确。

- [ ] Steps：RED → 实现 → GREEN → Commit `feat(editor): outline view — chapter/scene tree with jump and drag reorder`

---

### Task 5: `EpisodePanel.tsx` — 多集管理 UI

**Files:**
- Create: `frontend/editor/components/EpisodePanel.tsx`
- Modify: `frontend/editor/components/EditorShell.tsx`（rail Ep selector 接真数据）、`frontend/editor/sceneService.ts`（+createEpisode/updateEpisode/deleteEpisode——后端 PR #1052 已有 4 端点,对 `backend/app/api/episodes_router.py` 逐个核路径）
- Test: `frontend/editor/__tests__/episodePanel.test.tsx`

规格：
- rail 顶部 Ep selector 显示**本 script 所属 episode**（`fetchScriptProject` 返回的 script 行含 episode_id;若无对应 episode 显示 "Ep 1" 兜底）→ 点击展开面板：本 project 全部 episodes 列表（`listEpisodes(projectId)`）+ 行内 rename（双击）+ New Episode + 删除（空 episode 才可删,删除按钮 disabled + title 说明——`episode_id` FK 是 RESTRICT）。
- 选择另一 episode → `updateScriptProject(scriptId,{episode_id})` 改属（读 scriptService 现签名,若不接受 episode_id 则加后端小改动:update 端点白名单放行 episode_id——先核 `backend/app/api/` 的 script update schema,需要就一并改+wiring 测试）。
- i18n en/zh;双主题;测试：列表渲染/rename 提交/改属调用/空判删除禁用。

- [ ] Steps：RED → 实现 → GREEN → Commit `feat(editor): episode panel — list, create, rename, reassign`

---

### Task 6: P1 follow-up 小尾巴清扫（②-⑥）

**Files:** Modify `frontend/editor/{components/EditorShell.tsx, components/SceneBlock.tsx, components/MentionCombobox.tsx, components/WritingPanel.tsx, render/layoutShared.ts}` + 对应测试

逐项（每项独立 commit 步骤,共 1 个 commit 收尾）：
1. **②data-editing 死属性**：删属性或接真消费——决定：改成 CSS 消费（`[data-editing="true"]` 时给 shell 加编辑态样式 hook,注释改写为真实机制）；同步修正 SceneBlock/EditorShell 两处夸大注释。
2. **③combobox 空 filter aria 悬空**：无匹配时 `aria-expanded=false` 且移除 `aria-controls`/`aria-activedescendant`（聚焦行侧）。
3. **④窗口化拖拽边界**：placeholder 加 dragover/drop handler（落点=该 placeholder 的 scene id 前）——一并把该限制的文档注释删掉;测试:拖到 placeholder 触发 moveScene。
4. **⑤多词角色名 chip**：`buildElementHtml` 匹配升级——对 known CAST 名做**最长前缀匹配**（`@John Smith` 整体 chip;未知名保持单词止步）;测试:双词已知名整体成 chip,未知名只 chip 首词。
5. **⑥Statistics 乐观态**：SceneBlock 已有 `onSyncStateChange`,扩一个 `onElementsChange(sceneId, elements)` 上抛（防抖 1s）,EditorShell 聚合进 Statistics 派生;测试:块内编辑后统计数变化。

- [ ] Steps：每项 RED→GREEN,最后 `git add frontend/editor && git commit -m "fix(editor): P1 follow-up sweep — a11y dangling refs, cross-window drop, multiword chips, live stats"`

**→ PR-G2 ship**（终审 + 真机过场：Outline 跳转/换序、episode 改属、多词 chip）。

---

### Task 7: 后端 `POST /scenes/{scene_id}/copilot-ops` — 调和器端点

**Files:**
- Modify: `backend/app/api/script_scenes_router.py`（+端点）、`backend/app/services/storyboard/script/script_ai_service.py`（+`instruction_to_element_ops`）、`backend/app/core/config*`（+`FEATURE_COPILOT_OPS` flag,照现有 FEATURE_* 读法）
- Test: `backend/tests/test_copilot_ops_endpoint.py`

**Interfaces:**
- Produces（逐字契约）：

```
POST /api/v1/scenes/{scene_id}/copilot-ops
守卫: verify_scene_access;flag off → 404
体: {"instruction": str(≤2000), "read_version": int}
200: {"success":true,"data":{"ops":[...], "base_version":N, "summary":"one-line what-i-did"}}
     — read_version == 当前 content_version 时
200: {"success":true,"data":{"proposal":true, "ops":[...], "base_version":N(当前), "summary":...}}
     — read_version 过期（spec §2.2:stale→proposal,以**当前**元素为基重新生成）
422: {"success":false,"code":"<op_error_code>","detail":...} — LLM 产出 dry-run 失败(重试 1 次后仍失败)
```

实现要点：
1. 读 scene（content_json+content_version）；`resolve_script_provider_config(user_id)` → `ScriptAIService`。
2. `instruction_to_element_ops(elements, instruction)`：完整提示词（把元素数组以 `id|type|text` 行给 LLM,要求返回 JSON `{"ops":[...],"summary":str}`,op 形状=§2.2 逐字,新元素 id 用 `el_new_1..n` 占位——服务端替换为真 `el_<8hex>`,锚必须引用现有 id）；`_extract_json` 防御解析。
3. **服务端 dry-run**：`apply_ops(elements, ops)`——OpError → 带错误上下文重试 LLM 一次 → 仍失败 422。成功才返回。**端点不写库**（前端经 If-Match dispatch,并发面零新增）。
4. actor 说明：写入发生在前端 dispatch（用户 token,actor=user_id）——`summary` 供前端展示;script_ops 台账的 copilot 归因推 Phase 4（版本 UI）时做,本期注释记录。
5. 同步调用,uvicorn timeout 内（LLM ~15s）;超时抛 504 由既有 handler 兜。

测试（照 `test_scene_convert_dispatch.py` 范式 mock ScriptAIService）：契约 200 形状/proposal 分支（read_version 落后）/dry-run 失败重试后 422/flag off 404/守卫 wiring/`resolve_script_provider_config` 被调 pin。

- [ ] Steps：RED → 实现 → GREEN + lint（black/isort/flake8）+ **dev 库真调一次**（flag 开,真 LLM,断言 ops 可 dry-run——记录输出）→ Commit `feat(script): copilot free-text reconciler — LLM ops with server-side dry-run (flag-dark)`

---

### Task 8: 前端 copilot 自由文本解禁

**Files:**
- Modify: `frontend/editor/components/CopilotCard.tsx`、`frontend/editor/copilotService.ts`（+`requestCopilotOps(sceneId, instruction, readVersion)`——unwrapResponse,422→OpRejectedError 复用）、`components/SceneBlock.tsx`（接线）
- Test: `frontend/editor/__tests__/copilotFreeText.test.tsx`

规格：
- 输入框解禁（保留选区召唤前提）；提交 → phase 'applying'（卡片显示 streaming 文案槽——Phase 1 无 SSE,静态 "Thinking…"）→ 收 `{ops, base_version, summary}` → **buildInverse 存逆** → `sync.dispatchOps(ops, applyLocal(current, ops))` → done 态显示 summary + `N edits` + Undo（复用 Task 11 机制）。
- `proposal:true` → 不自动应用：卡片显示 `Scene changed while thinking — review & apply`（i18n）+ Apply/Discard 两按钮;Apply 走同 dispatch。
- 422 → failed 态显示 detail 摘要,元素不动。
- 后端 flag 关（404）→ 输入框回 disabled + tooltip（探测:首次 404 后记忆本 session）。
- 测试：提交调 service 正确参（read_version=当前 version）;成功 dispatch ops+Undo 逆;proposal 不自动应用、Apply 才 dispatch;422 failed;404 降级 disabled。

- [ ] Steps：RED → 实现 → GREEN + typecheck + build → Commit `feat(editor): copilot free-text — reconciled ops with proposal flow and undo`

---

### Task 9: 终审 + ship + 收口

- [ ] PR-G3 opus 整分支终审（重点:LLM 产出信任边界——dry-run 是否可绕过;proposal 分支正确性;prompt 注入面——instruction 拼进 prompt 的转义）。
- [ ] 真机 E2E：flag 开（NAS .env `FEATURE_COPILOT_OPS=true` + stop-t0/start——照 reference_nas_backend_env）→ 生产/dev 选区召唤 → 自由文本「把这段对白改得更急促」→ ops 应用 → Undo → 台账核对；节点视图/Outline/Episode 视觉过场截图。
- [ ] 全量 pytest + vitest + build;memory 更新（Phase 2 完成态+Phase 3 storyboard 入口）。

## Self-Review 已做

- **Spec 覆盖**：§3.3 节点视图（投影/Expand/Branch/双击回编辑器）→ Task 1-3；§5-2 联动起步 → Task 4；§6 Phase 2 多集 UI → Task 5、调和器 → Task 7-8；§2.2 proposal 语义 → Task 7/8。**明确不做**（在 Phase 3+ 或本期外）：Beats、storyboard、版本 UI、协同、Outline 富文本编辑、节点画布的智能布局算法（dagre 既有的够用）。
- **决策记录**：①节点视图挂左栏 Scenes 槽位而非第四 tab（顶部三 tab 是 UI 定稿结构）；②不复用旧 dialogs/CSS（绑定旧 store,clean 边界）；③调和器同步端点不落库（复用 If-Match 通道,zero 新并发面）；④copilot 端点独立后端 flag（LLM 成本）。
- **类型一致性**：`onOpenScene(sceneId:string)` Task 2 定义 Task 4 复用;`requestCopilotOps` 返回形状与 Task 7 契约一致;`useConvertPoll` Task 3 抽取自 EditorShell 既有实现。
- **无占位符**。
