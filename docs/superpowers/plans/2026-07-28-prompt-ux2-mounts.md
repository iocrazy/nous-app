# Prompt UX2 快线（挂载矩阵 + 入口重设计 + 宽度记忆）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** ① Prompt 区块入口重设计（v6.1 mockup 已过用户确认：与标签区同构的正式区块 + 预览卡）；② 挂载矩阵扩到四处（上传/下载 × 列表侧栏/详情页）；③ 侧边栏宽度记住用户最后一次拖拽值。

**纯前端**，零后端零 migration。数据线（逐张提示词/反推三格式/一键同款）在下一个 PR。

**Mockup:** https://claude.ai/code/artifact/44a26470-c032-453a-9ee2-b3668abaf90a §1（入口三态）
**分支/工作区:** `feature/download-prompt-section` @ `.worktrees/dl-prompt`（已建，基于 master）

## Global Constraints

- 只在该分支/工作区工作；不切分支、不碰主仓库目录
- UI 英文 + i18n（en/zh 双份；已有 key 复用：`resources.infoPanel.prompt/addPrompt/negativePrompt/...`）
- typecheck 基线 62 == master；vitest 全量必须全绿；lint 0 errors
- 已核实事实：
  - `PromptSection`（`frontend/components/resources/PromptSection.tsx`）现有三态在组件内部处理；props 见文件头 interface
  - 上传列表侧栏 = `frontend/components/ResourceInfoPanel.tsx`（props: resource/allTags/assignedTags/onUpdate...，480 行）
  - 下载列表侧栏 = `frontend/components/DownloadsView/DownloadInfoPanel.tsx`（props 含 `selectedVideo: Video`、`selectedResourceData?: {id,notes,rating}` ← **id 即 resourceId**、`selectedVideoTags`、`allTags`；355 行）
  - 下载详情 = `MediaCard.tsx` overview（有 `resourceId` prop、`resourceTags` state、EagleTagPicker 区块 ~L880）
  - 上传详情 = `ResourceDetailPage.tsx` 已直连 PromptSection（新样式自动生效，无需挂载改动）
  - 宽度：共享 hook `useResizablePanel(initial)`（`detail/DetailCardKit.tsx:22`，MIN/MAX clamp 已有）；DownloadsView 是独立内联 `useState(320)` + `handleResizeStart`（L412/690/704）
  - translate 服务 `translateGenPrompt(resourceId, lang)`（resourceService）；`hasPromptData`/`ensureDefaultTriggerTag`（utils/promptTriggerTags）；`fetchResourceTags/addResourceTag`（resourceService）；`updateResource`＝PATCH（找 resourceService 里现有的 update 函数名）
- 每 task 一 commit + trailer `Claude-Session: https://claude.ai/code/session_017MjbKQdeuX9c91L5xdhbns`

---

### Task 1: PromptSection 入口重设计（三态新样式）

**Files:**
- Modify: `frontend/components/resources/PromptSection.tsx`
- Modify: `frontend/components/resources/PromptSection.test.tsx`
- Modify: `frontend/public/locales/en.json` + `zh.json`（新 key `resources.infoPanel.promptSection`＝"Prompt"区块标题、`promptExpandHint`＝"Click to expand"）

**新三态（替换现有渲染，逻辑/props 接口不变）:**
1. **空 + 无触发标签／空 + 有触发标签**（两种空态合并为同一视觉）：区块头（Sparkles icon + `Prompt` 标题，样式对齐 EagleTagPicker 的 Tags 区头）+ 虚线胶囊按钮 `+ Add Prompt`（class 参考 PromptTriggerTagsCard 的 `.chip.add`：`border border-dashed border-ink-600 rounded-full px-3 py-1 text-xs text-ink-400 hover:text-[var(--accent-text)] hover:border-[var(--accent-border)] hover:bg-[var(--accent-soft)]`）。点击＝展开编辑 + onEnsureTriggerTag（现行为不变）
2. **有数据（折叠）**：区块头（+ 右侧 EN/中 mini toggle）+ **预览卡**：`border border-ink-700 rounded-[10px] bg-ink-800/40 px-3 py-2.5 hover:border-[var(--accent-border)] cursor-pointer`，内容＝正向 2 行截断（font-mono 11px）+ 负面 1 行截断（红 10.5px，仅非空时）+ 底部 meta 行（`Click to expand · Translate · Copy · Send to Canvas` 小灰字提示）。点卡片＝展开
3. **展开**：现有编辑区不变（正/负双框 + 操作行），仅把顶部 label 行换成与①②一致的区块头

- [ ] Step 1: 改测试——现有 5+ 用例断言选择器随新结构更新（预览卡取代折叠行；两种空态均出现区块头 + 胶囊按钮；点击胶囊/卡片仍触发 onEnsureTriggerTag + 展开），新增用例：负面-only 数据的预览卡显示负面行、隐藏正向行
- [ ] Step 2: FAIL → 实现 → PASS（`npx vitest run components/resources/PromptSection.test.tsx`）
- [ ] Step 3: typecheck 62 → Commit `feat(fe): Prompt 区块入口重设计 — 与标签区同构的区块头+预览卡 (v6.1)`

---

### Task 2: ResourcePromptSection 包装 + 三处挂载

**Files:**
- Create: `frontend/components/resources/ResourcePromptSection.tsx` + `.test.tsx`
- Modify: `frontend/components/DownloadsView/DownloadInfoPanel.tsx`（标签区之后）
- Modify: `frontend/components/ResourceInfoPanel.tsx`（标签区之后）
- Modify: `frontend/components/MediaCard.tsx`（EagleTagPicker 区块之后，仅 `resourceId` 存在时）

**Interfaces:**

```tsx
export function ResourcePromptSection({ resourceId }: { resourceId: string }) 
```

自包含：mount/resourceId 变化时并行取 ①resource 的 `id, file_type, gen_prompt, gen_prompt_zh, gen_prompt_negative, gen_prompt_negative_zh`（supabase 直查单行，resourceService 惯例）②`fetchResourceTags(resourceId)`（含 prompt_trigger）→ 本地 state。接线：
- `onPatch` → resourceService 的 PATCH（先读现有 update 函数签名照用）+ 本地 merge
- `onTranslate` → `translateGenPrompt(resourceId, lang)` → 本地 merge；translating state 自管
- `hasTriggerTag` ← 自取 tags；`onEnsureTriggerTag` → `ensureDefaultTriggerTag(await fetchAllTags())` + `addResourceTag` + 重取 tags
- `canGenerate=false, generating=false, onGenerate=noop`（反推入口数据线 PR 再接——PromptSection 已按 canGenerate 隐藏按钮）
- 内部渲染 `<PromptSection key={resourceId} ...>`；loading 时渲染 null（区块闪现比骨架好）

挂载：三处都是"标签区结束后"插 `<ResourcePromptSection resourceId={...} />`；DownloadInfoPanel 用 `selectedResourceData?.id`（无则不渲染）；MediaCard 用 `resourceId` prop。**先读各挂载点周边 JSX 再插**，别破坏既有布局/条件分支。

- [ ] Step 1: 测试先行（mock 服务层；断言：取数后渲染 PromptSection 且传参正确；translate 调服务并 merge；ensure 走 addResourceTag+重取）→ FAIL → 实现 → PASS
- [ ] Step 2: 三处挂载 + `npx vitest run components/` 全量无回归 + typecheck 62
- [ ] Step 3: Commit `feat(fe): Prompt 区块挂载矩阵 — 上传/下载列表侧栏 + 下载详情 (ResourcePromptSection)`

---

### Task 3: 侧边栏宽度记忆

**Files:**
- Modify: `frontend/components/detail/DetailCardKit.tsx`（useResizablePanel 加 `storageKey?: string`）
- Modify: `frontend/components/DownloadsView.tsx`（内联宽度状态接同一持久化约定）
- Modify: useResizablePanel 的调用方（grep 全部调用点，给"素材侧栏"类的传 key；其它调用方不传＝行为不变）
- Test: `frontend/components/detail/DetailCardKit.panelWidth.test.ts`（新建）

**约定:** localStorage key `nous.panelWidth.<name>`（如 `downloads-info` / `resources-info` / `resource-detail`）。读取时 clamp 到 [MIN, MAX]（脏值防御）；**mouseup 时写入**（不在 mousemove 写，避免高频 IO）；SSR/隐私模式 try/catch 静默降级为内存态。

- [ ] Step 1: 测试（jsdom localStorage：带 key 初始化读取并 clamp；无 key 行为不变；写入发生在 up 不在 move——模拟事件序列断言写入次数=1）→ FAIL → 实现 hook → PASS
- [ ] Step 2: DownloadsView：初始 `useState(() => loadPanelWidth('downloads-info', 320))`，其 onUp 处写入（找到内联 resize 的 mouseup 收口处）；ResourceInfoPanel 宿主（grep 谁持有它的宽度——ResourcesViewInner 或 wrapper）与 ResourceDetailPage 的 useResizablePanel 调用传 key
- [ ] Step 3: `npx vitest run` 全量 + typecheck + lint → Commit `feat(fe): 侧边栏宽度记忆 — localStorage 持久化最后一次拖拽值`

---

### Task 4: 全量验证 + 交付

- [ ] frontend 全量 vitest 全绿；typecheck 62；lint 0 errors
- [ ] ledger + push + PR（base master，描述带 mockup 链接与挂载矩阵表；CI 需 public 循环）

## Self-Review 记录

- 用户三条 ↔ 任务映射：下载/上传都有+列表侧栏（T2 四点位矩阵）、UI 不好看（T1 v6.1 样式）、宽度记忆（T3）
- PromptSection 接口不变 ⇒ ResourceDetailPage 既有挂载零改动自动换装
- 反推按钮在下载侧暂 canGenerate=false 是显式决策（数据线 PR 接三格式反推时统一开）
