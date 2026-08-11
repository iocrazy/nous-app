# 分镜画布(Shot Nodes on Canvas)Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 按 spec `docs/superpowers/specs/2026-08-11-shot-nodes-on-canvas-design.md`:镜头作为绑定节点进现有智能画布(每集一张分镜画布),双向同步 `script_shots`;编辑器分镜视图退役;编辑器/分镜页 keep-alive;附带三修。

**Architecture:** 后端一条 migration(canvases.episode_id+kind)+ get-or-create 端点 + 生成回填分支;前端扩展既有 `shot` 节点类型(可空 `shot_id` 绑定),分镜页画布 tab 直达该集分镜画布,三个聚焦入口(镜头卡/URL/shotFocusBus)收敛到画布视口聚焦。事实底账:`docs/superpowers/specs/2026-08-11-canvas-subsystem-map.md`(每个任务开工先读它)。

**Tech Stack:** React 19 + @xyflow/react(React Flow)+ vitest/Playwright;FastAPI + SQLAlchemy async + DBOS workflow + pytest;SQL migration。

## Global Constraints

- 每个任务开工先读 `docs/superpowers/specs/2026-08-11-canvas-subsystem-map.md`(文件路径/行号锚点都在里面,行号是近似锚,按代码形状定位)。
- 所有实体 ID 在 URL/props/JSONB 中一律**字符串**(Snowflake 超 2^53,禁 `Number()`)。
- UI 文本走 i18n `t('key','English Default')`,en.json/zh.json 双补齐;语义色 token(禁 indigo/amber/emerald 字面量);lucide 图标禁 emoji。
- 前端:`cd frontend && npx vitest run <目标>` 全绿;`npx tsc --noEmit` 错误数 ≤ 基线(当前 59,开工时以实测为准并记录);e2e 实跑用 build+preview+playwright 模式。
- 后端:`cd backend && uv run pytest <目标> -v`;应用代码禁新增 `text()` 裸 SQL;Router→Service→Repository 分层。
- migration 取号:实施时 `git fetch origin master && git ls-tree --name-only origin/master supabase/migrations/ | sort | tail -3` 取 max+1(写作时最新 419,主线在动)。
- 画布人写走 REST 车道(**不进** `script_shot_ops` 账本);生成回填走生成 workflow 车道并保持 `fire_surface_sync_for_shot` 回流(B4 纪律)。
- 每任务独立提交,commit message 结尾:`Claude-Session: https://claude.ai/code/session_01KvXWh2z8qRAE3yUgUDq4sW`。

## File Structure(全景)

```
supabase/migrations/<N>_storyboard_canvas.sql   # 新:episode_id + kind + partial unique
backend/app/models/canvas.py                    # 改:两列入 ORM
backend/app/api/canvases_router.py              # 改:GET /canvases/storyboard get-or-create
backend/app/workflows/canvas_generation.py      # 改:绑定 shot 的生成回填分支(实际文件名以 grep canvas_generation_workflow 为准)
frontend/features/canvas-core/smart/types.ts    # 改:ShotNodeData.shot_id 等绑定字段
frontend/features/canvas-core/smart/nodes/ShotNodeView.tsx  # 改:绑定态渲染+编辑回写+生成
frontend/features/canvas-core/smart/shotSync.ts # 新:入驻对账纯逻辑(可单测)
frontend/components/workspace/EpisodeStoryboardPage.tsx     # 改:画布 tab 挂分镜画布+聚焦
frontend/editor/components/EditorShell.tsx      # 改:storyboard rail 退役
frontend/editor/storyboard/                     # 删:StoryboardView 及独占子组件
frontend/components/workspace/ProjectWorkspace.tsx          # 改:keep-alive 显隐+预加载+深链跟改
frontend/components/Sidebar.tsx                 # 改:Canvas 入口回归
frontend/features/canvas-core/smart/CanvasComposer.tsx      # 改:WorkflowLibraryPicker portal 化
frontend/components/workspace/EpisodeNodeCard.tsx           # 改:交付物链接
```

PR 切片:**PR-1**=T1-T2(后端底座)/**PR-2**=T3-T4(节点绑定+对账)/**PR-3**=T5-T6(分镜页接线+编辑器退役)/**PR-4**=T7-T9(keep-alive+附带修+收尾)。

---

### Task 1: 分镜画布 migration + get-or-create 端点

**Files:**
- Create: `supabase/migrations/<N>_storyboard_canvas.sql`(N=实施时 max+1)
- Modify: `backend/app/models/canvas.py`(Canvases 加两列)
- Modify: `backend/app/api/canvases_router.py`(新端点,放既有 list/create 端点旁)
- Test: `backend/tests/test_storyboard_canvas.py`(新)

**Interfaces:**
- Consumes: 既有 `_gate_canvas_read/write`(canvases_router 内)、canvases repository/schema(`backend/app/schemas/canvas.py`)。
- Produces: `GET /api/v1/canvases/storyboard?episode_id=<id>` → 200 `{success, data: <画布 doc,同既有 GET /canvases/{id} 形状>}`;幂等 get-or-create;命名 `EP{n} · Storyboard`(n=该集 sort_order 序,取不到用 episode 标题);404 episode 不存在/不属于调用者可达项目;403 无 project_members。Task 5 消费该端点。

- [ ] **Step 1: migration**

```sql
-- <N>_storyboard_canvas.sql
-- 每集一张系统分镜画布(spec §2):kind='storyboard' + episode_id 唯一。
ALTER TABLE public.canvases
  ADD COLUMN IF NOT EXISTS episode_id BIGINT NULL REFERENCES public.episodes(id) ON DELETE CASCADE,
  ADD COLUMN IF NOT EXISTS kind TEXT NOT NULL DEFAULT 'free';
ALTER TABLE public.canvases DROP CONSTRAINT IF EXISTS canvases_kind_check;
ALTER TABLE public.canvases ADD CONSTRAINT canvases_kind_check CHECK (kind IN ('free','storyboard'));
CREATE UNIQUE INDEX IF NOT EXISTS uq_canvases_storyboard_per_episode
  ON public.canvases (project_id, episode_id) WHERE kind = 'storyboard';
COMMENT ON COLUMN public.canvases.kind IS 'free = user canvas; storyboard = system per-episode shot canvas (spec 2026-08-11)';
```

同 commit 把 `backend/app/models/canvas.py` 的 Canvases 模型加 `episode_id: Mapped[Optional[int]] = mapped_column(BigInteger)` 与 `kind: Mapped[str] = mapped_column(Text, server_default='free')`(对齐该文件既有列风格;schema-drift 门禁要求 SQL+ORM 同 PR)。

- [ ] **Step 2: 写失败测试**

```python
# backend/tests/test_storyboard_canvas.py — 测试家族模式照抄 test_episode_owner.py
# (ASGI transport + repository/gate 边界 mock;真 DB gated 则 skip)
async def test_get_or_create_creates_once_then_returns_same(client, episode):
    r1 = await client.get(f"/api/v1/canvases/storyboard?episode_id={episode['id']}")
    assert r1.status_code == 200
    cid = r1.json()["data"]["id"]
    r2 = await client.get(f"/api/v1/canvases/storyboard?episode_id={episode['id']}")
    assert r2.json()["data"]["id"] == cid          # 幂等,不重复建

async def test_created_canvas_is_storyboard_kind_named_after_episode(client, episode):
    data = (await client.get(f"/api/v1/canvases/storyboard?episode_id={episode['id']}")).json()["data"]
    assert data["kind"] == "storyboard"
    assert "Storyboard" in data["name"]

async def test_unknown_episode_404(client):
    r = await client.get("/api/v1/canvases/storyboard?episode_id=999999999999999999")
    assert r.status_code == 404

async def test_non_member_403(client_as_stranger, episode):
    r = await client_as_stranger.get(f"/api/v1/canvases/storyboard?episode_id={episode['id']}")
    assert r.status_code == 403
```

- [ ] **Step 3: 跑测试确认失败**(端点不存在 → 404/405)

Run: `cd backend && uv run pytest tests/test_storyboard_canvas.py -v`

- [ ] **Step 4: 实现端点**

`canvases_router.py` 新增(路径注意放在 `/{canvas_id}` 通配之前注册,避免 'storyboard' 被当 id 吃掉——FastAPI 按注册顺序匹配):

```python
@router.get("/storyboard", summary="Get or create the episode's storyboard canvas")
async def get_or_create_storyboard_canvas(auth: AuthDep, episode_id: str) -> Dict[str, Any]:
    ep = await get_episode_repository().get_by_id(episode_id)      # 404 if None
    if not ep:
        raise HTTPException(404, detail={"code": "episode_not_found"})
    project_id = str(ep["project_id"])
    await _gate_canvas_write(project_id, auth)                     # 复用既有闸(签名以实际为准)
    existing = await repo.get_storyboard_canvas(project_id, episode_id)   # 新 repo 方法:WHERE kind='storyboard' AND episode_id=
    if existing:
        return {"success": True, "data": existing}
    created = await repo.create({..., "project_id": project_id, "episode_id": episode_id,
                                 "kind": "storyboard", "name": _storyboard_name(ep), "nodes_json": []})
    return {"success": True, "data": created}
```

并发双请求撞 partial unique → 捕 IntegrityError 后重读返回既有行(幂等的真正保证是索引,不是应用层判断)。

- [ ] **Step 5: 跑测试确认通过 + 全量后端**

Run: `uv run pytest tests/test_storyboard_canvas.py -v && uv run pytest -q`(0 failed)

- [ ] **Step 6: Commit**

```bash
git add supabase/migrations backend/
git commit -m "feat(canvas): 每集分镜画布 — episode_id/kind 列 + get-or-create 端点"
```

---

### Task 2: 生成回填绑定镜头

**Files:**
- Modify: 画布生成 workflow(`grep -rn "canvas_generation_workflow" backend/app/workflows/` 定位文件)
- Test: `backend/tests/test_canvas_generation_shot_backfill.py`(新)

**Interfaces:**
- Consumes: 生成 workflow 完成时已把结果登记 `generated_media` 并回填节点 data(底账 §4);节点 JSONB 里 Task 3 定义的 `data.shot_id`(str|null)。
- Produces: 当生成的目标节点 `type=='shot'` 且 `data.shot_id` 非空:同一完成路径经 `script_shot_repository` 写 `script_shots.image_url`(结果图 URL)与 `status='done'`,随后 `await fire_surface_sync_for_shot(shot_id)`(`backend/app/services/workflow/surface_completion.py:240`,fire 永不 raise)。写库经 repository,禁裸 SQL。shot_id 无效(行不存在)→ 记 warning 跳过,不 fail 生成。

- [ ] **Step 1: 写失败测试**

```python
async def test_generation_backfills_bound_shot(monkeypatch):
    calls = {}
    monkeypatch.setattr(shot_repo_module, "update_media", lambda sid, **kw: calls.update({"sid": sid, **kw}) or {...})
    fired = []
    monkeypatch.setattr(surface_module, "fire_surface_sync_for_shot", async_recorder(fired))
    await run_generation_completion(node={"type": "shot", "data": {"shot_id": "9007199254740997"}}, result_url="https://cdn/x.png")
    assert calls["sid"] == "9007199254740997" and calls["image_url"].endswith("x.png")
    assert fired == ["9007199254740997"]

async def test_generation_skips_unbound_or_stale_shot(...):
    # data.shot_id 为 None → 不调 repo;shot 行不存在 → warning + 生成仍 success
```

(`run_generation_completion` 指 workflow 的完成步骤函数——先读 workflow 源码,把测试锚在真实函数名/参数上;repo 方法名 `update_media` 若既有名不同,用真实名,生成 workflow 车道 (c) 现有直写点就是参照。)

- [ ] **Step 2-4: RED → 实现 → GREEN**

Run: `uv run pytest tests/test_canvas_generation_shot_backfill.py -v && uv run pytest -k "canvas or shot" -q`

- [ ] **Step 5: Commit**

```bash
git commit -am "feat(canvas): 生成结果回填绑定镜头 — image_url/status + surface 回流"
```

> **PR-1 收口**:T1-T2 `/推送+PR+auto-merge`(与后续前端无编译依赖,先落库)。

---

### Task 3: ShotNodeData 绑定字段 + ShotNodeView 升级

**Files:**
- Modify: `frontend/features/canvas-core/smart/types.ts`(ShotNodeData)
- Modify: `frontend/features/canvas-core/smart/nodes/ShotNodeView.tsx`
- Modify: `frontend/services` 层 shots PATCH 方法(先 grep `script_shots` 的既有前端 service;缺则在既有 script service 文件补 `updateShot(shotId, patch)`)
- Test: `frontend/features/canvas-core/smart/nodes/ShotNodeView.test.tsx`(新/扩)

**Interfaces:**
- Consumes: 底账 §2/§8;既有 patchNode(id,{data}) 通道;生成触发按画布既有 generations 流程(prompt 节点的 Run 范式)。
- Produces:

```ts
export interface ShotNodeData {
  title: string;
  reference_resource_ids: string[];
  notes: string;
  /** 绑定的 script_shots.id(字符串 Snowflake);null=孤立草稿节点(存量兼容) */
  shot_id: string | null;
  /** 绑定态镜像字段(打开画布时对账写入,编辑即回写 PATCH): */
  shot_label: string | null;      // 镜号 如 "1-2"
  shot_type: string | null;       // 景别
  camera_angle: string | null;
  camera_movement: string | null;
  focal_length: string | null;
  description: string | null;
  image_url: string | null;       // 画面帧
  shot_status: string | null;
  /** 绑定镜像的场次(布局/聚焦用) */
  scene_id: string | null;
}
```

渲染(绑定态):镜号 chip + 景别/角度/运动/焦段 chips(可编辑,下拉复用画布 UiSelect)+ 描述 textarea + 画面帧槽(image_url 或「尚无画面」)+「生成」按钮;孤立态(shot_id=null)保持现渲染 + 菜单「转正为镜头」入口(仅发事件 `onPromoteShot(nodeId)`,Task 4 接)。编辑回写:字段变更 debounce 后调 `updateShot(shot_id, {字段})`,同时 patchNode 镜像;失败 → revert 镜像 + toast(`403 details.code` 类型化,generic 兜底),不重试。

- [ ] **Step 1: 写失败测试**

```tsx
it('bound shot node renders label, chips, description and frame slot', () => {
  render(<ShotNodeView data={{...base, shot_id: '9007199254740997', shot_label: '1-2',
    shot_type: 'MEDIUM', focal_length: '35mm', description: 'Dolly in', image_url: null}} ... />);
  expect(screen.getByText('1-2')).toBeTruthy();
  expect(screen.getByText('35mm')).toBeTruthy();
  expect(screen.getByTestId('shot-node-frame-empty')).toBeTruthy();
});
it('editing description patches script_shots and mirrors into node data', async () => { ... updateShot 被调 (shotId, {description}) ... });
it('PATCH failure reverts the mirror and toasts', async () => { ... });
it('unbound node keeps legacy rendering and shows promote menu entry', () => { ... });
```

- [ ] **Step 2-4: RED → 实现 → GREEN**

Run: `cd frontend && npx vitest run features/canvas-core/`(全绿)+ `npx tsc --noEmit`(≤基线)

- [ ] **Step 5: Commit**

```bash
git commit -am "feat(canvas): shot 节点绑定 script_shots — 镜像字段/编辑回写/生成入口"
```

---

### Task 4: 入驻对账(shotSync)+ 删除语义 + 转正

**Files:**
- Create: `frontend/features/canvas-core/smart/shotSync.ts`(纯逻辑)
- Modify: 画布挂载编排处(canvasCoreStore 或画布页组件——读 store 定挂载钩子位)
- Test: `frontend/features/canvas-core/smart/shotSync.test.ts`

**Interfaces:**
- Consumes: Task 3 的 ShotNodeData;scenes+shots 拉取(复用 `editor/sceneService` 的 `listShots`/scenes 拉取,分镜页已在用)。
- Produces:

```ts
export interface ShotSyncResult {
  nodesToAdd: SmartNode<ShotNodeData>[];     // 缺席镜头 → 新节点(默认布局)
  nodesToMarkStale: string[];                // shot_id 已不存在 → 标失效
  nodesToPatch: Array<{ id: string; data: Partial<ShotNodeData> }>; // 镜像字段过期 → 刷新
}
export function reconcileShotNodes(
  existing: SmartNode[],                     // 画布现有全部节点
  scenes: Array<{ id: string; sceneNo: number }>,
  shots: Array<{ id: string; scene_id: string; [k: string]: unknown }>,
): ShotSyncResult;
```

默认布局:场次列 x = sceneIndex*(NODE_W+GUTTER),场内 y 竖排;只对**新增**节点定位,已有节点位置不动。失效节点渲染「已失效」态(灰+提示),在下一次用户保存时由编排层过滤删除。删除节点(绑定态)= 仅移出 nodes_json(React Flow onNodesDelete 默认行为即是,确认不额外调 shots 删除);节点菜单「在分镜列表中删除」= 触发 onOpenShotInList(shotId) 事件(Task 5 接:切视图一并定位)。「转正为镜头」:调 `POST` shots 创建(既有 script_shots_router 创建端点,scene 必选——菜单里选场次)后把 shot_id 写进节点。

- [ ] **Step 1: 写失败测试**(纯函数,穷举:全新画布全量入驻/部分缺席/镜头被删标失效/镜像字段漂移刷新/孤立节点不受对账影响/超 2^53 字符串 id 不丢精度)

- [ ] **Step 2-4: RED → 实现 → GREEN**

Run: `npx vitest run features/canvas-core/smart/shotSync.test.ts features/canvas-core/`

- [ ] **Step 5: Commit**

```bash
git commit -am "feat(canvas): 镜头入驻对账 — 补节点/标失效/镜像刷新,删除仅移出画布"
```

> **PR-2 收口**:T3-T4。

---

### Task 5: 分镜页画布 tab 直达 + 三入口聚焦

**Files:**
- Modify: `frontend/components/workspace/EpisodeStoryboardPage.tsx`(canvas tab 挂分镜画布)
- Modify: `frontend/components/workspace/ProjectWorkspace.tsx`(shotFocusBus 订阅迁入/镜头卡入口跟改)
- Test: `EpisodeStoryboardPage.test.tsx`(扩)

**Interfaces:**
- Consumes: Task 1 端点(get-or-create by episode_id);canvas-core 的嵌入式画布组件(按 canvasId 挂载——读画布路由页找出可复用的画布视图组件;若强耦合路由则抽薄壳,记录取舍);Task 4 对账在挂载时执行;React Flow 实例 `setCenter/fitView` 聚焦。
- Produces: 画布 tab = 当前集分镜画布;`focusShotId`(URL `?view=canvas&shot=` / 视图一镜头卡点击 / `shotFocusBus.requestShotFocus`)→ 切 canvas tab + 视口聚焦该 shot_id 对应节点(节点未入驻时先对账再聚焦)。`onOpenShotInList(shotId)`(Task 4 菜单)→ 切视图一并滚动到该镜头卡。镜头卡点击行为从「深链编辑器」改为本页聚焦(替换 IA 重构那版 handleOpenShotInEditor 通路,旧通路代码删除)。

- [ ] **Step 1: 写失败测试**(canvas tab 挂载调 get-or-create 并渲染画布容器;focusShotId 触发 setCenter 到对应节点 mock;镜头卡点击不再调 openEpisodeScript;shotFocusBus 事件切 tab+聚焦)

- [ ] **Step 2-4: RED → 实现 → GREEN**

Run: `npx vitest run components/workspace/ features/canvas-core/`;e2e `projects-workspace.spec.ts` 实跑保绿。

- [ ] **Step 5: Commit**

```bash
git commit -am "feat(storyboard): 画布 tab 直达分镜画布 — 三入口聚焦,镜头卡不再深链编辑器"
```

---

### Task 6: 编辑器分镜视图退役

**Files:**
- Modify: `frontend/editor/components/EditorShell.tsx`(RailView 删 'storyboard'、onShotFocus 订阅删、pendingFocusShotId 删)
- Modify: `frontend/editor/components/RailModules.tsx`(分镜入口删)
- Delete: `frontend/editor/storyboard/StoryboardView.tsx` 及仅被它引用的子组件(删前 grep 引用确认独占)
- Modify: `frontend/components/workspace/ProjectWorkspace.tsx`(`handleOpenWorkView('storyboard',{sceneId})` 场次深链改落分镜页视图一并定位场次列;`openEpisodeScript` 的 storyboard railView 分支删)
- Test: 相关单测/e2e 跟改(EditorShell storyboard 用例迁移或删除,保意图;`studioFocusSceneId` 相关断言跟改)

**Interfaces:**
- Consumes: Task 5 的分镜页聚焦能力(scene 定位:视图一滚动到 `scene-column-<id>`)。
- Produces: 编辑器仅剩 script/outline/cover;全仓 `grep -rn "storyboard" frontend/editor/` 零业务残留(注释允许);场次卡「打开」落分镜页视图一对应场次列。

- [ ] **Step 1-4: 跟改测试(RED 在删除后自然出现)→ 清理实现 → 全绿**

Run: `npx vitest run` 全前端;e2e `projects-workspace.spec.ts` + `workflow-walkthrough.spec.ts` 实跑;`npx tsc --noEmit` ≤ 基线(删除应只减不增)。

- [ ] **Step 5: Commit**

```bash
git commit -am "refactor(editor): 分镜视图退役 — StoryboardView/rail 入口/shotFocus 订阅删净,场次深链落分镜页"
```

> **PR-3 收口**:T5-T6。

---

### Task 7: keep-alive 显隐切换 + chunk 预加载

**Files:**
- Modify: `frontend/components/workspace/ProjectWorkspace.tsx`(editor 与 storyboard 两模块持久挂载容器)
- Test: `ProjectWorkspace.test.tsx`(扩)

**Interfaces:**
- Produces: `studioMode` 与 `activeModule==='storyboard'` 的两棵子树改为**同时挂载**,以 `style={{display: active ? '' : 'none'}}` 容器切换;非 active 模块不响应 URL 聚焦副作用(effect 加 active 守卫,防隐藏页抢 focus);切集仍卸载重挂(现行为保留)。进入工作区后 `requestIdleCallback(() => import('./EpisodeStoryboardPage'))` 预热 chunk(SSR/降级 setTimeout)。

- [ ] **Step 1: 写失败测试**(切 storyboard 再切回 script:EditorShell mock 未被卸载(实例保持,断言 mock 组件 unmount 未触发);隐藏态不触发 focus effect;预加载在挂载后被调)

- [ ] **Step 2-4: RED → 实现 → GREEN**

Run: `npx vitest run components/workspace/`;e2e `projects-workspace.spec.ts` 实跑(显隐切换对 e2e 可见性断言的影响跟改:`toBeVisible` 语义仍成立,隐藏树用 `display:none` 保证 not visible)。

- [ ] **Step 5: Commit**

```bash
git commit -am "perf(workspace): 编辑器/分镜页 keep-alive 显隐切换 + 分包空闲预加载"
```

---

### Task 8: Canvas 侧栏入口回归 + WorkflowLibraryPicker portal 修复

**Files:**
- Modify: `frontend/components/Sidebar.tsx`(Canvas 导航项回归,`CANVAS_NAV_ENABLED` flag 复用;对照旧 commit `c5b6e18e` 的实现形状)
- Modify: `frontend/features/canvas-core/smart/CanvasComposer.tsx` + `WorkflowLibraryPicker.tsx`(弹层 createPortal 到 body + fixed 定位,DateRangePopover 同款测高翻转;或最小改:弹层挂到 composer 外层无 overflow 容器——两案选实现风险小者,报告写取舍)
- Test: e2e `canvas-nav.spec.ts:91` 与 `canvas-feel.spec.ts:228` **复活**(两张产品票的验收就是这两条既有用例转绿)+ 相关单测

- [ ] **Step 1-4: 两条 e2e 当 RED → 修复 → 实跑转绿**

Run: `npx playwright test e2e/canvas-nav.spec.ts e2e/canvas-feel.spec.ts`(build+preview 实跑;canvas-feel 其余既有失败不在本任务范围,只要 :228 转绿且无新增红)

- [ ] **Step 5: Commit**

```bash
git commit -am "fix(canvas): 侧栏入口回归(#1680 遗失) + WorkflowLibraryPicker portal 化脱离 overflow 裁剪"
```

---

### Task 9: 节点卡交付物链接 + 收尾全量

**Files:**
- Modify: `frontend/components/workspace/EpisodeNodeCard.tsx`(交付物 fact → 链接:`onOpenStage(node.id)` 通道,ProjectWorkspace 已有 handleOpenStage)
- Test: `EpisodeNodeCard.test.tsx`(扩:交付物点击调 onOpenStage;只读态同样可点——它是导航不是编辑)

**收尾全量验证**:`npx vitest run`(全前端)+ `npx tsc --noEmit`(≤基线)+ `cd backend && uv run pytest -q`(0 failed)+ e2e 全套实跑(报告 pass/fail 与基线对比:canvas-nav:91 与 canvas-feel:228 应转绿,其余不回退)。

- [ ] **Step 1-4: RED → 实现 → GREEN → 全量**
- [ ] **Step 5: Commit**

```bash
git commit -am "feat(overview): 节点卡交付物链接直达 Stage Board + 分镜画布收尾全量验证"
```

> **PR-4 收口**:T7-T9,`/推送+PR+auto-merge`。

---

## Self-Review 记录

- **Spec 覆盖**:§2=T1;§3=T3+T4(+T2 生成);§4=T1+T2;§5=T5;§6=T6;§7=T7;§8=T8+T9;§9 范围外未触碰;§10 测试分布各任务。无缺口。
- **占位符**:T2 的 `run_generation_completion`/repo 方法名、T5 的嵌入画布组件名是「实施时按真实源码锚定」的显式指令(底账给了 grep 锚),非 TBD;其余任务有可运行代码/断言。
- **类型一致性**:`ShotNodeData`(T3→T4/T5)、`reconcileShotNodes`(T4→T5 挂载编排)、get-or-create 端点形状(T1→T5)、`onOpenShotInList`(T4→T5)、`fire_surface_sync_for_shot`(T2,既有签名)已对齐。
