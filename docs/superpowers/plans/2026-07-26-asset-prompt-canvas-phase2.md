# Asset Prompt Canvas Phase 2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 素材 Prompt 接入画板——PromptNode 的 Library 按钮从素材 Prompt 库加载（生成 Media 缩略图节点 + Prompt 节点对）、素材详情 Send to Canvas 反向入口、标签右键 "Show Prompt Panel" 开关。

**Architecture:** 全前端（零后端/零 migration）。PromptNodeData 增加可选 `negative_body`；Library picker 走 Supabase 直查（Phase 1 已定的口径）；跨页插入用 **router state 传 payload、CanvasComposer 挂载后消费**（不直接写另一画布的 nodes_json——绕开 base_updated_at 乐观锁冲突，走正常 store 路径让 realtime/历史栈自然工作，此处与 spec §7.2 "调 canvas API 插入" 的字面差异是有意为之）。

**Tech Stack:** React 19 + @xyflow/react（smart canvas）+ zustand store（canvasCoreStore）+ Supabase JS + vitest。

**Spec:** `docs/superpowers/specs/2026-07-26-asset-prompt-management-design.md` §7
**基础分支:** 基于 `feature/asset-prompt-management`（Phase 1，PR #1577）新建 `feature/asset-prompt-canvas`，在 worktree `.worktrees/feature-asset-prompt-mgmt` 内切换。PR 等 #1577 合并后再开。

## Global Constraints

- 工作目录固定 `/media/heygo/program/projects-code/repos/nous-app/.worktrees/feature-asset-prompt-mgmt`；只在 `feature/asset-prompt-canvas` 分支工作，绝不 checkout 其它分支、绝不碰主仓库目录
- UI 文本英文 + i18n（`frontend/public/locales/en.json` / `zh.json`）
- 画板节点/连线约定（已核实的现状，直接引用）：
  - `createPromptNode(data, {position})` / `createMediaNode(data, {position})` 来自 `features/canvas-core/smart/factories.ts`；PromptNodeData 字段是 **`body`**（不是 text）
  - `MediaNodeData.items: GeneratedImageRef[]`，`GeneratedImageRef = { url, kind, name? }`
  - 连线形状 `{ id: 'conn-<sourceId>-<targetId>', source, target }`（characterTemplate.ts:101 同款）
  - 插入 = `setNodes([...nodes, node])` + `setConnections([...connections, conn])`（CanvasComposer.addNode 模式）
  - 素材图 URL 用 `getResourceCoverUrl(String(resource.id))`（`services/resourceService.ts`，ResourceCard 同款）
- 测试命令：`cd frontend && npx vitest run <path>`；`npm run typecheck`（62 个既有错误是 master 基线，接触文件必须零错误）
- 每 task 一 commit，commit 末尾加 `Claude-Session: https://claude.ai/code/session_017MjbKQdeuX9c91L5xdhbns`

---

### Task 1: 分支 + PromptNodeData.negative_body + 节点视图负面显示

**Files:**
- Modify: `frontend/features/canvas-core/smart/types.ts`（PromptNodeData，~L53）
- Modify: `frontend/features/canvas-core/smart/factories.ts`（createPromptNode，~L117）
- Modify: `frontend/features/canvas-core/smart/nodes/PromptNodeView.tsx`
- Test: `frontend/features/canvas-core/smart/nodes/PromptNodeView.negative.test.tsx`（新建）

**Interfaces:**
- Produces: `PromptNodeData.negative_body?: string`（可选，仅非空时持久化，同 `gen` 的 spread 模式）；PromptNodeView 在 `negative_body` 非空时于主 textarea 下渲染负面小输入区（红色系弱化），编辑走 `patch({ negative_body })`；Task 3/5 依赖该字段名

- [ ] **Step 0: 建分支**

```bash
cd /media/heygo/program/projects-code/repos/nous-app/.worktrees/feature-asset-prompt-mgmt
git checkout -b feature/asset-prompt-canvas
git branch --show-current   # 必须输出 feature/asset-prompt-canvas
```

- [ ] **Step 1: 写失败测试**

先读 `PromptNodeView.theme.test.tsx` 学该目录的 mock 套路（ReactFlow Handle、store、hooks 都有现成 mock 模式），照抄其 harness。用例：

```tsx
// PromptNodeView.negative.test.tsx —— 核心断言（harness 照抄同目录既有测试）
it('renders negative textarea only when negative_body is non-empty', () => {
  // data: { body: 'pos', negative_body: 'lowres', ...必填字段 }
  // 断言: placeholder 含 "Negative" 的 textarea 存在,值为 'lowres'
  // data: { body: 'pos' } (无 negative_body)
  // 断言: 该 textarea 不存在
});
it('editing negative textarea patches negative_body', () => {
  // fireEvent.change + 断言 useNodeDataPatch 的 mock 收到 { negative_body: '...' }
});
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd frontend && npx vitest run features/canvas-core/smart/nodes/PromptNodeView.negative.test.tsx`
Expected: FAIL。

- [ ] **Step 3: 实现**

`types.ts` PromptNodeData 内（`resource_refs` 之后）：

```typescript
  /**
   * Negative prompt text loaded from an asset's prompt library entry
   * (Phase 2). Optional; absent for hand-typed prompts. The generation
   * pipeline does not consume it yet — providers that support negative
   * prompts will pick it up when the runner grows that capability.
   */
  negative_body?: string;
```

`factories.ts` createPromptNode data 内（`gen` spread 同款）：

```typescript
      ...(data.negative_body ? { negative_body: data.negative_body } : {}),
```

`PromptNodeView.tsx`：解构 `negative_body = ''`；主 textarea 之后渲染：

```tsx
        {negative_body ? (
          <div className="mt-1.5">
            <span className="text-[10px] uppercase tracking-wider text-rose-400/85">
              Negative
            </span>
            <textarea
              value={negative_body}
              onChange={(e) => patch({ negative_body: e.target.value })}
              placeholder="Negative prompt"
              rows={2}
              className="nodrag nowheel mt-0.5 w-full resize-y rounded-lg border border-rose-400/25 bg-rose-500/[.06] px-2 py-1 text-[11px] text-ink-300 outline-none placeholder:text-canvas-muted focus:ring-1 focus:ring-rose-400/40"
            />
          </div>
        ) : null}
```

（样式细节以文件内既有 textarea 的 className 风格为准微调；`patch` 即现有 `useNodeDataPatch(id)`。）

- [ ] **Step 4: 跑测试确认通过**

Run: `cd frontend && npx vitest run features/canvas-core/smart/nodes/ && npm run typecheck`
Expected: 新测试 PASS、既有 PromptNodeView 测试无回归、typecheck 接触文件零错误。

- [ ] **Step 5: Commit**

```bash
git add frontend/features/canvas-core/smart/types.ts frontend/features/canvas-core/smart/factories.ts frontend/features/canvas-core/smart/nodes/PromptNodeView.tsx frontend/features/canvas-core/smart/nodes/PromptNodeView.negative.test.tsx
git commit -m "feat(canvas): PromptNode 支持 negative_body — 类型/工厂/节点视图"
```

---

### Task 2: fetchPromptAssets 服务 + AssetPromptPicker 组件

**Files:**
- Modify: `frontend/services/resourceService.ts`（新增 fetchPromptAssets）
- Create: `frontend/features/canvas-core/smart/nodes/AssetPromptPicker.tsx`
- Test: `frontend/services/resourceService.promptAssets.test.ts`（新建）
- Test: `frontend/features/canvas-core/smart/nodes/AssetPromptPicker.test.tsx`（新建）

**Interfaces:**
- Produces:

```typescript
// resourceService.ts
export interface PromptAsset {
  id: string;
  filename: string;
  gen_prompt: string | null;
  gen_prompt_zh: string | null;
  gen_prompt_negative: string | null;
  gen_prompt_negative_zh: string | null;
  updated_at: string;
}
export async function fetchPromptAssets(opts: {
  query?: string;      // ilike 搜 filename 或 prompt 文本
  tagId?: string;      // 按触发标签过滤(resource_tags join)
  limit?: number;      // 默认 50
}): Promise<PromptAsset[]>

// AssetPromptPicker.tsx
export function AssetPromptPicker(props: {
  onPick: (asset: PromptAsset, lang: 'en' | 'zh') => void;
  onClose: () => void;
}): JSX.Element
```

- [ ] **Step 1: 写失败测试（service）**

```typescript
// resourceService.promptAssets.test.ts — mock supabase client 链式调用
// (mock getSupabaseClient 返回可链式的 from/select/or/ilike/limit/order stub,
//  参考 services/ 下既有 supabase mock 测试的写法; 若无先例, 用 vi.fn 链)
it('filters to rows with any prompt field via .or()', async () => {
  // 断言 .or('gen_prompt.not.is.null,gen_prompt_zh.not.is.null') 被调用
});
it('applies ilike search on filename', async () => { /* query: 'cyber' → .ilike 调用 */ });
it('tag filter goes through resource_tags join path', async () => { /* tagId → 两段查询或 inner join */ });
```

- [ ] **Step 2: 实现 fetchPromptAssets**

```typescript
/** Phase 2: assets carrying a prompt, for the canvas Library picker. */
export async function fetchPromptAssets(opts: {
  query?: string;
  tagId?: string;
  limit?: number;
} = {}): Promise<PromptAsset[]> {
  const supabase = getSupabaseClient();
  if (!supabase) return [];
  const cols =
    'id, filename, gen_prompt, gen_prompt_zh, gen_prompt_negative, gen_prompt_negative_zh, updated_at';
  let q = supabase
    .from('resources')
    .select(opts.tagId ? `${cols}, resource_tags!inner(tag_id)` : cols)
    .or('gen_prompt.not.is.null,gen_prompt_zh.not.is.null')
    .eq('is_trashed', false)
    .order('updated_at', { ascending: false })
    .limit(opts.limit ?? 50);
  if (opts.tagId) q = q.eq('resource_tags.tag_id', opts.tagId);
  if (opts.query) q = q.ilike('filename', `%${opts.query}%`);
  const { data, error } = await q;
  if (error) { console.error('[fetchPromptAssets]', error); return []; }
  return (data ?? []).map((r: Record<string, unknown>) => ({
    id: String(r.id), filename: String(r.filename ?? ''),
    gen_prompt: (r.gen_prompt as string | null) ?? null,
    gen_prompt_zh: (r.gen_prompt_zh as string | null) ?? null,
    gen_prompt_negative: (r.gen_prompt_negative as string | null) ?? null,
    gen_prompt_negative_zh: (r.gen_prompt_negative_zh as string | null) ?? null,
    updated_at: String(r.updated_at ?? ''),
  }));
}
```

（`is_trashed` 列名以 types.ts 的 Resource 为准；若实际是别名先 grep 再用。）

- [ ] **Step 3: 写失败测试（组件）+ 实现 AssetPromptPicker**

组件（mock fetchPromptAssets + fetchAllTags + react-i18next，套路同 PromptTriggerTagsCard.test.tsx）：
- 弹层卡片：搜索框（防抖 300ms 可用简单 useEffect+setTimeout）、触发标签 filter chips（fetchAllTags 里 `prompt_trigger===true` 的 + "All"）、EN/中 toggle、列表行（缩略图 `getResourceCoverUrl(id)` + prompt 首行截断 + filename + 有负面时 `−neg` 红字角标）
- 点行 → `onPick(asset, lang)`；Esc/点遮罩 → `onClose()`
- 测试：渲染列表行；搜索输入触发 fetchPromptAssets({query})；点行回调带当前 lang；标签 chip 点击带 tagId 重查

样式贴 canvas 弹层惯例：读 `CanvasMentionPicker.tsx` 的容器/滚动/暗色 class 直接沿用。

- [ ] **Step 4: 跑测试 + typecheck**

Run: `cd frontend && npx vitest run services/resourceService.promptAssets.test.ts features/canvas-core/smart/nodes/AssetPromptPicker.test.tsx && npm run typecheck`
Expected: 全 PASS、接触文件零 type 错误。

- [ ] **Step 5: Commit**

```bash
git add frontend/services/resourceService.ts frontend/services/resourceService.promptAssets.test.ts frontend/features/canvas-core/smart/nodes/AssetPromptPicker.tsx frontend/features/canvas-core/smart/nodes/AssetPromptPicker.test.tsx
git commit -m "feat(canvas): 素材 Prompt 库 picker — fetchPromptAssets + AssetPromptPicker"
```

---

### Task 3: PromptNodeView Library 按钮 + 加载成节点对

**Files:**
- Modify: `frontend/features/canvas-core/smart/nodes/PromptNodeView.tsx`
- Create: `frontend/features/canvas-core/smart/loadPromptAsset.ts`（纯函数，好测）
- Test: `frontend/features/canvas-core/smart/loadPromptAsset.test.ts`（新建）

**Interfaces:**
- Consumes: Task 1 `negative_body`、Task 2 `AssetPromptPicker`/`PromptAsset`、`createMediaNode`、`getResourceCoverUrl`
- Produces:

```typescript
// loadPromptAsset.ts — 纯函数,不碰 store
export function buildPromptAssetLoad(args: {
  asset: PromptAsset;
  lang: 'en' | 'zh';
  promptNodeId: string;
  promptNodePosition: { x: number; y: number };
}): {
  promptPatch: { body: string; negative_body?: string };
  mediaNode: MediaNode;          // items=[{url: getResourceCoverUrl(asset.id), kind:'image', name: filename}], position 在 prompt 节点左上方 (x-320, y-40)
  connection: { id: string; source: string; target: string };  // media → prompt, id `conn-<mediaId>-<promptId>`
}
```

- [ ] **Step 1: 写失败测试**

```typescript
it('builds patch from the chosen lang side, falling back to the other side', () => {
  // lang 'zh' + 只有 gen_prompt → body 取 gen_prompt(fallback), negative 同理
  // lang 'zh' + gen_prompt_zh 存在 → body 取 zh 侧
});
it('omits negative_body when the asset has no negative on either side', () => {});
it('media node carries cover url + filename and sits left of the prompt node', () => {});
it('connection wires media → prompt with conn-<src>-<tgt> id', () => {});
```

- [ ] **Step 2: 实现纯函数**（选边逻辑：`lang==='zh' ? (zh ?? en) : (en ?? zh)`，负面同构；negative 为空/undefined 则省略 key）

- [ ] **Step 3: 接入 PromptNodeView**

- 工具栏（`gen` 选择器那排）加 Library 按钮（lucide `Library` icon，样式 `CANVAS_PILL_TRIGGER` 同款 pill）→ `setState` 打开 `<AssetPromptPicker>`（渲染在节点内 relative 容器,或 portal——照 CanvasMentionPicker 的挂载方式）
- `onPick`: 调 `buildPromptAssetLoad`（promptNodePosition 从 store 里当前节点找，`useCanvasCoreStore` 的 nodes）→ `patch(promptPatch)` + `setNodes([...nodes, mediaNode])` + `setConnections([...connections, connection])` → 关闭 picker
- store 访问：`useCanvasCoreStore((s) => s.nodes/connections/setNodes/setConnections)`——先 grep store 接口确认 setter 名（`setNodes`/`setConnections` 已知存在）

- [ ] **Step 4: 跑测试 + typecheck**

Run: `cd frontend && npx vitest run features/canvas-core/smart/loadPromptAsset.test.ts features/canvas-core/smart/nodes/ && npm run typecheck`
Expected: 全 PASS。

- [ ] **Step 5: Commit**

```bash
git add frontend/features/canvas-core/smart/nodes/PromptNodeView.tsx frontend/features/canvas-core/smart/loadPromptAsset.ts frontend/features/canvas-core/smart/loadPromptAsset.test.ts
git commit -m "feat(canvas): PromptNode Library 按钮 — 从素材库加载 prompt + 缩略图节点对"
```

---

### Task 4: Send to Canvas（素材详情 → 画板）

**Files:**
- Create: `frontend/components/resources/SendToCanvasModal.tsx`
- Test: `frontend/components/resources/SendToCanvasModal.test.tsx`
- Modify: `frontend/components/resources/PromptSection.tsx`（展开态右下加 Send to Canvas 按钮）
- Modify: `frontend/features/canvas-core/smart/CanvasComposer.tsx`（消费 router state 的 pending insert）
- Modify: `frontend/public/locales/en.json` + `zh.json`

**Interfaces:**
- Consumes: `listCanvases(projectId)`（`features/canvas-core/services/canvasService.ts:41`）、项目列表服务（`services/projectService.ts` 的 fetch 函数——先读该文件确认函数名）、Task 3 的 `buildPromptAssetLoad`
- Produces:
  - `SendToCanvasModal({ resource, onClose })`：两级选择 项目 → 画布（listCanvases），确定后 `navigate('/team/'+teamId+'/canvas/'+canvasId'`（无 team 前缀场景照 ResourceDetailPage:2039 的既有拼法），router state：

```typescript
{ promptInsert: { assetId, filename, positive, negative, coverUrl } }
```

  - CanvasComposer 挂载且 load ready 后消费一次 `location.state.promptInsert`：`createPromptNode({body, negative_body}) + createMediaNode + connection` 于视口中心（dropPosition() 现成），`setSelection` 两节点，然后 `navigate(location.pathname, {replace:true})` 清 state 防刷新重插

- [ ] **Step 1: 写失败测试（modal）**：mock 项目/画布服务；渲染项目列表→点项目→listCanvases 被调→点画布→navigate 被调且 state.promptInsert 字段齐全（positive 取当前语言侧、negative 可空）
- [ ] **Step 2: 实现 modal**（样式贴 ShareModal/ConfirmDialog 暗色卡；i18n key `resources.infoPanel.sendToCanvas` = "Send to Canvas"、`sendToCanvasPick` 等；无项目时空态文案 + 禁用）
- [ ] **Step 3: PromptSection 接入**：展开态操作行加按钮（lucide `Send` 或 `LayoutDashboard`），点开 modal；仅 `hasPromptData(resource)` 时显示
- [ ] **Step 4: CanvasComposer 消费**：`useLocation()` + `useEffect`（依赖 load status ready）；写组件测试太重——把 payload→nodes 的构造复用 Task 3 纯函数，composer 内只做 wiring；在 `CanvasComposer.workflow.test.tsx` 同思路的现有测试文件里若能低成本补一条就补，否则在 report 里说明依赖手测
- [ ] **Step 5: 跑测试 + typecheck**（含 `npx vitest run components/resources/`）
- [ ] **Step 6: Commit**

```bash
git add frontend/components/resources/SendToCanvasModal.tsx frontend/components/resources/SendToCanvasModal.test.tsx frontend/components/resources/PromptSection.tsx frontend/features/canvas-core/smart/CanvasComposer.tsx frontend/public/locales/en.json frontend/public/locales/zh.json
git commit -m "feat(fe): Send to Canvas — 素材 prompt 一键发到项目画板成节点对"
```

---

### Task 5: 标签右键 "Show Prompt Panel" 开关

**Files:**
- Modify: `frontend/components/EagleTagPicker/TagContent.tsx`（context menu，~L34/86 已有 Star/Merge 项）
- Test: `frontend/components/EagleTagPicker/TagContent.promptTrigger.test.tsx`（新建；若该目录已有 TagContent 测试则追加）

**Interfaces:**
- Consumes: `updateTag(tagId, { prompt_trigger })`（unifiedTagService）、`Tag.prompt_trigger`
- Produces: 右键菜单新项 "Show Prompt Panel"（勾选态 ✓），仅 `type==='user'` 的标签显示（系统标签后端会 403）；点击翻转 `prompt_trigger` 并本地更新列表

- [ ] **Step 1: 写失败测试**：右键 user 标签 → 菜单含 "Show Prompt Panel"；点击 → updateTag 收到 `{prompt_trigger: true}`；右键 system 标签 → 无该项
- [ ] **Step 2: 实现**：菜单 Star 项之后加一项（Sparkles icon，`tag.prompt_trigger` 时显示 ✓ 与 accent 色）；需要 TagContent 能拿到完整 tag 对象（现有 contextMenu 只存 tagId——从传入的 tags 数组 find）；updateTag 成功后调用已有的刷新回调（读组件现有的标签更新回调链，沿用）
- [ ] **Step 3: 跑测试 + typecheck**：`npx vitest run components/EagleTagPicker/ && npm run typecheck`
- [ ] **Step 4: Commit**

```bash
git add frontend/components/EagleTagPicker/TagContent.tsx frontend/components/EagleTagPicker/TagContent.promptTrigger.test.tsx
git commit -m "feat(fe): 标签右键菜单 Show Prompt Panel 开关 (user 标签)"
```

---

### Task 6: 全量验证

- [ ] **Step 1**: `cd frontend && npx vitest run` 全量（3141+ 基线全绿）
- [ ] **Step 2**: `npm run typecheck 2>&1 | grep -c "error TS"` == 62；`npm run lint` 0 errors
- [ ] **Step 3**: 手测清单（记录到 report，可延后到 PR preview）：画板 Prompt 节点 Library → 选中英文各一条 → 节点对生成、缩略图可见；素材详情 Send to Canvas → 跳转画板出现节点对；标签右键开关翻转后 Settings 卡片同步
- [ ] **Step 4**: ledger 记录；PR 等 #1577 合并后基于 master rebase 再开

## Self-Review 记录

- Spec §7 覆盖：7.1 Library picker（T2+T3）、7.2 Send to Canvas（T4，插入机制改 router-state 消费，理由见 Architecture）、7.3 右键开关（T5）、negative_text→`negative_body`（T1，管线消费明确 deferred 并写入注释）
- 类型一致性：`negative_body`（T1→T3/T4）、`PromptAsset`（T2→T3/T4）、`buildPromptAssetLoad`（T3→T4）、连线 id 约定与 characterTemplate 一致
- 占位符：T4 Step 4 的 composer 测试允许"低成本则补,否则手测说明"——这是显式决策不是 TBD；两处"先读文件确认函数名"给了判定路径
