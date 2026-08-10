# 项目工作区 IA 重构 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 按 spec `docs/superpowers/specs/2026-08-10-workspace-ia-redesign-design.md` 落地四层 IA:总览手风琴、单节点信息卡(角色门控行内编辑)、分镜独立成页(设计稿分列布局)、设置节点配置 + 统一 URL 寻址 + `episodes.owner_id` 权限模型。

**Architecture:** 前端以 `ProjectWorkspace.tsx` 的 module 路由为骨架:新增 `storyboard` 模块承接三视图(从 Overview 迁出),Overview 重写为手风琴(WorkflowStrip 复用 + 新 EpisodeNodeCard),URL searchParams 扩展为 `ep/node/view/scene/shot` 全寻址。后端一条 migration(`episodes.owner_id`)+ 两处权限收紧(节点字段 PATCH、剧集编排端点)。4 个 PR 切片:PR-1(Task 1–3 路由+分镜页)、PR-2(Task 4–5 手风琴)、PR-3(Task 6–9 权限+编辑+设置)、PR-4(Task 10 编辑器胶囊+收尾)。

**Tech Stack:** React 19 + TS + Vite(vitest + @testing-library/react);FastAPI + SQLAlchemy async + pytest;SQL migration(三位数编号)。

## Global Constraints

- **UI 文本一律走 i18n**:代码里 `t('key', 'English Default')`,`frontend/public/locales/en.json` 与 `zh.json` 同步补齐(en 值 Title Case;禁硬编码中文)。
- **语义色 token**(ok/warn/danger/info/agent + island/line/ink 梯),禁 indigo/amber/emerald 等旧色相字面量。
- **图标用 lucide-react**,禁 emoji。
- **所有实体 ID 在 URL/props/事件载荷中一律字符串**(Snowflake BIGINT 超 2^53,禁 `Number()`);组件间传 ID 不传数组下标。
- 前端测试:`cd frontend && npx vitest run <file>`;该 worktree `tsc` 基线约 59 个既有错误,不新增即可。
- 后端测试:`cd backend && uv run pytest <file> -v`。
- migration 取号:实施时先 `git fetch origin master && ls supabase/migrations/ | sort | tail -3` 再取 max+1(写作时最新为 414,且撞号治理 PR 可能占走 415)。
- 每个 Task 独立提交;commit message 结尾带 `Claude-Session: https://claude.ai/code/session_01KvXWh2z8qRAE3yUgUDq4sW`。

## File Structure(全景)

```
frontend/components/workspace/
  ProjectWorkspace.tsx        # 改:URL 寻址、storyboard 模块路由、surface panel 迁出
  WorkspaceOverview.tsx       # 重写:手风琴集列表(删 Continue 卡/tiles/WorkflowSection 堆叠)
  EpisodeSummaryRow.tsx       # 改:可展开行(chevron + 展开态)
  EpisodeNodeCard.tsx         # 新:单节点信息卡(只读 facts + 角色门控行内编辑 + advance)
  EpisodeStoryboardPage.tsx   # 新:分镜页(页头 tab 靠左 + 三视图容器,从 ProjectWorkspace 的 surface panel 块迁来)
  EpisodeSceneBoard.tsx       # 改:全宽横条 → 设计稿窄列分列
  WorkspaceSidebar.tsx        # 改:分镜行直达 storyboard 模块
  WorkspaceTopBar.tsx         # 改:studio 模式流程胶囊
  ProjectWorkspace 内设置块    # 改:新增「节点配置」区(新文件 WorkspaceNodeSettings.tsx)
frontend/components/common/
  DateRangePopover.tsx        # 新:日历区间选择器(portal + fixed + 实测高度翻转)
backend/
  supabase/migrations/<N>_episode_owner.sql   # 新:episodes.owner_id
  app/models/scripts.py       # 改:Episodes.owner_id
  app/api/episodes_router.py  # 改:PATCH 接受 owner_id(仅项目负责人)
  app/api/projects_router.py  # 改:节点字段 PATCH 权限(项目负责人∨集负责人)、编排端点收紧
```

---

### Task 1: URL 统一寻址(ep/node/view/scene/shot)

**Files:**
- Modify: `frontend/components/workspace/ProjectWorkspace.tsx`(searchParams 块 ~L110-140、episode 初始化 ~L155-175、`handleEpisodeChange` ~L245)
- Test: `frontend/components/workspace/ProjectWorkspace.urlparams.test.tsx`(新)

**Interfaces:**
- Consumes: 现有 `useSearchParams`、`episodeStorageKey(projectId)` localStorage 键。
- Produces: URL 参数约定 `?module=&ep=&node=&view=&scene=&shot=`;`currentEpisodeId` 初始化优先级 = URL `ep` > localStorage > 首集;导出 helper `readWorkspaceParams(searchParams): { ep: string|null; node: string|null; view: string|null; scene: string|null; shot: string|null }`(供 Task 2/4 消费,放在 `ProjectWorkspace.tsx` 顶部 export)。

- [ ] **Step 1: 写失败测试**

```tsx
// ProjectWorkspace.urlparams.test.tsx
import { describe, it, expect } from 'vitest';
import { readWorkspaceParams } from './ProjectWorkspace';

describe('readWorkspaceParams', () => {
  it('reads all five params as strings', () => {
    const sp = new URLSearchParams(
      'module=storyboard&ep=324362669885098&node=316961365523510&view=canvas&scene=9007199254740995&shot=9007199254740997',
    );
    expect(readWorkspaceParams(sp)).toEqual({
      ep: '324362669885098', node: '316961365523510',
      view: 'canvas', scene: '9007199254740995', shot: '9007199254740997',
    });
  });
  it('missing params are null, never NaN or empty string', () => {
    const sp = new URLSearchParams('module=overview');
    const p = readWorkspaceParams(sp);
    expect(p).toEqual({ ep: null, node: null, view: null, scene: null, shot: null });
  });
});
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd frontend && npx vitest run components/workspace/ProjectWorkspace.urlparams.test.tsx`
Expected: FAIL(`readWorkspaceParams` 未导出)

- [ ] **Step 3: 实现**

`ProjectWorkspace.tsx` 顶部(组件外)加:

```tsx
export function readWorkspaceParams(sp: URLSearchParams) {
  const get = (k: string) => {
    const v = sp.get(k);
    return v && v.length > 0 ? v : null;
  };
  return { ep: get('ep'), node: get('node'), view: get('view'), scene: get('scene'), shot: get('shot') };
}
```

接线(都在既有位置小改,不动行为语义):
1. episode 初始化 effect(~L155):`const fromUrl = readWorkspaceParams(searchParams).ep;` 优先于 `localStorage.getItem(...)`,仍校验 `rows.some(r => r.episode_id === fromUrl)`。
2. `handleEpisodeChange` 里同步写 URL:`setSearchParams(prev => { const n = new URLSearchParams(prev); n.set('ep', episodeId); return n; }, { replace: true })`(保留现有 localStorage 写入作降级默认)。
3. 现有 module/node 同步 effect(~L130)扩展:非 stage 模块也保留 `node`(手风琴选中节点,Task 4 写入);`view/scene/shot` 由 Task 2 写入,本 task 只保证 helper 与 ep。

- [ ] **Step 4: 跑测试确认通过**

Run: `cd frontend && npx vitest run components/workspace/ProjectWorkspace.urlparams.test.tsx`
Expected: PASS。再跑 `npx vitest run components/workspace/ProjectWorkspace.test.tsx` 确认既有测试不回退。

- [ ] **Step 5: Commit**

```bash
git add frontend/components/workspace/ProjectWorkspace.tsx frontend/components/workspace/ProjectWorkspace.urlparams.test.tsx
git commit -m "feat(workspace): URL 统一寻址 — ep 参数为当前集第一真相,readWorkspaceParams helper"
```

---

### Task 2: 分镜页独立成页(storyboard 模块)

**Files:**
- Create: `frontend/components/workspace/EpisodeStoryboardPage.tsx`
- Modify: `frontend/components/workspace/ProjectWorkspace.tsx`(`WorkspaceModule` 联合类型 ~L117 加 `'storyboard'`;`handleOpenWorkView` ~L419 storyboard 分支;`handleSelectNode` ~L505;surface panel 块 ~L717-830 整块迁出;`showSurfacePanel`/`surfaceViews`/`surfaceScript`/`episodeView` 状态随迁)
- Modify: `frontend/components/workspace/WorkspaceSidebar.tsx`(分镜行,~L274 附近的 `modules.storyboard` 点击)
- Test: `frontend/components/workspace/EpisodeStoryboardPage.test.tsx`(新)

**Interfaces:**
- Consumes: Task 1 的 `readWorkspaceParams`;现组件 `EpisodeViewTabs`(props `views/active/onChange/actions`)、`EpisodeSceneBoard`(props `scriptId/onOpenScene`)、`EpisodeShotListTable`(ref handle `exportCsv()`)、`WorkspaceCanvas`(props `projectId/teamId`)、`shotFocusBus.requestShotFocus(shotId: string)`;`findExistingScript(episode)` 只读探针与 `resolveOrProvisionScript(episode)`(均在 ProjectWorkspace,以 props 传入)。
- Produces:

```tsx
export interface EpisodeStoryboardPageProps {
  projectId: string;
  teamId: string;
  episode: EpisodeProgress | null;
  /** 'board' | 'canvas' | 'shotlist',来自 URL ?view=,缺省 'board' */
  initialView: string | null;
  /** URL ?shot=:进页后切 canvas 并 requestShotFocus */
  focusShotId: string | null;
  onViewChange: (view: string) => void;   // 回写 URL
  findExistingScript: (ep: EpisodeProgress) => Promise<string | null>;
  provisionScript: (ep: EpisodeProgress) => Promise<string | null>;
  /** 场次卡 Open 深链 → 编辑器 scene 级(既有 handleOpenWorkView('storyboard',{sceneId})) */
  onOpenScene: (sceneId: string) => void;
}
```

- [ ] **Step 1: 写失败测试**

```tsx
// EpisodeStoryboardPage.test.tsx — 三个关键行为
import { render, screen, waitFor } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';
import { EpisodeStoryboardPage } from './EpisodeStoryboardPage';
import * as bus from '../agentActivity/shotFocusBus';

const ep = { episode_id: '324362669885098', title: 'EP1', scene_count: 7,
  shots_done: 0, shots_total: 6, renders_count: 0, status: 'in_progress' } as any;
const base = {
  projectId: 'p1', teamId: 't1', episode: ep, initialView: null, focusShotId: null,
  onViewChange: vi.fn(), onOpenScene: vi.fn(),
  findExistingScript: vi.fn().mockResolvedValue('sc1'),
  provisionScript: vi.fn().mockResolvedValue('sc1'),
};

describe('EpisodeStoryboardPage', () => {
  it('renders left-aligned view tabs with board active by default', async () => {
    render(<EpisodeStoryboardPage {...base} />);
    const tabs = await screen.findByTestId('episode-view-tabs');
    expect(tabs.querySelector('[data-view="storyboard"][aria-selected="true"]')).toBeTruthy();
  });
  it('shows Start Storyboard empty state when no script, without provisioning', async () => {
    render(<EpisodeStoryboardPage {...base} findExistingScript={vi.fn().mockResolvedValue(null)} />);
    expect(await screen.findByTestId('episode-surface-no-script')).toBeTruthy();
    expect(base.provisionScript).not.toHaveBeenCalled();
  });
  it('focusShotId switches to canvas and fires shotFocusBus', async () => {
    const spy = vi.spyOn(bus, 'requestShotFocus');
    render(<EpisodeStoryboardPage {...base} initialView="canvas" focusShotId="9007199254740997" />);
    await waitFor(() => expect(spy).toHaveBeenCalledWith('9007199254740997'));
  });
});
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd frontend && npx vitest run components/workspace/EpisodeStoryboardPage.test.tsx`
Expected: FAIL(组件不存在)

- [ ] **Step 3: 实现组件 + 迁移路由**

`EpisodeStoryboardPage.tsx`:把 ProjectWorkspace 里 `showSurfacePanel && activeEpisodeView` 那整块 JSX(EpisodeViewTabs + storyboard/canvas/shotlist 三分支 + `renderSurfaceScriptGate`)与配套状态(`surfaceScript` probe effect、`handleStartStoryboard`、`shotListTableRef`)搬进来,收敛为上面的 props 接口。页头:

```tsx
<div className="flex items-center gap-3 border-b border-line px-4 py-2.5">
  <span className="text-[13.5px] font-bold">{t('projects.storyboardPage.title', 'Storyboard')}</span>
  <span className="font-mono text-[11px] text-ink-500">{episode?.shots_total ?? 0}</span>
  <EpisodeViewTabs views={STORYBOARD_VIEWS} active={view} onChange={handleView} />
  {view === 'shotlist' && (
    <button type="button" data-testid="ep-shotlist-export" className="ml-auto ..." onClick={...}>
      {t('projects.shotList.export')}
    </button>
  )}
</div>
```

`STORYBOARD_VIEWS` 固定三项(不再依赖当前节点 surface——这页永远是分镜):`[{key:'storyboard',labelKey:'projects.episodeViews.storyboard'},{key:'canvas',...},{key:'shotlist',...}]`(key 复用 nodeSurface.ts 的既有 SURFACE_VIEWS key,避免 i18n 新键)。

focus 效果:

```tsx
useEffect(() => {
  if (!focusShotId) return;
  setView('canvas');
  const t = window.setTimeout(() => requestShotFocus(focusShotId), 300); // canvas 挂载后
  return () => window.clearTimeout(t);
}, [focusShotId]);
```

ProjectWorkspace 路由改动:
1. `'storyboard'` 加进 module 联合与合法值数组(~L117)。
2. `handleOpenWorkView('storyboard')` 无 sceneId 分支:`setActiveModule('storyboard')`(替换原 `setActiveModule('overview'); setEpisodeView('storyboard')`);写 URL `view` 由 `onViewChange` 回调统一做。
3. `handleSelectNode` 的 storyboard case 不变(仍走 handleOpenWorkView)。
4. WorkspaceSidebar 分镜行:确认其点击就是 `onOpenWorkView('storyboard')`,无需改(路由行为在 2 已换)。
5. 渲染分支:`{activeModule === 'storyboard' && <EpisodeStoryboardPage ... initialView={readWorkspaceParams(searchParams).view} focusShotId={readWorkspaceParams(searchParams).shot} />}`;原 Overview 上的 surface panel 块**整块删除**(`showSurfacePanel`/`surfaceViews`/`episodeView` 等状态若仅它使用则一并删除)。

- [ ] **Step 4: 跑测试**

Run: `cd frontend && npx vitest run components/workspace/`
Expected: 新测试 PASS;既有 ProjectWorkspace/EpisodeViewTabs 测试如引用了 surface panel 行为需跟改(改断言到新模块,不得删测试意图)。

- [ ] **Step 5: Commit**

```bash
git add -A frontend/components/workspace/
git commit -m "feat(workspace): 分镜独立成页 — storyboard 模块直达,三视图迁出 Overview,tab 靠左"
```

---

### Task 3: EpisodeSceneBoard 分列布局 + 镜头卡进画布

**Files:**
- Modify: `frontend/components/workspace/EpisodeSceneBoard.tsx`(布局改造;数据逻辑 `autoStoryboard`/`listShots` 不动)
- Modify: `frontend/components/workspace/EpisodeStoryboardPage.tsx`(传 `onOpenShot`)
- Test: `frontend/components/workspace/EpisodeSceneBoard.test.tsx`(既有,跟改+新增)

**Interfaces:**
- Consumes: Task 2 的页面容器。
- Produces: `EpisodeSceneBoardProps` 增 `onOpenShot: (shotId: string) => void`(镜头卡点击载荷 = shot id 字符串);页面容器实现为 `onOpenShot={(id) => { setView('canvas'); onViewChange('canvas'); requestShotFocus(id); 同步写 URL shot=id }}`。

- [ ] **Step 1: 写失败测试**(在既有 test 文件追加)

```tsx
it('lays scenes out as fixed-width columns, not full-width rows', async () => {
  render(<EpisodeSceneBoard scriptId="sc1" onOpenScene={vi.fn()} onOpenShot={vi.fn()} />);
  const col = await screen.findByTestId('scene-column-9007199254740995'); // data-testid=`scene-column-${scene.id}`
  expect(col.className).toMatch(/w-\[236px\]/);
  expect(screen.getByTestId('scene-columns').className).toMatch(/overflow-x-auto/);
});
it('clicking a shot card reports the shot id', async () => {
  const onOpenShot = vi.fn();
  render(<EpisodeSceneBoard scriptId="sc1" onOpenScene={vi.fn()} onOpenShot={onOpenShot} />);
  (await screen.findByTestId('shot-card-9007199254740997')).click();
  expect(onOpenShot).toHaveBeenCalledWith('9007199254740997');
});
```

(mock 的 scenes/shots fixture 沿用该测试文件现有 service mock,补一个超 2^53 的字符串 id 断言精度。)

- [ ] **Step 2: 跑测试确认失败**

Run: `cd frontend && npx vitest run components/workspace/EpisodeSceneBoard.test.tsx`
Expected: 新增两条 FAIL。

- [ ] **Step 3: 实现**

布局骨架(设计稿视图一,场记条+元数据格+按钮行+镜头卡竖排):

```tsx
<div data-testid="scene-columns" className="flex gap-3 overflow-x-auto pb-2">
  {scenes.map((scene) => (
    <div key={scene.id} data-testid={`scene-column-${scene.id}`}
         className="w-[236px] flex-none overflow-hidden rounded-lg border border-line bg-island">
      <div className="h-5 border-b border-line bg-[repeating-linear-gradient(115deg,var(--line)_0_9px,transparent_9px_18px)]" />
      <div className="grid grid-cols-[1fr_auto] gap-x-2.5 gap-y-0.5 px-3 py-2 font-mono">
        {/* SCENE/I-E/LOCATION/D-N 四格,label 用 text-[9px] uppercase text-ink-500 */}
      </div>
      <div className="flex gap-1.5 px-3 pb-2.5">{/* 打开 / 自动分镜(既有按钮与派发逻辑) */}</div>
      <div className="flex flex-col gap-2 px-3 pb-3">
        {shotsFor(scene.id).map((shot) => (
          <button key={shot.id} type="button" data-testid={`shot-card-${shot.id}`}
                  onClick={() => onOpenShot(String(shot.id))}
                  className="rounded-md border border-dashed border-line bg-island-2 px-2 py-2.5 text-center hover:border-agent-line hover:bg-agent-soft">
            <span className="block font-mono text-[11.5px] font-bold text-ink-300">
              {shot.shotLabel}
            </span>
            <span className="text-[10.5px] text-ink-500">
              {t('projects.storyboardPage.shotHint', 'Open on canvas')}
            </span>
          </button>
        ))}
      </div>
    </div>
  ))}
</div>
```

空场景/无镜头空态复用现文案键;删除旧全宽横条 JSX。

- [ ] **Step 4: 跑测试**

Run: `cd frontend && npx vitest run components/workspace/EpisodeSceneBoard.test.tsx components/workspace/EpisodeStoryboardPage.test.tsx`
Expected: 全 PASS。

- [ ] **Step 5: Commit**

```bash
git add frontend/components/workspace/EpisodeSceneBoard.tsx frontend/components/workspace/EpisodeSceneBoard.test.tsx frontend/components/workspace/EpisodeStoryboardPage.tsx
git commit -m "feat(storyboard): 场次分列布局按设计稿 236px 窄列 + 镜头卡点击进画布聚焦"
```

> **PR-1 收口**:Task 1–3 完成后 `/ship`(含 en/zh i18n 新键、e2e 跟改:`ws-module-storyboard` 直达断言)。

---

### Task 4: 总览手风琴(WorkspaceOverview 重写)

**Files:**
- Modify: `frontend/components/workspace/WorkspaceOverview.tsx`(重写)
- Modify: `frontend/components/workspace/EpisodeSummaryRow.tsx`(可展开行)
- Modify: `frontend/components/workspace/ProjectWorkspace.tsx`(传 `expandedEpisodeId`/`selectedNodeId` 与 URL 同步)
- Test: `frontend/components/workspace/WorkspaceOverview.test.tsx`(既有,重写)

**Interfaces:**
- Consumes: Task 1 URL helper;`WorkflowStrip`(现组件,props 见其文件:nodes/current/onSelectNode 等,按实际签名接);Task 5 的 `EpisodeNodeCard`(本 task 先用占位插槽 `renderNodeCard(episodeId, nodeId)` prop,Task 5 填充)。
- Produces:

```tsx
export interface WorkspaceOverviewProps {   // 替换原 props
  project: Project;
  episodes: EpisodeProgress[];
  workflow?: ProjectWorkflow | null;        // 全项目节点,内部按 episode_id 过滤
  expandedEpisodeId: string | null;         // URL ep=
  selectedNodeId: string | null;            // URL node=
  onExpandEpisode: (episodeId: string | null) => void;  // null=收起
  onSelectNode: (nodeId: string) => void;
  renderNodeCard: (episodeId: string, nodeId: string | null) => React.ReactNode;
}
```

- [ ] **Step 1: 写失败测试**

```tsx
it('renders one row per episode and no summary tiles / continue card / surface panel', () => {
  render(<WorkspaceOverview {...base} />);
  expect(screen.getAllByTestId(/^ep-accordion-row-/)).toHaveLength(4);
  expect(screen.queryByTestId('ws-overview-summary')).toBeNull();
  expect(screen.queryByTestId('ws-continue-card')).toBeNull();
  expect(screen.queryByTestId('episode-surface-panel')).toBeNull();
});
it('expanded row shows that episode strip and node card slot; others collapsed', () => {
  render(<WorkspaceOverview {...base} expandedEpisodeId="ep1" selectedNodeId="n2" />);
  expect(screen.getByTestId('ep-accordion-body-ep1')).toBeTruthy();
  expect(screen.queryByTestId('ep-accordion-body-ep2')).toBeNull();
  expect(screen.getByTestId('node-card-slot').textContent).toContain('CARD:ep1:n2');
});
it('clicking an expanded row collapses it (onExpandEpisode null)', () => {
  const onExpandEpisode = vi.fn();
  render(<WorkspaceOverview {...base} expandedEpisodeId="ep1" onExpandEpisode={onExpandEpisode} />);
  fireEvent.click(screen.getByTestId('ep-accordion-row-ep1'));
  expect(onExpandEpisode).toHaveBeenCalledWith(null);
});
```

(`base.renderNodeCard = (e, n) => <div data-testid="node-card-slot">CARD:{e}:{n}</div>`;episodes fixture 用字符串 id `'ep1'...'ep4'`。)

- [ ] **Step 2: 跑测试确认失败**

Run: `cd frontend && npx vitest run components/workspace/WorkspaceOverview.test.tsx`
Expected: FAIL。

- [ ] **Step 3: 实现**

WorkspaceOverview 重写为:头行(`剧集 · N` + awaiting 计数,逻辑照抄现文件 L68-75)+ `episodes.map` 手风琴项。EpisodeSummaryRow 改造:外层 `div`(open 态 border-agent-line),行本体变 `<button data-testid={'ep-accordion-row-'+ep.episode_id}>`(原有 EP 号/标题/计数/分段条/状态全保留,尾部加 lucide `ChevronDown` 旋转);展开体 `data-testid={'ep-accordion-body-'+id}` 内渲染:

```tsx
<WorkflowStrip nodes={nodesOfEpisode} currentNodeId={...} focusNodeId={selectedNodeId}
  onSelectNode={(node) => onSelectNode(String(node.id))} />
{renderNodeCard(ep.episode_id, selectedNodeId ?? currentNodeIdOfEpisode)}
```

(`nodesOfEpisode = workflow?.nodes.filter(n => String(n.episode_id) === ep.episode_id)`;WorkflowStrip 的实际 props 以其源码为准,若无 `focusNodeId` 高亮位则以 `onSelectNode` + 卡片切换表达选中。)
ProjectWorkspace:`onExpandEpisode` = 写 URL `ep`(复用 handleEpisodeChange)+ 展开态;`onSelectNode` = 写 URL `node`。删除对 `WorkflowSection`/`SummaryTile`/Continue 卡的引用与 `bg-indigo-500` 类。

- [ ] **Step 4: 跑测试**

Run: `cd frontend && npx vitest run components/workspace/`
Expected: PASS;`grep -rn "indigo" frontend/components/workspace/WorkspaceOverview.tsx` 零命中。

- [ ] **Step 5: Commit**

```bash
git add -A frontend/components/workspace/
git commit -m "feat(overview): 总览手风琴 — 集行展开流程条+节点卡插槽,删 tiles/Continue/indigo"
```

---

### Task 5: EpisodeNodeCard(单节点信息卡,先只读+advance)

**Files:**
- Create: `frontend/components/workspace/EpisodeNodeCard.tsx`
- Modify: `frontend/components/workspace/ProjectWorkspace.tsx`(`renderNodeCard` 实装)
- Test: `frontend/components/workspace/EpisodeNodeCard.test.tsx`

**Interfaces:**
- Consumes: `ProjectStageNode`(types.ts,含 owner/schedule/deliverable 字段以实际类型为准)、`resolveSurface(node)`(nodeSurface.ts)、`requestAdvance` 通道与 `onOpenTodolist`(CurrentNodeCard 同款 props 形状)。
- Produces:

```tsx
export interface EpisodeNodeCardProps {
  node: ProjectStageNode;
  /** 观察者是否可编辑负责人/排期(Task 9 前恒 false → 全只读) */
  canEditConfig: boolean;
  isCursorNode: boolean;              // 非游标节点隐藏 完成阶段/回退(同 CurrentNodeCard isActive 语义)
  onEnterSurface: (node: ProjectStageNode) => void;   // 复用 handleSelectNode
  onRequestAdvance: (direction: 'forward' | 'back') => void;
  onOpenTodolist: () => void;
  onOpenSettings: (episodeId: string, nodeId: string) => void; // 深链设置节点配置
}
```

- [ ] **Step 1: 写失败测试**

```tsx
const node = { id: 'n2', name: 'Storyboard', status: 'pending', episode_id: 'ep1',
  owner_name: null, schedule_start: null, schedule_end: null,
  deliverable_name: 'Shot list + boards', archived_count: 0 } as any;

it('renders name, status, entry button by surface, and readonly facts', () => {
  render(<EpisodeNodeCard node={node} canEditConfig={false} isCursorNode
    onEnterSurface={vi.fn()} onRequestAdvance={vi.fn()} onOpenTodolist={vi.fn()} onOpenSettings={vi.fn()} />);
  expect(screen.getByTestId('node-card-enter').textContent).toMatch(/Storyboard|Open/);
  expect(screen.getByTestId('node-card-owner').textContent).toMatch(/Unassigned/);
  expect(screen.getByTestId('node-card-owner').tagName).toBe('SPAN'); // 只读=非按钮
});
it('advance buttons only on cursor node', () => {
  const { rerender } = render(<EpisodeNodeCard node={node} canEditConfig={false} isCursorNode {...cbs} />);
  expect(screen.getByTestId('node-card-advance')).toBeTruthy();
  rerender(<EpisodeNodeCard node={node} canEditConfig={false} isCursorNode={false} {...cbs} />);
  expect(screen.queryByTestId('node-card-advance')).toBeNull();
});
it('enter button reports the node object', () => {
  const onEnterSurface = vi.fn();
  render(<EpisodeNodeCard node={node} canEditConfig={false} isCursorNode onEnterSurface={onEnterSurface} {...rest} />);
  fireEvent.click(screen.getByTestId('node-card-enter'));
  expect(onEnterSurface).toHaveBeenCalledWith(node);
});
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd frontend && npx vitest run components/workspace/EpisodeNodeCard.test.tsx`
Expected: FAIL。

- [ ] **Step 3: 实现**

结构 = 方案稿 §3:头部(名+状态+`data-testid="node-card-enter"` 入口按钮,文案按 `resolveSurface`:script→`t('projects.nodeCard.openScript','Open Script')`/storyboard→`Open Storyboard`/renders→`Open Renders`/null→`Open Stage Board`);facts 行(负责人/排期/交付物链接);底部(`在待办中打开` + 权限灰字(onOpenSettings 深链)+ cursor 节点才有 `data-testid="node-card-advance"` 的 回退/完成阶段)。只读态渲染 `<span>`;`canEditConfig` 分支本 task 只留 props 通道(恒只读渲染),Task 9 填充。ProjectWorkspace 的 `renderNodeCard` 实装:按 id 找 node,`canEditConfig={false}`,`onEnterSurface={handleSelectNode}`,`onOpenSettings={(ep,node)=>setSearchParams(...module=settings&tab=nodes&ep=&node=...)}`。

- [ ] **Step 4: 跑测试**

Run: `cd frontend && npx vitest run components/workspace/`
Expected: PASS。

- [ ] **Step 5: Commit**

```bash
git add frontend/components/workspace/EpisodeNodeCard.tsx frontend/components/workspace/EpisodeNodeCard.test.tsx frontend/components/workspace/ProjectWorkspace.tsx
git commit -m "feat(overview): 单节点信息卡 — 只读 facts + surface 入口 + 游标节点 advance"
```

> **PR-2 收口**:Task 4–5 后 `/ship`;e2e 跟改重点:原依赖 Overview `WorkflowSection` strip 的用例(`workflow-deps`/`stage-board` 系)改为「展开 EP1 → 点节点」路径。

---

### Task 6: `episodes.owner_id` migration + PATCH

**Files:**
- Create: `supabase/migrations/<N>_episode_owner.sql`(N = 实施时 max+1,见 Global Constraints)
- Modify: `backend/app/models/scripts.py`(Episodes 加列)
- Modify: `backend/app/api/episodes_router.py`(PATCH 接受 `owner_id`,仅项目负责人可改)
- Test: `backend/tests/test_episode_owner.py`

**Interfaces:**
- Consumes: `verify_episode_write_access`(episodes_router 既有)、`resolve_effective_role`(#1742,项目负责人 → 'manager')。
- Produces: `Episodes.owner_id: Mapped[Optional[uuid.UUID]]`;PATCH `/api/v1/episodes/{id}` body 可含 `owner_id: str|null`;权限失败返回 `403 {"detail": {"code": "episode_owner_forbidden"}}`。Task 7 依赖 `owner_id` 列做「集负责人」判定。

- [ ] **Step 1: migration**

```sql
-- <N>_episode_owner.sql
-- 集负责人(spec §5 方案 A):项目负责人在设置里指派;为空 = 未指派。
ALTER TABLE public.episodes
  ADD COLUMN IF NOT EXISTS owner_id UUID NULL REFERENCES auth.users(id) ON DELETE SET NULL;
COMMENT ON COLUMN public.episodes.owner_id IS 'Episode owner (workspace IA redesign); may edit node owner/schedule of this episode';
```

同批把模型改了(schema-drift 门禁要求 SQL 与 ORM 同 PR):`Episodes` 加 `owner_id: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid)`(import 对齐该文件既有 Uuid 用法)。

- [ ] **Step 2: 写失败测试**

```python
# backend/tests/test_episode_owner.py — 真-DB gated 风格照抄 test_episodes_scenes_authz_wiring.py 的 fixture
async def test_project_owner_can_set_episode_owner(client_as_owner, episode):
    r = await client_as_owner.patch(f"/api/v1/episodes/{episode['id']}", json={"owner_id": MEMBER_UUID})
    assert r.status_code == 200
    assert r.json()["data"]["owner_id"] == MEMBER_UUID

async def test_non_owner_cannot_set_episode_owner(client_as_member, episode):
    r = await client_as_member.patch(f"/api/v1/episodes/{episode['id']}", json={"owner_id": MEMBER_UUID})
    assert r.status_code == 403
    assert r.json()["detail"]["code"] == "episode_owner_forbidden"

async def test_patch_without_owner_field_unaffected(client_as_member, episode):
    r = await client_as_member.patch(f"/api/v1/episodes/{episode['id']}", json={"title": "Renamed"})
    assert r.status_code == 200   # 既有字段权限不因本改动收紧
```

- [ ] **Step 3: 跑测试确认失败**

Run: `cd backend && uv run pytest tests/test_episode_owner.py -v`
Expected: FAIL(owner_id 不是合法字段/无权限分支)。本地无 INTEGRATION_DATABASE_URL 时按该测试家族惯例 skip——则以 schema 单测(`owner_id` 在 PATCH schema 中)最小闭环。

- [ ] **Step 4: 实现**

episodes_router `update_episode`:body schema 加 `owner_id: Optional[str] = None`(sentinel 区分「未传」与「置空」,照 body 里 `model_fields_set`);当 `'owner_id' in fields_set` 时:`role = await resolve_effective_role(project_id, user_id)`,非 `'manager'` → `raise HTTPException(403, detail={"code": "episode_owner_forbidden"})`;否则写列。响应带回 `owner_id`(str)。

- [ ] **Step 5: 跑测试确认通过 + Commit**

Run: `cd backend && uv run pytest tests/test_episode_owner.py tests/test_episodes_scenes_authz_wiring.py -v`

```bash
git add supabase/migrations/ backend/app/models/scripts.py backend/app/api/episodes_router.py backend/tests/test_episode_owner.py
git commit -m "feat(episodes): 集负责人 owner_id — migration + PATCH(仅项目负责人) + 权限测试"
```

---

### Task 7: 节点字段与剧集编排权限收紧

**Files:**
- Modify: `backend/app/api/projects_router.py`(`patch_workflow_node` L721 起;节点结构增删端点 L824 DELETE 与对应 POST;集增删/排序在 episodes_router 对应端点)
- Test: `backend/tests/test_workflow_node_config_authz.py`(新)

**Interfaces:**
- Consumes: Task 6 的 `episodes.owner_id`;`resolve_effective_role`。
- Produces: 权限谓词 `async def can_edit_node_config(project_id, node, user_id) -> bool`(项目负责人 manager ∨ `episodes.owner_id == user_id`),放 `backend/app/services/workflow/node_authz.py`(新小文件,两个函数,供 router 复用);403 错误码:节点字段 `node_config_forbidden`,编排 `arrangement_forbidden`。

- [ ] **Step 1: 写失败测试**

```python
async def test_episode_owner_can_patch_node_owner(client_as_epowner, node):
    r = await client_as_epowner.patch(f"/api/v1/projects/{PID}/workflow/nodes/{node['id']}",
                                      json={"owner_id": SOME_UUID})
    assert r.status_code == 200

async def test_plain_member_cannot_patch_node_owner(client_as_member, node):
    r = await client_as_member.patch(..., json={"owner_id": SOME_UUID})
    assert r.status_code == 403 and r.json()["detail"]["code"] == "node_config_forbidden"

async def test_plain_member_cannot_delete_node(client_as_member, node):
    r = await client_as_member.delete(f"/api/v1/projects/{PID}/workflow/nodes/{node['id']}")
    assert r.status_code == 403 and r.json()["detail"]["code"] == "arrangement_forbidden"

async def test_advance_gate_unchanged(client_as_member, node):
    # 完成阶段走既有 advance 角色闸,不受本收紧影响(members 若原本可推进仍可)
    ...  # 断言与现 test 套件中 advance 权限行为一致,防误伤
```

- [ ] **Step 2: 跑测试确认失败**,Run: `cd backend && uv run pytest tests/test_workflow_node_config_authz.py -v`

- [ ] **Step 3: 实现**

`node_authz.py`:

```python
async def can_edit_node_config(project_id: str, episode_id: Optional[str], user_id: str) -> bool:
    role = await resolve_effective_role(project_id, user_id)
    if role == "manager":
        return True
    if episode_id is None:
        return False
    ep = await get_episode(episode_id)          # 既有 repo getter
    return bool(ep and str(ep.get("owner_id") or "") == str(user_id))

async def require_arrangement_role(project_id: str, user_id: str) -> None:
    if await resolve_effective_role(project_id, user_id) != "manager":
        raise HTTPException(403, detail={"code": "arrangement_forbidden"})
```

`patch_workflow_node`:当 body 涉及 config 字段(owner/members/schedule/brief;字段名按该端点现 schema)时先过 `can_edit_node_config(project_id, node["episode_id"], user_id)`,否则 403 `node_config_forbidden`;不涉及时行为不变。节点 POST/DELETE、集 create/delete/reorder 端点开头加 `require_arrangement_role`。

- [ ] **Step 4: 跑测试**,Run: `cd backend && uv run pytest tests/test_workflow_node_config_authz.py tests/ -k "workflow and authz" -v`

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/workflow/node_authz.py backend/app/api/projects_router.py backend/app/api/episodes_router.py backend/tests/test_workflow_node_config_authz.py
git commit -m "feat(workflow): 权限收紧 — 节点配置=负责人∨集负责人,剧集编排=仅项目负责人,403 类型化"
```

---

### Task 8: DateRangePopover(日历区间组件)

**Files:**
- Create: `frontend/components/common/DateRangePopover.tsx`
- Test: `frontend/components/common/DateRangePopover.test.tsx`

**Interfaces:**
- Produces:

```tsx
export interface DateRangePopoverProps {
  anchorEl: HTMLElement | null;          // null = 关闭
  start: string | null;                  // 'YYYY-MM-DD'
  end: string | null;
  onChange: (start: string | null, end: string | null) => void;  // 清除 = (null,null)
  onClose: () => void;
}
export function DateRangePopover(props: DateRangePopoverProps): React.ReactPortal | null;
```

行为规范(方案稿 v4.2 实测结论,逐条为验收):`createPortal(document.body)` + `position:fixed`;打开时按 `anchorEl.getBoundingClientRect()` 定位于下方 6px,底部空间不足按**实测 offsetHeight** 翻转到上方 6px;月视图 + ‹ › 翻月;第一次点击设 start、第二次设 end(早于 start 则交换),跨月连续;起止两格回显;外点/Escape/滚动 `onClose`;`prefers-reduced-motion` 下无过渡。

- [ ] **Step 1: 写失败测试**

```tsx
it('renders into document.body via portal when anchored', () => {
  const anchor = document.createElement('button'); document.body.appendChild(anchor);
  render(<DateRangePopover anchorEl={anchor} start="2026-07-15" end="2026-08-03"
    onChange={vi.fn()} onClose={vi.fn()} />);
  const pop = screen.getByTestId('date-range-popover');
  expect(pop.parentElement).toBe(document.body);
  expect(pop.style.position).toBe('fixed');
});
it('two clicks select a cross-month range in order', () => {
  const onChange = vi.fn();
  // 初始显示 2026-07(由 start 推导);点 15 → start;翻月点 3 → end
  render(<DateRangePopover anchorEl={anchor} start={null} end={null} onChange={onChange} onClose={vi.fn()} />);
  fireEvent.click(screen.getByRole('button', { name: '2026-07-15' }));
  fireEvent.click(screen.getByLabelText('Next Month'));
  fireEvent.click(screen.getByRole('button', { name: '2026-08-03' }));
  expect(onChange).toHaveBeenLastCalledWith('2026-07-15', '2026-08-03');
});
it('escape and outside click both close', () => { /* onClose 两次断言 */ });
```

(日期格 `aria-label` 用 ISO 串,测试与无障碍同时受益。)

- [ ] **Step 2: 跑测试确认失败**,Run: `cd frontend && npx vitest run components/common/DateRangePopover.test.tsx`

- [ ] **Step 3: 实现**(要点)

```tsx
const place = useCallback(() => {
  if (!anchorEl || !ref.current) return;
  const r = anchorEl.getBoundingClientRect();
  const h = ref.current.offsetHeight;
  let top = r.bottom + 6;
  if (top + h > window.innerHeight - 8) top = Math.max(8, r.top - 6 - h);
  const left = Math.max(8, Math.min(r.left, window.innerWidth - 262 - 12));
  ref.current.style.top = `${top}px`;
  ref.current.style.left = `${left}px`;
}, [anchorEl]);
useLayoutEffect(place, [place, month]);
```

月网格纯函数 `monthGrid(year, month)` 单测友好;样式用 island/line/agent token(选中端点 `bg-agent text-white`,区间 `bg-agent-soft`)。

- [ ] **Step 4: 跑测试**,Expected: PASS。

- [ ] **Step 5: Commit**

```bash
git add frontend/components/common/DateRangePopover.tsx frontend/components/common/DateRangePopover.test.tsx
git commit -m "feat(common): DateRangePopover 日历区间组件 — portal/实测高度翻转/跨月"
```

---

### Task 9: 节点卡行内编辑(角色门控)

**Files:**
- Modify: `frontend/components/workspace/EpisodeNodeCard.tsx`(编辑态)
- Modify: `frontend/components/workspace/ProjectWorkspace.tsx`(算 `canEditConfig` 并传入;PATCH 调用)
- Modify: `frontend/services/projectService.ts`(如无现成 node PATCH 方法则补)
- Test: `frontend/components/workspace/EpisodeNodeCard.test.tsx`(追加)

**Interfaces:**
- Consumes: Task 5 组件、Task 8 `DateRangePopover`、`OwnerPicker`(现组件,props `people/value/onChange` 按其源码)、后端 Task 7 的 PATCH 与 403 码。
- Produces: `EpisodeNodeCardProps` 增 `people: PersonOption[]`、`onPatchNode: (nodeId: string, patch: {owner_id?: string|null; schedule_start?: string|null; schedule_end?: string|null}) => Promise<void>`;`canEditConfig` 计算 = `me.id === project.owner_id || me.id === episode.owner_id`(episodes/progress 响应需带 `owner_id`,Task 6 已入列——前端 types.ts `EpisodeProgress` 加 `owner_id?: string | null`)。

- [ ] **Step 1: 写失败测试**(追加)

```tsx
it('editable owner renders as button and patches via onPatchNode', async () => {
  const onPatchNode = vi.fn().mockResolvedValue(undefined);
  render(<EpisodeNodeCard node={nodeWithOwner} canEditConfig people={PEOPLE} onPatchNode={onPatchNode} {...rest} />);
  fireEvent.click(screen.getByTestId('node-card-owner'));       // 打开 OwnerPicker
  fireEvent.click(await screen.findByText('Alice'));
  expect(onPatchNode).toHaveBeenCalledWith('n2', { owner_id: 'alice-uuid' });
});
it('empty editable fields render as plus pills', () => {
  render(<EpisodeNodeCard node={emptyNode} canEditConfig {...rest} />);
  expect(screen.getByTestId('node-card-owner').textContent).toMatch(/Assign/);
  expect(screen.getByTestId('node-card-schedule').textContent).toMatch(/Set Schedule/);
});
it('readonly viewer sees plain text even when fields empty', () => {
  render(<EpisodeNodeCard node={emptyNode} canEditConfig={false} {...rest} />);
  expect(screen.getByTestId('node-card-owner').tagName).toBe('SPAN');
});
it('403 from patch shows toast and reverts optimistic value', async () => {
  const onPatchNode = vi.fn().mockRejectedValue({ code: 'node_config_forbidden' });
  /* 断言字段值回退 + toast(mock ToastContext) */
});
```

- [ ] **Step 2: 跑测试确认失败**。

- [ ] **Step 3: 实现**

编辑态渲染(方案稿 v4 定稿):有值 = 安静按钮(平时如文本,hover 出 border-line 背景 island-2 + ⌄);空值 = 虚线圆角 `+ Assign Owner` / `+ Set Schedule`(hover agent-soft)。负责人点开 `OwnerPicker`,排期点开 `DateRangePopover`(anchor = 按钮元素);`onPatchNode` 乐观更新 + 403 回退 toast(`t('projects.nodeCard.forbidden', 'Only the project or episode owner can edit this')`)。`projectService` 若缺则加:

```ts
async patchWorkflowNode(projectId: string, nodeId: string, patch: Record<string, unknown>) {
  return apiPatch(`/projects/${projectId}/workflow/nodes/${nodeId}`, patch);
}
```

- [ ] **Step 4: 跑测试**,Run: `cd frontend && npx vitest run components/workspace/EpisodeNodeCard.test.tsx`

- [ ] **Step 5: Commit**

```bash
git add -A frontend/components/workspace/ frontend/services/projectService.ts frontend/types.ts
git commit -m "feat(overview): 节点卡角色门控行内编辑 — OwnerPicker/日历接线,空值 + 引导,403 回退"
```

---

### Task 10: 设置·节点配置区 + 编辑器流程胶囊 + 收尾

**Files:**
- Create: `frontend/components/workspace/WorkspaceNodeSettings.tsx`
- Modify: ProjectWorkspace 设置渲染块(~L855 `activeModule === 'settings'`)加「节点配置」区与 `?tab=nodes&ep=&node=` 深链定位
- Modify: `frontend/components/workspace/WorkspaceTopBar.tsx`(studio 模式流程胶囊)
- Test: `frontend/components/workspace/WorkspaceNodeSettings.test.tsx`、`WorkspaceTopBar.test.tsx`(追加)

**Interfaces:**
- Consumes: `WorkflowStrip`、`OwnerPicker`、`DateRangePopover`、Task 9 的 `patchWorkflowNode`、Task 6 的 episodes PATCH(集负责人指派)。
- Produces:

```tsx
export interface WorkspaceNodeSettingsProps {
  projectId: string;
  episodes: EpisodeProgress[];
  workflow: ProjectWorkflow | null;
  initialEpisodeId: string | null;   // URL ep=
  initialNodeId: string | null;      // URL node=
  canEditFor: (episodeId: string) => boolean;   // 权限谓词(项目负责人∨该集负责人)
  canAssignEpisodeOwner: boolean;               // 仅项目负责人
  people: PersonOption[];
}
```

- [ ] **Step 1: 写失败测试**

```tsx
// WorkspaceNodeSettings.test.tsx
it('renders node strip for the selected episode; skipped nodes disabled', () => { ... });
it('clicking a node shows only that node form (owner/members/schedule/deliverable/brief)', () => { ... });
it('episode owner row visible and editable only when canAssignEpisodeOwner', () => { ... });
it('deep-link initialNodeId preselects that node', () => { ... });
// WorkspaceTopBar.test.tsx 追加
it('studio mode shows flow pill with node name and Complete Stage action', () => { ... });
it('no pill when workflow missing or cursor not on this episode', () => { ... });
```

(每条按 Task 4/5 的 fixture 风格写实断言;胶囊点击断言 `onRequestAdvance('forward')` 被调。)

- [ ] **Step 2: 跑测试确认失败**。

- [ ] **Step 3: 实现**

WorkspaceNodeSettings:顶行 `集负责人`(OwnerPicker,`canAssignEpisodeOwner` 才可点,PATCH episodes.owner_id)+ 「EP ⌄」切集(切换只改本组件 state 并回写 URL ep)+ WorkflowStrip(skip 节点 `disabled`)+ 选中节点表单(负责人/成员/排期/交付物/背景说明,复用 OwnerPicker/DateRangePopover/BriefField 既有组件;无权 = 全只读)。
WorkspaceTopBar 胶囊(studio 模式已有 `workflow`/`onRequestAdvance` props):

```tsx
{slate && cursorNode && (
  <span className="ml-auto inline-flex items-center gap-2 rounded-full border border-agent-line bg-agent-soft py-1 pl-3 pr-1 text-[11.5px] font-bold text-agent">
    <span className="h-1.5 w-1.5 rounded-full bg-warn" />
    {cursorNode.name} · {t(`projects.workspace.nodeStatus.${cursorNode.status}`)}
    <button type="button" onClick={() => onRequestAdvance('forward')}
      className="rounded-full bg-agent px-2.5 py-1 text-[11px] text-white">
      {t('projects.workflow.completeStage', 'Complete Stage')}
    </button>
  </span>
)}
```

- [ ] **Step 4: 跑全量**

Run: `cd frontend && npx vitest run && npx tsc --noEmit | wc -l`(错误数 ≤ 基线)
Run: `cd backend && uv run pytest -q`(全绿/既有 skip)

- [ ] **Step 5: Commit + PR**

```bash
git add -A
git commit -m "feat(workspace): 设置节点配置区 + 集负责人指派 + 编辑器流程胶囊"
```

> **PR-3 收口** = Task 6–9,**PR-4 收口** = Task 10(或按体量并入 PR-3);e2e 全链用例(总览展开→点节点→进分镜→镜头进画布;设置改负责人→总览卡即时反映)随最后一个 PR 补,`/ship` 走发布。

---

## Self-Review 记录

- **Spec 覆盖**:§2 手风琴=T4;§3 节点卡=T5+T9;§4 分镜页=T2+T3;§5 权限=T6+T7;§6 胶囊=T10;§7 设置=T10;§8 寻址=T1(+T2/T4 写参);§10 测试分布在各 task;§11 范围外未触碰。无缺口。
- **占位符**:Task 10 Step 1 的测试以意图列出、Step 3 给了实现骨架——执行者需按 T4/T5 的 fixture 风格补全断言体;其余 task 均有可运行代码。
- **类型一致性**:`readWorkspaceParams`(T1→T2/T4)、`EpisodeNodeCardProps`(T5→T9)、`DateRangePopoverProps`(T8→T9/T10)、`patchWorkflowNode`(T9→T10)、错误码三枚(`episode_owner_forbidden`/`node_config_forbidden`/`arrangement_forbidden`,T6/T7→T9)已对齐。
