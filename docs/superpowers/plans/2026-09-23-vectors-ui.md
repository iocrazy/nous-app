# 资源库向量检索 UI（PR 6 前置版）— 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 按画板把三个屏落到现有骨架上：① My Downloads 的 AI 搜索命中（Layer / Sort / legs 三个 chip、卡片命中徽标与相似度条）；② info island 大 tab 收成 Overview / AI / Shots（Shots 先做未索引占位）；③ Settings → AI 加 Vectors 子 tab（当前空间卡 + Retrieval layers 表 + 语义层回填按钮），MCP 挪到左栏独立导航。

**Architecture:** 纯前端 PR，基于 master。后端契约来自 PR 2（`feat/vector-spaces-halfvec`）：hybrid 响应新增 `vector_leg` / `legs` / `reranked` 与每条结果的 `layer`；`GET /api/v1/search/vectors/status`；`POST /api/v1/ai/analyze/backfill-embeddings`（已有）。**所有新字段都是可选的**：后端还没上 PR 2 时 UI 必须优雅退化（没有 `legs` 就不画 legs chip、没有 `layer` 一律当 `text`）。镜头相关（Shots 正文、Visual / Camera 两层、Indexing policy、rerank）由 PR 3/4 提供，本 PR 只画**占位**并明确标注 "Arrives with PR 3"，与资产库 P2 的 `Send To Canvas` 禁用占位同一手法。

**Tech Stack:** React + TS + Tailwind（语义色 token `ok/warn/danger/info`），react-i18next（单 namespace，`t('key','Default')`），vitest + jsdom。

**Spec:** `docs/superpowers/specs/2026-09-16-video-vector-layers-design.md` §4.6；画板 `docs/superpowers/specs/2026-09-16-video-vector-layers-mockup.html`（四屏文字版见本文附录 B）。

## Global Constraints

- UI 文案英文、Title Case；新文案一律走 i18n key（`t('key','Default')`），`public/locales/en.json` 与 `zh.json` **同批**加键。
- 状态色只用 `ok / warn / danger / info` token（`index.css` `@theme inline`，`text-ok` / `bg-warn-soft` / `border-danger-line`），不引入 indigo/amber/red 等旧色相类。
- 不新开页面、不加新的一行工具栏：增量塞进现有 chip 行与卡片。
- 清除搜索后所有搜索增量必须消失（Layer/Sort/legs chip、徽标、相似度条、Search hit 卡）。
- 引擎离线（`vector_leg` 不是 `ok`）：legs chip 向量点变红/砖红，徽标与相似度条不画，文本命中照常，**不弹 toast**。
- 测试镜像现有写法：DownloadsView 用 `vi.mock` 桩（见附录 A §7），CompactMediaCard 直接 render，VideoDetailPanel mock TaskManagerContext/aiService/MediaCard。
- CI 四件：`npm run lint`、`npm run typecheck`、`npm run build`、`npx vitest run`。改完的每个组件至少加一个新测试文件或用例。
- `catch` 不许静默：`catch (err) { console.error(...) }`。
- 边界 mock 用真实 JSON 形状：`similarity_score` 是 number、`media_id` 是 number、`platform_id` 是 string。

---

## 文件结构

| 文件 | 责任 |
|---|---|
| `frontend/services/searchService.ts` | 类型加 `vector_leg` / `legs` / `reranked` / `layer`；新增 `getVectorsStatus()` |
| `frontend/services/aiService.ts`（或已有的回填函数所在文件） | `backfillEmbeddings({limit, dry_run})` |
| `frontend/components/resources/filter/FilterBar.tsx` | `trailing?: ReactNode` 插槽 |
| `frontend/components/DownloadsView/SearchLegsChips.tsx`（新） | Layer chip、Sort chip、legs chip、hits 行 |
| `frontend/components/DownloadsView.tsx` | 保留命中 map、搜索元信息 state、渲染 chips、传 `hit` 给卡片 |
| `frontend/components/CompactMediaCard.tsx` | `hit` prop → 徽标 + 3px 条 |
| `frontend/components/VideoDetailPanel.tsx` | 大 tab Overview / AI / Shots；AI 内小 tab；Shots 占位；Search hit 卡 |
| `frontend/components/VideoDetailPanel/ShotsTabPlaceholder.tsx`（新） | 未索引态占位（估算 + 禁用按钮） |
| `frontend/components/ResourceDetailPage.tsx` | inspector 同构收成 Overview / AI / Shots |
| `frontend/components/settings/VectorsPanel.tsx`（新） | Vector Spaces + Retrieval layers |
| `frontend/components/settings/AISettingsTabs.tsx` | 加 `vectors`，删 `mcp` |
| `frontend/components/SettingsModal.tsx` / `SettingsView.tsx` | MCP 成左栏独立导航项 |
| `frontend/public/locales/{en,zh}.json` | 新键 |

---

### Task U1: 搜索命中 — service 类型、chip 行、卡片徽标

**Files:**
- Modify: `frontend/services/searchService.ts:9-36`
- Modify: `frontend/components/resources/filter/FilterBar.tsx:51`（props）与渲染尾部
- Create: `frontend/components/DownloadsView/SearchLegsChips.tsx`
- Modify: `frontend/components/DownloadsView.tsx`（:200-222 state、:352-390 filteredLibrary、:464-508 handleAISearch/clear、:1302 FilterBar、:1445/:1486 卡片、:1528 计数行）
- Modify: `frontend/components/CompactMediaCard.tsx`（:22-40 props、:337-355 右上角列、:380 之后 3px 条）
- Test: `frontend/components/DownloadsView/SearchLegsChips.test.tsx`、`frontend/components/DownloadsView.vectorHits.test.tsx`、`frontend/components/CompactMediaCard.hit.test.tsx`
- Modify: `frontend/public/locales/en.json` / `zh.json`（`library.vectorSearch.*`）

**Interfaces:**
- Produces：
  ```ts
  // searchService.ts
  export type HitLayer = 'text' | 'semantic' | 'visual' | 'camera' | 'transcript';
  export type VectorLegOutcome = 'ok' | 'unconfigured' | 'embed_failed' | 'dimension_mismatch'
    | 'timeout' | 'unavailable' | 'error' | 'store_missing' | 'skipped_filters'
    | 'skipped_full_page' | 'skipped_no_scope' | 'skipped_no_query';
  export interface SearchResultItem { /* 现有字段 */ layer?: HitLayer; }
  export interface SearchResponse { /* 现有字段 */ vector_leg?: VectorLegOutcome | null;
    legs?: Partial<Record<HitLayer, number>> | null; reranked?: boolean; }
  export interface SearchHit { layer: HitLayer; score: number }   // 卡片/详情用
  ```
  - `CompactMediaCardProps.hit?: SearchHit`
  - `SearchLegsChips` props：`{ legs?: Partial<Record<HitLayer, number>> | null; vectorLeg?: VectorLegOutcome | null; reranked?: boolean; layer: HitLayer | 'all'; onLayerChange(l): void; sort: 'similarity' | 'date'; onSortChange(s): void; hits: number; processingMs?: number }`
  - DownloadsView 内部：`searchHitMap: Map<string /*platform_id*/, SearchHit>`、`searchMeta: { legs, vectorLeg, reranked, processingMs } | null`、`hitLayerFilter`、`hitSort`。

- [ ] **Step 1: 类型与 FilterBar 插槽（先写测试）**

`FilterBar.tsx` 加 `trailing?: React.ReactNode`，在 Clear all 按钮之后渲染 `{trailing}`。测试：`FilterBar` 已有测试文件的话追加一条「trailing 节点被渲染」；没有就在 `SearchLegsChips.test.tsx` 里覆盖组合。

- [ ] **Step 2: `SearchLegsChips`（先写测试）**

```tsx
// SearchLegsChips.test.tsx
it('renders one dot per leg with counts and colours by outcome', () => {
  render(<SearchLegsChips legs={{ text: 12, semantic: 9 }} vectorLeg="ok" reranked={false}
    layer="all" onLayerChange={() => {}} sort="similarity" onSortChange={() => {}} hits={21} processingMs={412} />);
  expect(screen.getByText('Text 12')).toBeInTheDocument();
  expect(screen.getByText('Semantic 9')).toBeInTheDocument();
  expect(screen.getByTestId('leg-dot-semantic')).toHaveClass('bg-ok');
  expect(screen.getByText(/rerank off/i)).toBeInTheDocument();
  expect(screen.getByText(/21 hits/)).toBeInTheDocument();
});
it('paints the vector dot danger and keeps text when the leg is unavailable', () => {
  render(<SearchLegsChips legs={{ text: 3 }} vectorLeg="unavailable" ... />);
  expect(screen.getByTestId('leg-dot-semantic')).toHaveClass('bg-danger');
  expect(screen.getByTestId('leg-dot-semantic').closest('[title]')).toHaveAttribute('title', expect.stringContaining('unavailable'));
});
it('layer dropdown lists all five layers and reports change', ...);
it('renders nothing when legs is undefined (backend without PR 2)', ...);
```

实现要点：五个层固定顺序 `text / semantic / visual / camera / transcript`；`legs` 里没有的层画灰点并 title "Not built"（visual/camera 再加 "Arrives with PR 3"）；`semantic` 点颜色：`vectorLeg === 'ok'` → `bg-ok`，`skipped_*` → `bg-warn`，其余 → `bg-danger`；title 就是 outcome 码原文。Layer / Sort 用现有 `FilterChip` 组件（`resources/filter/FilterChip.tsx`）保持视觉一致。

- [ ] **Step 3: DownloadsView 接线（先写测试）**

`DownloadsView.vectorHits.test.tsx` 照 `DownloadsView.searchScope.test.tsx` 的桩：hybridSearchMock 返回
```ts
{ results: [{ media_id: 1, platform_id: 'a', title: 'A', cover_url: null, author: null, similarity_score: 1.0, description: null, tags: [], view_count: 0, created_at: '2026-09-01T00:00:00Z', layer: 'text' },
            { media_id: 2, platform_id: 'b', ..., similarity_score: 0.71, layer: 'semantic' }],
  total: 2, query: 'q', search_type: 'hybrid', vector_leg: 'ok', legs: { text: 1, semantic: 1 }, reranked: false, processing_time_ms: 412 }
```
断言：搜索后 legs chip 出现；选 Layer=Semantic 只剩 b；Sort=Date 按 created_at 排；清除搜索后 chip 消失；没有 `legs` 字段时不画 chip。

实现：`handleAISearch` 成功后 `setSearchHitMap(new Map(results.map(r => [r.platform_id, { layer: r.layer ?? 'text', score: r.similarity_score }])))`、`setSearchMeta({...})`；`filteredLibrary` 里按 `hitLayerFilter` 过滤、按 `hitSort` 排序（默认 similarity）；`handleSearchClear` / `handleSearchQueryChange` 清空这些 state 并把 `hitLayerFilter` 归 `all`、`hitSort` 归 `similarity`；`restored` 恢复逻辑不必持久化这几个（刷新即清）。卡片处传 `hit={searchHitMap.get(video.platform_id)}`（仅 `isSearchActive` 时）。计数行文案在 `hasActiveQuery` 分支改用 `library.vectorSearch.hitsLine`："{{count}} hits · best match per video · {{ms}} ms"（`ms` 缺省时省略后半句）。

- [ ] **Step 4: CompactMediaCard（先写测试）**

```tsx
it('shows the hit badge and similarity bar when hit is given', () => {
  render(<CompactMediaCard data={baseVideo} onClick={() => {}} hit={{ layer: 'semantic', score: 0.71 }} />);
  expect(screen.getByTestId('hit-badge')).toHaveTextContent('Semantic · 0.71');
  expect(screen.getByTestId('similarity-bar')).toHaveStyle({ width: '71%' });
});
it('renders neither without hit', ...);
it('title-only hit reads "Title · 1.00" and bar full', ...);   // layer 'text' 显示 "Title"
```
徽标放右上角列（:337-355）末尾：`rounded px-1 text-[10px] bg-black/60 text-white`；3px 条放封面 div 结束之后：外层 `h-[3px] bg-line` 内层 `bg-ok` 宽度 `score*100%`。

- [ ] **Step 5: i18n 键**（`library.vectorSearch.layer`, `.sort`, `.sortSimilarity`, `.sortDate`, `.legs.*` 五个层名, `.rerankOn/Off`, `.hitsLine`, `.notBuilt`, `.arrivesWithPr3`）两份文件同批。

- [ ] **Step 6: `npm run lint && npm run typecheck && npx vitest run components/DownloadsView components/CompactMediaCard` 绿；Commit** `feat(library): AI 搜索命中 chip 行（Layer / Sort / legs）与卡片命中徽标、相似度条`

---

### Task U2: info island 大 tab 收成 Overview / AI / Shots（+ ResourceDetailPage 同构）

**Files:**
- Modify: `frontend/components/VideoDetailPanel.tsx`（:82 TabKey、:161 state、:184-194 懒加载、:471-490 tabs、:506-540 tab 栏、:547 Overview、:577-1090 正文）
- Create: `frontend/components/VideoDetailPanel/ShotsTabPlaceholder.tsx`
- Modify: `frontend/pages/DownloadDetailPage.tsx:571-576`（透传 `searchHit`，从 `useLocation().state?.searchHit` 读）
- Modify: `frontend/components/DownloadsView.tsx`（点卡片导航时把 `searchHitMap.get(platform_id)` 放进 navigate 的 state；找现有 navigate 调用处）
- Modify: `frontend/components/ResourceDetailPage.tsx`（:316 rightTab、:1602-1668 tab 栏、:1679-2335 正文三元链、:810-828 懒加载、:1779 快捷入口）
- Test: `frontend/components/VideoDetailPanel.tabs.test.tsx`、`frontend/components/VideoDetailPanel/ShotsTabPlaceholder.test.tsx`、`frontend/components/ResourceDetailPage.tabs.test.tsx`（若该页已有测试骨架就追加）

**Interfaces:**
- `VideoDetailPanelProps.searchHit?: { layer: HitLayer; score: number; startMs?: number }`（`HitLayer` 从 `services/searchService` import）
- `TabKey = 'overview' | 'ai' | 'shots' | 'lyrics'`；`AiSubTab = 'transcript' | 'summary' | 'visual'`
- `ShotsTabPlaceholder({ durationSeconds?: number })`：显示估算行 `m:ss · ≈ N shots · ≈ 2N embeddings`（N = ceil(duration/4.5)，无时长时显示 "—"）+ 按钮 `Index This Video`（`disabled`，`title="Arrives with PR 3"`）+ 一行说明 "Runs as a Task Center task · you can keep browsing"。

- [ ] **Step 1: 失败测试 `VideoDetailPanel.tabs.test.tsx`**（mock 同 `VideoDetailPanel.test.tsx`）

```tsx
it('shows Overview / AI / Shots for a video and Overview / Lyrics for audio', ...);
it('AI tab hosts Transcript / Summary / Visual sub tabs, default Transcript', () => {
  // 点 AI → 三个小 tab 按钮存在；点 Summary → Summarize 按钮出现（aiService.getSummary mock 返回 null）
});
it('Shots tab renders the placeholder with a disabled Index This Video button', ...);
it('renders the Search hit card above Overview when searchHit is given and hides it otherwise', () => {
  // searchHit={{ layer:'semantic', score:0.71 }} → 文案 "Search hit" 与 "Semantic · 0.71"
});
it('status dot on the AI tab reflects transcript_status or summary_status', ...);
```

- [ ] **Step 2: 实现**

- `tabs` 数组改三项，AI 的状态点取 `transcript_status` 与 `summary_status` 中「最忙」的那个（processing > failed > completed > 无），用现有 `getStatusIndicator`。
- AI 大 tab 内：`aiSubTab` state，默认 `transcript`；小 tab 栏 `text-xs`，正文分别渲染现有 Transcript 块（:577-837）、Summary 小节（:841-965）、Visual 小节（:966-1090）——**只搬 JSX，不改逻辑**；懒加载 effect 改成 `activeTab === 'ai'` 时按小 tab 取数据（transcript 取 transcript；summary/visual 取 summary+visualAnalysis，沿用现有两次调用）。
- 音频仍 `overview + lyrics`；:486-490 的回落逻辑保留。
- Search hit 卡：在 Overview 的 `<MediaCard/>` 前，`searchHit` 存在时渲染 `border border-info-line bg-info-soft rounded p-2`：标题 "Search Hit"，一行 `${layerLabel} · ${score.toFixed(2)}`；有 `startMs` 时加按钮 `Play From m:ss` 调 `onSeek(startMs/1000)`，与 `Open Shots` 按钮（切到 Shots tab）。本 PR 里 `startMs` 永远缺省，两个按钮仍要实现（PR 3 直接可用）。
- `ShotsTabPlaceholder` 独立小组件 + 自己的测试（估算数字、禁用态、title）。
- `DownloadDetailPage`：从 `useLocation().state?.searchHit` 读并透传；DownloadsView 打开详情的 navigate 调用带 `{ state: { searchHit } }`（找不到 navigate 就查 `onClick` 传到卡片的回调链）。

- [ ] **Step 3: ResourceDetailPage 同构**

`rightTab` 收成 `'info' | 'ai' | 'shots' | 'review' | 'lyrics'`；tab 栏：Overview / AI / Shots（视频、音频且非上传音频）/ Review（保留为第四个，条件不变）/ Lyrics（上传音频）；AI 内小 tab Transcript / Summary / Visual 复用同一个小 tab 栏样式（可抽 `components/detail/AiSubTabs.tsx` 给两处用）；:1779-1781 快捷入口改成切到 `ai` 并设对应小 tab；懒加载条件同步。Shots 用同一个 `ShotsTabPlaceholder`。测试：三大 tab 可见、Review 仍在、小 tab 切换。

- [ ] **Step 4: i18n**（`detail.tabs.overview/ai/shots/review/lyrics`、`detail.aiTabs.transcript/summary/visual`、`detail.searchHit.*`、`detail.shots.placeholder.*`）。VideoDetailPanel 此前没接 i18n，本 PR 只给**新加**的文案接 `useTranslation`，旧硬编码不动（避免 diff 爆炸）。

- [ ] **Step 5: lint / typecheck / vitest 绿；Commit** `feat(detail): info island 大 tab 收成 Overview / AI / Shots；Search hit 卡；Shots 未索引占位`

---

### Task U3: Settings → AI → Vectors 子 tab；MCP 挪到左栏

**Files:**
- Modify: `frontend/services/searchService.ts`（`getVectorsStatus()` → `GET /api/v1/search/vectors/status`）
- Modify/Create: 回填函数 `backfillEmbeddings(body: { limit: number; dry_run: boolean })` → `POST /api/v1/ai/analyze/backfill-embeddings`（先 `grep -rn backfill-embeddings frontend/services`，有就复用）
- Create: `frontend/components/settings/VectorsPanel.tsx`
- Modify: `frontend/components/settings/AISettingsTabs.tsx:17-25,66-70`
- Modify: `frontend/components/SettingsModal.tsx:15,69-84`、`frontend/components/SettingsView.tsx:35,779`
- Test: `frontend/components/settings/VectorsPanel.test.tsx`、`frontend/components/settings/AISettingsTabs.test.tsx`（新：Vectors 在、MCP 不在）、`frontend/components/SettingsModal.test.tsx`（新：左栏有 MCP 项）
- Modify: `en.json` / `zh.json`（`settings.aiTabs.vectors`、`settings.nav.mcp`、`settings.vectors.*`）

**Interfaces:**
- 后端契约（PR 2）：
  ```ts
  export interface VectorsStatus {
    status: 'ok' | 'unconfigured' | 'store_missing';
    space: { id: number; actual_model: string; protocol: string; dims: number;
             modalities: string[]; instruction_version: string } | null;
    layers: Array<{ layer: 'semantic' | 'transcript'; status: 'ok' | 'not_built'; covered: number; total: number }>;
  }
  export interface BackfillResult {
    success: boolean; dry_run: boolean; space?: unknown;
    reembedded: number[]; dispatched: Array<{ resource_id: number; task_id?: string }>;
    skipped: Array<{ resource_id: number; reason: string }>;
    in_flight: number; remaining: number; total_missing: number;
  }
  ```
  错误体是 `ErrorResponse` 外壳，类型化码在 `details.code`（`embedder_unconfigured` 409 / `vector_store_missing` 503）——`apiClient` 已解析，照 `apiClient.ts` 的既有错误类型读。

- [ ] **Step 1: 失败测试 `VectorsPanel.test.tsx`**（mock `../../services/searchService` 与回填函数）

```tsx
it('renders the current space card from /vectors/status', async () => {
  getVectorsStatusMock.mockResolvedValue({ status: 'ok',
    space: { id: 1, actual_model: 'doubao-embedding-vision-251215', protocol: 'ark-multimodal', dims: 2048,
             modalities: ['image','text','video'], instruction_version: 'en_keyword_v1' },
    layers: [{ layer: 'semantic', status: 'ok', covered: 20, total: 1409 }, { layer: 'transcript', status: 'not_built', covered: 0, total: 1409 }] });
  render(<VectorsPanel />);
  expect(await screen.findByText('doubao-embedding-vision-251215')).toBeInTheDocument();
  expect(screen.getByText('ark-multimodal')).toBeInTheDocument();
  expect(screen.getByText('2048 · halfvec · HNSW')).toBeInTheDocument();
  expect(screen.getByText('20 / 1,409')).toBeInTheDocument();
});
it('unconfigured status shows the setup hint and disables backfill buttons', ...);
it('Dry Run calls backfill with dry_run=true and lists remaining; Run 20 calls with limit 20 and lists skipped by reason', async () => {
  backfillMock.mockResolvedValueOnce({ success: true, dry_run: true, reembedded: [1,2], dispatched: [], skipped: [], in_flight: 0, remaining: 1389, total_missing: 1389 });
  ...点 Dry Run → 文案 "2 would be embedded · 1,389 remaining"
  backfillMock.mockResolvedValueOnce({ success: true, dry_run: false, reembedded: [1], dispatched: [], skipped: [{ resource_id: 2, reason: 'empty_text' }], in_flight: 0, remaining: 1388, total_missing: 1389 });
  ...点 Run 20 → "1 embedded · 1 skipped (empty_text ×1) · 1,388 remaining"，并重新拉 status
});
it('shows a typed error line on 409 embedder_unconfigured instead of a toast', ...);
it('Visual / Camera rows read Arrives with PR 3 and have no buttons', ...);
```

- [ ] **Step 2: 实现 `VectorsPanel`**

两个 section（Indexing policy 与 Candidate space / Add space 属于 PR 3/空间切换，本 PR 不画，只在 Vector Spaces 卡右上放一个禁用的 `Add Space`，title "Arrives with space switching"）：
- **Vector Spaces**：Current space 卡 —— Provider 行（`actual_model`）、Protocol、Capabilities chips（`modalities` 每个一个 chip，`text-ok bg-ok-soft border-ok-line`），Dimensions `${dims} · halfvec · HNSW`，Instruction `instruction_version`。`status==='unconfigured'` → 卡内一句 "No embedding model configured. Set one in Admin → AI Models."；`store_missing` → "Vector store not migrated yet (migration 494)."。
- **Retrieval layers** 表：四行 Semantic / Visual · frame / Camera · clip / Transcript。列 Layer / Status / Coverage / Source / Backfill。Semantic 行：Status 点（ok 绿、not_built 灰），Coverage `covered / total` + 细进度条，Source "title + description + tags + summary + transcript"，Backfill 两个按钮 `Dry Run` / `Run 20`（`status!=='ok'` 时禁用）。Visual / Camera：Status 灰 "Arrives with PR 3"，Coverage `— / total`。Transcript：Status "not built · Phase 2"。
- 表下方 "Last backfill" 一行：本次会话最近一次结果（dry run 或真跑），skipped 按 reason 聚合 `reason ×n`；错误按 `details.code` 显示一行 `text-danger`。
- 数据：`useEffect` 拉 status；回填后重拉。加载态 / 失败态分开（"Loading…" / "Could not load vector status"）。

- [ ] **Step 3: AISettingsTabs + MCP 挪位（先写测试）**

- `AISettingsTabs`：`SUB_TABS` 去掉 `mcp`，末尾加 `{ id: 'vectors', labelKey: 'settings.aiTabs.vectors' }`；分支渲染 `<VectorsPanel/>`；删 `MCPServersPanel` import。
- `SettingsModal`：`SettingsTab` 联合加 `'mcp'`；`APP_SETTINGS_SECTION` 在 `ai` 之后插 `{ id: 'mcp', label: 'settings.nav.mcp', icon: Plug }`（lucide `Plug` 或已有 MCP 图标）；`TAB_LABELS` 加键。
- `SettingsView`：`activeTab` 联合加 `'mcp'`，分支渲染 `<MCPServersPanel/>`（import 路径按 AISettingsTabs 原来的）。
- `AISettings.tsx:1868` 那行过时注释改成 "MCP lives in its own settings tab (left nav) since 2026-09"。
- 测试：AISettingsTabs 渲染后 `ai-subtab-vectors` 存在、`ai-subtab-mcp` 不存在；SettingsModal 左栏有 MCP 项且点击后渲染 MCPServersPanel（mock 之）。

- [ ] **Step 4: i18n 两份同批；lint / typecheck / build / vitest 绿；Commit** `feat(settings): AI → Vectors 子 tab（当前空间 + Retrieval layers + 语义层回填）；MCP 成左栏独立项`

---

## 自审

- spec §4.6 屏 1：Layer / Sort / legs chip ✔、徽标 + 相似度条 ✔、Search hit 卡 ✔、shots queued（PR 3 才有数据，不画）、引擎离线行为 ✔。
- 屏 2：三大 tab ✔、AI 小 tab ✔、Shots 未索引态 ✔（有索引态 PR 3）、ResourceDetailPage 同构 ✔（播放器镜头标记 PR 3）。
- 屏 3：Vectors 子 tab ✔、MCP 挪左栏 ✔、Current space 卡 ✔、Retrieval layers + Dry run / Run 20 ✔、Candidate space / Indexing policy（占位/不画，依赖空间切换与 PR 3）。
- 类型一致：`HitLayer` / `SearchHit` 定义在 U1 的 `searchService.ts`，U2 / U3 从那里 import；U2 与 U3 不改 `searchService.ts` 的同一段（U3 只追加 `getVectorsStatus` 与 `VectorsStatus` 类型到文件末尾）。

---

## 附录 A：落点地图（2026-09-23 只读侦察，路径相对 `frontend/`）

1. **DownloadsView.tsx**：state `searchResults`:200 `searchVideoMap`:207 `isSearchActive`:210 `searchQueryText`:211 `isAISearching`:212 `searchScope`:215 `searchError`:219 `chipsIgnoredByMode`:222；`ToolbarSearch`:1234 的 `onAISearch` → `handleAISearch`:464，hybrid 调 `hybridSearch(query, {}, 100, 0.5, searchScope, chips)`:483，出错回落 `localSearch`:500；`filteredLibrary`:352-390 按 `similarity_score` 降序:367（**similarity 映射成 Video 时丢掉**）；清除 `handleSearchClear`:508、`handleSearchQueryChange`:456；桌面 `<FilterBar config={filterBarConfig}/>`:1302，移动 `<FilterChipBar>`:1312；`FilterBar` 无尾部插槽（props :51）；无排序 state；计数行 :1528-1537（`library.searchResultsCount`）；卡片调用 :1445 / :1486。
2. **CompactMediaCard.tsx**：props :22-40；封面容器 :245；右上角列 :337-355（`absolute top-2 right-2 flex flex-col items-end gap-1`）；封面 div 结束 :380，Info 区 :383；图标行 :400-445。`MediaCard.tsx` 是详情面板的 Overview 卡（VideoDetailPanel :548）。
3. **VideoDetailPanel.tsx**：`TabKey`:82，`activeTab`:161，懒加载 :184-194，`tabs`:471-476（硬编码英文、无 i18n），`isAudio`:469，回落 :486-490，tab 栏 :506-540，状态点 `getStatusIndicator`:492；Overview `<MediaCard/>`:547-566；Lyrics `SodaLyricsTab`:570；Transcript 块 :577-837（`onSeek` 按钮 :796）；Analysis 块 :839-1090（Summary :841，Summarize :866/:888 → `handleSummarize`:396；Visual :966，Trigger :1049/:1075 → `handleVisualAnalysis`:419）；`onSeek` prop :62，宿主 `pages/DownloadDetailPage.tsx:576`。**ResourceDetailPage.tsx**：`rightTab`:316（info/review/transcript/analysis/lyrics），tab 栏 :1602-1668，正文 :1679/:1997/:2013/:2170/:2335，懒加载 :810-828，快捷入口 :1779-1781。
4. **SettingsModal.tsx**：`SettingsTab`:15，NavItem `{id,label,icon}`:43，`APP_SETTINGS_SECTION`:69-82（ai :78），`TAB_LABELS`:84，app 类 tab 交给 `SettingsView`:330。**SettingsView.tsx**：`activeTab` 联合 :35，ai 分支 :779-780 渲染 `AISettingsTabs`。**settings/AISettingsTabs.tsx**：`AISubTab`:17，`SUB_TABS`:19-25，testid `ai-subtab-${id}`，mcp 分支 :66-70 渲染 `<MCPServersPanel/>`（import :13）。
5. **services/searchService.ts**：`SearchResultItem`:9-20，`SearchResponse`:25-36（无 vector_leg/layer/legs），`hybridSearch`:70，`semanticSearch`:56，`textSearch`:147。
6. **i18n**：单 namespace，`public/locales/en.json` / `zh.json`；DownloadsView 用 `library.*` / `resources.*`，FilterBar 用 `resources.filter.*`，AISettingsTabs 用 `settings.aiTabs.*`；写法 `t('key','Default')`。色 token `index.css:254-268`。
7. **测试**：`DownloadsView.searchScope.test.tsx` 裸 render + `vi.mock`（./ToolbarSearch、../services/searchService、./SearchScopePicker、./Toast、./DownloadsView/DownloadContextMenu、./DownloadsView/useDownloadsData、react-i18next、react-router-dom useNavigate、../contexts/LibraryContext、../contexts/TeamContext）；`CompactMediaCard.test.tsx` mock ../contexts/AuthContext 与 ../services/resourceService；`VideoDetailPanel.test.tsx` mock ../contexts/TaskManagerContext、../services/aiService、./MediaCard、./SodaLyricsTab；SettingsModal 无测试。vitest jsdom，setup `./tests/setup.ts`。
8. **命令**：`npm run lint`（eslint .）、`npm run typecheck`（tsc --noEmit）、`npm run test`（vitest run）、`npm run build`。

## 附录 B：画板文字版（节选）

屏 1：chip 行 `Layer · All ⌄` / `⇅ Sort · Similarity ⌄` / `Text 12 Semantic 9 Visual 14 Camera 7 Transcript · rerank on`；行尾 `57 hits · best shot per video · 412 ms`；卡片右上 `3:41 Camera · 0.71` 或 `Title · 0.40`。
屏 2：`Overview | AI | Shots`；Overview 顶部 `Search hit · Camera · Shot 54 · 3:41–3:48 · rerank 0.71 · vector 0.50 · Play from 3:41 / Open Shots`；Shots 未索引态 `7:25 · ≈ 100 shots · ≈ 200 embeddings · local provider: free · Index this video · runs as a Task Center task · you can keep browsing`；AI 内 `Transcript | Summary | Visual`。
屏 3：子 tab `General | Providers | Local CLI | Memory | Vectors`，MCP 进左栏；Current space 卡 `Provider / Protocol / Capabilities text image video rerank / Dimensions 2048 · halfvec · HNSW / Instruction en_keyword · v1`；Retrieval layers 表 `Semantic ok 1,285/1,285 title + description + summary [Dry run]` / `Visual · frame` / `Camera · clip [Run 20]` / `Transcript not built 0/1,285 Phase 2`；`Last backfill · 20 dispatched · 3 skipped (no_cover_url ×2, analysis_row_missing ×1)`。
