# Inspiration Notes P3 — 热点融合 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把老 TopicInspiration 热点能力融进 P2 的 InspirationPage:顶栏 Notes⇄Hotspots tab、Notes tab 侧栏 Hotspots Top3、Hotspots tab(排名列表+详情面板)、存灵感闭环(热点→带 ref_hotspot 的笔记)、热点 category 标签、全局 Parse URL 按钮。全部在 `VITE_FEATURE_INSPIRATION_NOTES` flag 后。

**Architecture:** 复用现有热点组件(HotspotCard/hotspotRanking/topicService)与 P2 组件(Composer 已有 prefill prop/NoteCard 已渲染 refcard);新增一个 `useHotspots` 共享 hook(从 TopicInspirationPage 提炼加载/乐观状态逻辑)、一个内联 `HotspotDetail`(Hotspots tab 右栏,不走 portal)、两个容器(HotspotsWorkspace/HotspotsSidePanel)、一个纯映射 `hotspotToRef`。InspirationPage 加 tab 状态编排三者。**纯前端,零后端**——热点 category 标签在客户端从已加载列表聚合(比 spec 的 `/topics/hotspot-tags` 端点更简单,已加载的当日热点是有界集合)。

**Tech Stack:** React 19 + TS + Tailwind(ink/island token)+ topicService(现有)+ vitest/@testing-library。

**Spec:** `docs/superpowers/specs/2026-07-07-inspiration-notes-design.md` §2(交互契约 6/7/8、屏 2);mockup artifact c897f3b8 v7。

## Global Constraints

- 工作目录:本 worktree(分支 `feature/inspiration-p3-hotspots`);全部命令在 `frontend/` 下
- 纯前端,不碰后端;不新增后端端点(热点 category 标签客户端聚合)
- UI 文本英文走 i18n:`t('inspiration.<key>', '<English fallback>')`,`inspiration` 命名空间已存在(P2 建),新增 key 补 en.json+zh.json 同步;**零 emoji 图标**(lucide);**禁 zinc 类**(用 ink/island/content/line/indigo token)
- 所有 id 是 string;错误 toast 不静默(`catch (err) { addToast(...) }` 或 `console.error`)
- 复用而非重建:`components/TopicInspiration/HotspotCard.tsx`、`hotspotRanking.ts`(topHotspots/blendedScore)、`services/topicService.ts`(getHotspots/getHotspot/getHotspotDates/setHotspotState)、P2 的 `Composer`(已有 `prefill?: {content, refHotspot}` prop)、`NoteCard`(已渲染 ref_hotspot refcard)
- 存 hotspot→note 映射固定为:`{ hotspot_id: h.id, title: h.title, source: h.source_label ?? undefined, heat: h.heat ?? undefined, url: h.origin_url || h.url || undefined, captured_at: h.captured_at ?? undefined }`(前端 RefHotspot 类型即契约,后端 ref_hotspot 是自由 JSONB)
- Composer prefill 触发靠 **key 重挂载**(prefill 只 useState 初始化读一次;key 变→新会话,不覆盖用户已输入)
- 每 task 结束:`npx vitest run <本任务测试>` 过 + commit;收口 task 跑 `npm run lint`(rules-of-hooks 阻塞)/ `npm run build` / 全量 vitest
- flag off 时 InspirationPage 不渲染(TopicInspirationPage 短路),P3 改动对旧页零影响

---

### Task 1: hotspotToRef 纯映射

**Files:**
- Create: `frontend/components/Inspiration/hotspotToRef.ts`
- Test: `frontend/components/Inspiration/hotspotToRef.test.ts`

**Interfaces:**
- Consumes: `Hotspot`(services/topicService)、`RefHotspot`(services/inspirationService)
- Produces: `hotspotToRef(h: Hotspot): RefHotspot` — 存灵感闭环的快照映射

- [ ] **Step 1: 写失败测试**

```ts
import { describe, expect, it } from 'vitest';
import { hotspotToRef } from './hotspotToRef';
import type { Hotspot } from '../../services/topicService';

const base = (over: Partial<Hotspot> = {}): Hotspot => ({
  id: '42',
  title: 'Silent vlog cooking passes 2.1B',
  tags: [],
  source_label: 'DOUYIN',
  heat: 98.4,
  origin_url: 'https://douyin.com/x',
  url: 'https://fallback',
  captured_at: '2026-07-07T00:00:00+00:00',
  ...over,
});

describe('hotspotToRef', () => {
  it('maps the core fields into a RefHotspot snapshot', () => {
    expect(hotspotToRef(base())).toEqual({
      hotspot_id: '42',
      title: 'Silent vlog cooking passes 2.1B',
      source: 'DOUYIN',
      heat: 98.4,
      url: 'https://douyin.com/x',
      captured_at: '2026-07-07T00:00:00+00:00',
    });
  });

  it('prefers origin_url, falls back to url', () => {
    expect(hotspotToRef(base({ origin_url: null })).url).toBe('https://fallback');
  });

  it('drops null/undefined optionals rather than emitting nulls', () => {
    const ref = hotspotToRef(base({ source_label: null, heat: null, origin_url: null, url: null, captured_at: null }));
    expect(ref).toEqual({ hotspot_id: '42', title: 'Silent vlog cooking passes 2.1B' });
    expect('source' in ref).toBe(false);
    expect('url' in ref).toBe(false);
  });
});
```

- [ ] **Step 2: 跑测试确认失败**

Run: `npx vitest run components/Inspiration/hotspotToRef.test.ts`
Expected: FAIL — 模块不存在

- [ ] **Step 3: 实现**

```ts
// frontend/components/Inspiration/hotspotToRef.ts
// Snapshot a hotspot into a note's ref_hotspot (save-as-note loop, spec §2 #6).
// The frontend RefHotspot shape IS the contract — the backend column is free
// JSONB. Optionals are omitted when absent (never emitted as null).
import type { RefHotspot } from '../../services/inspirationService';
import type { Hotspot } from '../../services/topicService';

export function hotspotToRef(h: Hotspot): RefHotspot {
  const ref: RefHotspot = { hotspot_id: h.id, title: h.title };
  const source = h.source_label ?? undefined;
  const url = h.origin_url || h.url || undefined;
  if (source) ref.source = source;
  if (typeof h.heat === 'number') ref.heat = h.heat;
  if (url) ref.url = url;
  if (h.captured_at) ref.captured_at = h.captured_at;
  return ref;
}
```

- [ ] **Step 4: 跑测试确认全过**

Run: `npx vitest run components/Inspiration/hotspotToRef.test.ts`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add frontend/components/Inspiration/hotspotToRef.ts frontend/components/Inspiration/hotspotToRef.test.ts
git commit -m "feat(inspiration): hotspot→ref_hotspot snapshot mapper"
```

---

### Task 2: useHotspots 共享加载 hook

**Files:**
- Create: `frontend/components/Inspiration/useHotspots.ts`
- Test: `frontend/components/Inspiration/useHotspots.test.ts`

**Interfaces:**
- Consumes: `getHotspots`/`setHotspotState`(topicService)、`Hotspot`/`HotspotStatePatch`
- Produces:
```ts
export function useHotspots(opts: { day?: string; enabled: boolean }): {
  hotspots: Hotspot[];
  loading: boolean;
  error: string | null;
  reload: () => void;
  applyState: (h: Hotspot, patch: HotspotStatePatch) => Promise<void>;  // 乐观 setHotspotState,失败 reload 回滚
}
```
`enabled=false` 时不拉取(Notes tab 未展开 hotspots 时省请求);`day` 变化重拉(全页共享日期驱动)。

- [ ] **Step 1: 写失败测试**

```ts
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { renderHook, waitFor, act } from '@testing-library/react';

const getHotspots = vi.fn();
const setHotspotState = vi.fn();
vi.mock('../../services/topicService', () => ({
  getHotspots: (...a: unknown[]) => getHotspots(...a),
  setHotspotState: (...a: unknown[]) => setHotspotState(...a),
}));

import { useHotspots } from './useHotspots';

const HS = (id: string, over = {}) => ({ id, title: `t${id}`, tags: [], ...over });

describe('useHotspots', () => {
  beforeEach(() => {
    getHotspots.mockReset();
    setHotspotState.mockReset();
    getHotspots.mockResolvedValue([HS('1'), HS('2')]);
  });

  it('does not fetch when disabled', () => {
    renderHook(() => useHotspots({ enabled: false }));
    expect(getHotspots).not.toHaveBeenCalled();
  });

  it('fetches for the given day when enabled', async () => {
    const { result } = renderHook(() => useHotspots({ enabled: true, day: '2026-07-07' }));
    await waitFor(() => expect(result.current.hotspots).toHaveLength(2));
    expect(getHotspots).toHaveBeenCalledWith('2026-07-07', undefined, undefined, 'all', undefined);
  });

  it('applyState optimistically patches then persists', async () => {
    setHotspotState.mockResolvedValue({});
    const { result } = renderHook(() => useHotspots({ enabled: true }));
    await waitFor(() => expect(result.current.hotspots).toHaveLength(2));
    await act(async () => {
      await result.current.applyState(HS('1'), { is_saved: true });
    });
    expect(setHotspotState).toHaveBeenCalledWith('1', { is_saved: true });
    expect(result.current.hotspots.find((h) => h.id === '1')?.is_saved).toBe(true);
  });

  it('applyState reloads to roll back on failure', async () => {
    setHotspotState.mockRejectedValue(new Error('boom'));
    const { result } = renderHook(() => useHotspots({ enabled: true }));
    await waitFor(() => expect(result.current.hotspots).toHaveLength(2));
    getHotspots.mockClear();
    await act(async () => {
      await result.current.applyState(HS('1'), { is_saved: true }).catch(() => {});
    });
    await waitFor(() => expect(getHotspots).toHaveBeenCalled());
  });

  it('surfaces load errors without throwing', async () => {
    getHotspots.mockRejectedValue(new Error('down'));
    const { result } = renderHook(() => useHotspots({ enabled: true }));
    await waitFor(() => expect(result.current.error).toBe('down'));
    expect(result.current.hotspots).toEqual([]);
  });
});
```

- [ ] **Step 2: 跑测试确认失败**

Run: `npx vitest run components/Inspiration/useHotspots.test.ts`
Expected: FAIL

- [ ] **Step 3: 实现**

```ts
// frontend/components/Inspiration/useHotspots.ts
// Shared hotspot loading + optimistic state engine, extracted from the legacy
// TopicInspirationPage so both the Hotspots tab and the Notes-tab Top-3 panel
// drive off one source. Day comes from the page-wide selected date.
import { useCallback, useEffect, useState } from 'react';
import {
  getHotspots,
  setHotspotState,
  type Hotspot,
  type HotspotStatePatch,
} from '../../services/topicService';

export function useHotspots(opts: { day?: string; enabled: boolean }) {
  const { day, enabled } = opts;
  const [hotspots, setHotspots] = useState<Hotspot[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);

  const reload = useCallback(() => setReloadKey((k) => k + 1), []);

  useEffect(() => {
    if (!enabled) return;
    let alive = true;
    setLoading(true);
    setError(null);
    (async () => {
      try {
        const rows = await getHotspots(day, undefined, undefined, 'all', undefined);
        if (alive) setHotspots(rows);
      } catch (err) {
        if (alive) {
          setError((err as Error).message);
          setHotspots([]);
        }
      } finally {
        if (alive) setLoading(false);
      }
    })();
    return () => {
      alive = false;
    };
  }, [day, enabled, reloadKey]);

  const applyState = useCallback(
    async (h: Hotspot, patch: HotspotStatePatch) => {
      setHotspots((prev) => prev.map((x) => (x.id === h.id ? { ...x, ...patch } : x)));
      try {
        await setHotspotState(h.id, patch);
      } catch (err) {
        reload(); // revert via refetch
        throw err;
      }
    },
    [reload],
  );

  return { hotspots, loading, error, reload, applyState };
}
```

- [ ] **Step 4: 跑测试确认全过**

Run: `npx vitest run components/Inspiration/useHotspots.test.ts`
Expected: 5 passed。注意 `renderHook`/`act` 来自 `@testing-library/react`(已装)。

- [ ] **Step 5: Commit**

```bash
git add frontend/components/Inspiration/useHotspots.ts frontend/components/Inspiration/useHotspots.test.ts
git commit -m "feat(inspiration): useHotspots — shared load + optimistic state engine"
```

---

### Task 3: FloatingParse 受控 open(全局 Parse 按钮)

**Files:**
- Modify: `frontend/components/TopicInspiration/FloatingParse.tsx`(加可选受控 props,向后兼容)
- Test: `frontend/components/TopicInspiration/FloatingParse.controlled.test.tsx`

**Interfaces:**
- Produces: FloatingParse 新增可选 props `{ open?: boolean; onOpenChange?: (open: boolean) => void }`。给了 `open` 就受控(open=true→phase 至少为 input;open=false→collapsed);不给则维持现有自持行为(向后兼容,老 TopicInspirationPage 无参用法不变)。

- [ ] **Step 1: 写失败测试**

```tsx
import { describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (_k: string, f?: string) => f ?? _k }),
}));
vi.mock('../Toast', () => ({ useToast: () => ({ addToast: vi.fn() }) }));
vi.mock('../../services/topicService', () => ({ getTags: vi.fn().mockResolvedValue([]) }));
vi.mock('../../services/parserService', () => ({
  parseAndDownload: vi.fn(),
  getAuthHeaders: vi.fn().mockResolvedValue({}),
}));

import { FloatingParse } from './FloatingParse';

describe('FloatingParse controlled open', () => {
  it('renders the input panel when open=true is passed', () => {
    render(<FloatingParse open onOpenChange={vi.fn()} />);
    // 展开态含输入框 placeholder(与 collapsed 的圆按钮区分)
    expect(screen.getByPlaceholderText(/paste/i)).toBeTruthy();
  });

  it('stays collapsed when open=false', () => {
    render(<FloatingParse open={false} onOpenChange={vi.fn()} />);
    expect(screen.queryByPlaceholderText(/paste/i)).toBeNull();
  });
});
```

- [ ] **Step 2: 跑测试确认失败**

Run: `npx vitest run components/TopicInspiration/FloatingParse.controlled.test.tsx`
Expected: FAIL(现在无 open prop,始终 collapsed)。**注意**:先 Read FloatingParse.tsx 确认输入框真实 placeholder 文案(测试里 `/paste/i` 正则要匹配它;若实际是别的词,改测试正则匹配真实文案——这是唯一允许调测试的点,因为占位文案是既有事实)。同时确认它 import 的 service 名(getTags/parseAndDownload 等),mock 要对齐真实 import,否则 render 就崩。

- [ ] **Step 3: 实现**(改造为受控/非受控双模式)

在 `FloatingParse.tsx` 组件签名加可选 props,并让 `phase` 受 `open` 驱动:
```tsx
export const FloatingParse: React.FC<{
  open?: boolean;
  onOpenChange?: (open: boolean) => void;
}> = ({ open, onOpenChange }) => {
  // ...existing state...
  // 受控:open 变化时同步 phase(仅当传了 open)
  useEffect(() => {
    if (open === undefined) return;
    setPhase(open ? (p) => (p === 'collapsed' ? 'input' : p) : 'collapsed');
  }, [open]);
```
`setPhase(open ? (p) => ...)` 写法:setState 的函数式更新拿到前值 p,open=true 且当前 collapsed→切 input,已经是 input/result 则不动;open=false→collapsed。
所有原来 `setPhase('collapsed')`(用户点关闭/完成)的地方,追加 `onOpenChange?.(false)` 通知父级;`setPhase('input')`(用户点圆按钮展开)处追加 `onOpenChange?.(true)`。这样受控父级状态与内部 phase 双向同步。**不传 open/onOpenChange 时全部退化为原行为**(useEffect early-return,onOpenChange?. 是 no-op)。

- [ ] **Step 4: 跑测试确认全过 + 回归**

Run: `npx vitest run components/TopicInspiration/FloatingParse.controlled.test.tsx` → 2 passed
Run: `npx vitest run components/TopicInspiration/` → 现有 TopicInspiration 测试无回归(若有 FloatingParse 既有测试,确认仍绿)

- [ ] **Step 5: Commit**

```bash
git add frontend/components/TopicInspiration/FloatingParse.tsx frontend/components/TopicInspiration/FloatingParse.controlled.test.tsx
git commit -m "feat(inspiration): FloatingParse optional controlled-open for global Parse button"
```

---

### Task 4: HotspotDetail 内联详情面板

**Files:**
- Create: `frontend/components/Inspiration/HotspotDetail.tsx`
- Test: `frontend/components/Inspiration/HotspotDetail.test.tsx`

**Interfaces:**
- Consumes: `getHotspot`(topicService,补全文/摘要)、`Hotspot` 类型、Task 1 无关
- Produces:
```tsx
<HotspotDetail hotspot={Hotspot | null}
  onSaveAsNote={(h: Hotspot) => void} onParse={(h: Hotspot) => void}
  onNotInterested={(h: Hotspot) => void} />
```
内联(非 portal)右栏:来源徽标 + category 标签 chip + 标题 + 热度/来源数 + 摘要(挂载时 getHotspot 补 ai_summary/summary)+ 原链;底部动作条 Save as note / Parse / Not interested / Open source。hotspot=null 显示占位空态。

- [ ] **Step 1: 写失败测试**

```tsx
import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';

const getHotspot = vi.fn();
vi.mock('../../services/topicService', () => ({
  getHotspot: (...a: unknown[]) => getHotspot(...a),
}));
vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (_k: string, f: string) => f }),
}));

import { HotspotDetail } from './HotspotDetail';

const HS = (over = {}) => ({
  id: '42', title: 'Silent vlog 2.1B', tags: [], source_label: 'DOUYIN',
  category: 'food', heat: 98.4, origin_url: 'https://x', summary: 'short', ...over,
});

describe('HotspotDetail', () => {
  it('shows empty state when no hotspot selected', () => {
    render(<HotspotDetail hotspot={null} onSaveAsNote={vi.fn()} onParse={vi.fn()} onNotInterested={vi.fn()} />);
    expect(screen.getByText(/select a hotspot/i)).toBeTruthy();
  });

  it('renders title, source, category tag, heat', async () => {
    getHotspot.mockResolvedValue(HS({ ai_summary: 'full summary' }));
    render(<HotspotDetail hotspot={HS()} onSaveAsNote={vi.fn()} onParse={vi.fn()} onNotInterested={vi.fn()} />);
    expect(screen.getByText('Silent vlog 2.1B')).toBeTruthy();
    expect(screen.getByText('DOUYIN')).toBeTruthy();
    expect(screen.getByText('#food')).toBeTruthy();
    expect(screen.getByText(/98.4/)).toBeTruthy();
    await waitFor(() => expect(screen.getByText('full summary')).toBeTruthy());
  });

  it('save-as-note action fires with the hotspot', () => {
    getHotspot.mockResolvedValue(HS());
    const onSaveAsNote = vi.fn();
    render(<HotspotDetail hotspot={HS()} onSaveAsNote={onSaveAsNote} onParse={vi.fn()} onNotInterested={vi.fn()} />);
    fireEvent.click(screen.getByText('Save as note'));
    expect(onSaveAsNote).toHaveBeenCalledWith(expect.objectContaining({ id: '42' }));
  });
});
```

- [ ] **Step 2: 跑测试确认失败**

Run: `npx vitest run components/Inspiration/HotspotDetail.test.tsx`
Expected: FAIL

- [ ] **Step 3: 实现**

```tsx
// frontend/components/Inspiration/HotspotDetail.tsx
// Inline detail pane for the Hotspots tab (spec §2 screen-2 right pane).
// Content mirrors the legacy HotspotInfoPanel but rendered in-flow (no portal),
// since the tab wants a fixed right column, not a floating island.
import React, { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { ExternalLink, Flame, Plus } from 'lucide-react';
import { getHotspot, type Hotspot } from '../../services/topicService';

interface Props {
  hotspot: Hotspot | null;
  onSaveAsNote: (h: Hotspot) => void;
  onParse: (h: Hotspot) => void;
  onNotInterested: (h: Hotspot) => void;
}

export const HotspotDetail: React.FC<Props> = ({ hotspot, onSaveAsNote, onParse, onNotInterested }) => {
  const { t } = useTranslation();
  const [full, setFull] = useState<Hotspot | null>(null);

  useEffect(() => {
    if (!hotspot) {
      setFull(null);
      return;
    }
    setFull(hotspot);
    let alive = true;
    getHotspot(hotspot.id)
      .then((h) => alive && setFull(h))
      .catch((err) => console.error('hotspot detail load failed', err));
    return () => {
      alive = false;
    };
  }, [hotspot]);

  if (!hotspot) {
    return (
      <div className="flex h-full items-center justify-center rounded-xl bg-island px-4 py-10 text-center text-sm text-content-3">
        {t('inspiration.selectHotspot', 'Select a hotspot to see details')}
      </div>
    );
  }

  const h = full ?? hotspot;
  const summary = h.ai_summary || h.summary || '';
  const url = h.origin_url || h.url || undefined;

  return (
    <div className="flex h-full flex-col rounded-xl bg-island">
      <div className="flex-1 overflow-y-auto p-4">
        <div className="flex flex-wrap items-center gap-1.5">
          {h.source_label && (
            <span className="rounded bg-island-2 px-1.5 py-0.5 text-[10px] font-bold text-content-2">{h.source_label}</span>
          )}
          {h.category && (
            <span className="rounded bg-indigo-500/15 px-1.5 py-0.5 text-[11px] text-indigo-300">#{h.category}</span>
          )}
        </div>
        <h4 className="mt-2 text-[15px] font-semibold leading-snug text-content">{h.title}</h4>
        <div className="mt-2 flex flex-wrap items-center gap-3 text-[11px] text-content-3 tabular-nums">
          {typeof h.heat === 'number' && (
            <span className="inline-flex items-center gap-1 text-amber-400">
              <Flame size={11} /> {h.heat}
            </span>
          )}
          {typeof h.source_count === 'number' && h.source_count > 1 && (
            <span>{t('inspiration.sourceCount', '{{count}} sources', { count: h.source_count })}</span>
          )}
        </div>
        {summary && (
          <>
            <div className="mt-4 text-[10.5px] font-semibold uppercase tracking-wider text-content-4">
              {t('inspiration.summary', 'Summary')}
            </div>
            <p className="mt-1.5 text-[12.5px] leading-relaxed text-content-2">{summary}</p>
          </>
        )}
      </div>
      <div className="flex flex-wrap items-center gap-2 border-t border-line p-3">
        <button
          onClick={() => onSaveAsNote(h)}
          className="inline-flex items-center gap-1.5 rounded-lg bg-indigo-500 px-3 py-1.5 text-xs font-semibold text-white"
        >
          <Plus size={13} /> {t('inspiration.saveAsNote', 'Save as note')}
        </button>
        <button onClick={() => onParse(h)} className="rounded-lg bg-island-2 px-3 py-1.5 text-xs text-content-2">
          {t('inspiration.parse', 'Parse')}
        </button>
        <button onClick={() => onNotInterested(h)} className="rounded-lg bg-island-2 px-3 py-1.5 text-xs text-content-2">
          {t('inspiration.notInterested', 'Not interested')}
        </button>
        {url && (
          <a
            href={url}
            target="_blank"
            rel="noreferrer"
            className="ml-auto inline-flex items-center gap-1 text-xs text-content-3 hover:text-content-2"
          >
            {t('inspiration.openSource', 'Open source')} <ExternalLink size={11} />
          </a>
        )}
      </div>
    </div>
  );
};
```

- [ ] **Step 4: 跑测试确认全过**

Run: `npx vitest run components/Inspiration/HotspotDetail.test.tsx`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add frontend/components/Inspiration/HotspotDetail.tsx frontend/components/Inspiration/HotspotDetail.test.tsx
git commit -m "feat(inspiration): inline hotspot detail pane for the Hotspots tab"
```

---

### Task 5: HotspotsWorkspace(Hotspots tab 主区)

**Files:**
- Create: `frontend/components/Inspiration/HotspotsWorkspace.tsx`
- Test: `frontend/components/Inspiration/HotspotsWorkspace.test.tsx`

**Interfaces:**
- Consumes: Task 2 `useHotspots`、Task 4 `HotspotDetail`、`topHotspots`(hotspotRanking)、`HotspotCard`(TopicInspiration)
- Produces:
```tsx
<HotspotsWorkspace day={string | null}
  onSaveAsNote={(h: Hotspot) => void} onParse={(h: Hotspot) => void} />
```
左列当日排名列表(topHotspots 排序,复用 HotspotCard 或简列表),点选驱动右侧 HotspotDetail;Not interested 走 useHotspots.applyState({is_hidden:true});空态。

- [ ] **Step 1: 写失败测试**

```tsx
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';

const getHotspots = vi.fn();
const setHotspotState = vi.fn();
const getHotspot = vi.fn();
vi.mock('../../services/topicService', () => ({
  getHotspots: (...a: unknown[]) => getHotspots(...a),
  setHotspotState: (...a: unknown[]) => setHotspotState(...a),
  getHotspot: (...a: unknown[]) => getHotspot(...a),
}));
vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: (_k: string, f: string) => f }) }));

import { HotspotsWorkspace } from './HotspotsWorkspace';

const HS = (id: string, over = {}) => ({ id, title: `Topic ${id}`, tags: [], heat: 90 - Number(id), source_label: 'WEIBO', ...over });

describe('HotspotsWorkspace', () => {
  beforeEach(() => {
    getHotspots.mockReset();
    getHotspot.mockReset();
    getHotspots.mockResolvedValue([HS('1'), HS('2'), HS('3')]);
    getHotspot.mockResolvedValue(HS('1'));
  });

  it('renders the ranked list for the day', async () => {
    render(<HotspotsWorkspace day="2026-07-07" onSaveAsNote={vi.fn()} onParse={vi.fn()} />);
    await waitFor(() => expect(screen.getByText('Topic 1')).toBeTruthy());
    expect(screen.getByText('Topic 3')).toBeTruthy();
  });

  it('clicking a row selects it into the detail pane', async () => {
    render(<HotspotsWorkspace day={null} onSaveAsNote={vi.fn()} onParse={vi.fn()} />);
    await waitFor(() => expect(screen.getByText('Topic 2')).toBeTruthy());
    fireEvent.click(screen.getByText('Topic 2'));
    await waitFor(() => expect(getHotspot).toHaveBeenCalledWith('2'));
  });

  it('empty day shows empty state', async () => {
    getHotspots.mockResolvedValue([]);
    render(<HotspotsWorkspace day={null} onSaveAsNote={vi.fn()} onParse={vi.fn()} />);
    await waitFor(() => expect(screen.getByText(/no hotspots/i)).toBeTruthy());
  });
});
```

- [ ] **Step 2: 跑测试确认失败**

Run: `npx vitest run components/Inspiration/HotspotsWorkspace.test.tsx`
Expected: FAIL

- [ ] **Step 3: 实现**(实现前 Read `components/TopicInspiration/hotspotRanking.ts` 确认 `topHotspots(list, n?)` 签名;Read `HotspotCard.tsx` 的 props——若 props 复杂,本任务用简列表行而非 HotspotCard,避免耦合它的 save/hide 图标语义)

```tsx
// frontend/components/Inspiration/HotspotsWorkspace.tsx
// Hotspots tab main area: ranked list (left) + inline detail (right).
// Date comes from the page-wide selected date; Not-interested hides via applyState.
import React, { useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Flame } from 'lucide-react';
import { useHotspots } from './useHotspots';
import { HotspotDetail } from './HotspotDetail';
import { topHotspots } from '../TopicInspiration/hotspotRanking';
import type { Hotspot } from '../../services/topicService';

interface Props {
  day: string | null;
  onSaveAsNote: (h: Hotspot) => void;
  onParse: (h: Hotspot) => void;
}

export const HotspotsWorkspace: React.FC<Props> = ({ day, onSaveAsNote, onParse }) => {
  const { t } = useTranslation();
  const { hotspots, loading, applyState } = useHotspots({ enabled: true, day: day ?? undefined });
  const [selectedId, setSelectedId] = useState<string | null>(null);

  const ranked = useMemo(() => topHotspots(hotspots.filter((h) => !h.is_hidden), 50), [hotspots]);
  const selected = ranked.find((h) => h.id === selectedId) ?? ranked[0] ?? null;

  const notInterested = async (h: Hotspot) => {
    try {
      await applyState(h, { is_hidden: true });
    } catch (err) {
      console.error('hide hotspot failed', err);
    }
  };

  if (!loading && ranked.length === 0) {
    return (
      <div className="rounded-xl bg-island px-4 py-10 text-center text-sm text-content-3">
        {t('inspiration.noHotspots', 'No hotspots for this day.')}
      </div>
    );
  }

  return (
    <div className="flex gap-2.5" style={{ minHeight: 420 }}>
      <div className="w-[240px] shrink-0 space-y-1.5 overflow-y-auto rounded-xl bg-island p-2">
        {ranked.map((h) => (
          <button
            key={h.id}
            onClick={() => setSelectedId(h.id)}
            className={`block w-full rounded-lg px-3 py-2 text-left ${
              selected?.id === h.id ? 'bg-indigo-500/15' : 'hover:bg-island-2'
            }`}
          >
            <div className={`text-[12.5px] leading-snug ${selected?.id === h.id ? 'text-indigo-300' : 'text-content'}`}>
              {h.title}
            </div>
            <div className="mt-1 flex items-center gap-2 text-[10px] text-content-4 tabular-nums">
              {h.source_label && <span>{h.source_label}</span>}
              {typeof h.heat === 'number' && (
                <span className="inline-flex items-center gap-0.5">
                  <Flame size={9} /> {h.heat}
                </span>
              )}
            </div>
          </button>
        ))}
      </div>
      <div className="min-w-0 flex-1">
        <HotspotDetail hotspot={selected} onSaveAsNote={onSaveAsNote} onParse={onParse} onNotInterested={notInterested} />
      </div>
    </div>
  );
};
```

- [ ] **Step 4: 跑测试确认全过**

Run: `npx vitest run components/Inspiration/HotspotsWorkspace.test.tsx`
Expected: 3 passed。若 `topHotspots` 签名不是 `(list, n)`,按实际调整调用(Read 确认),不改测试断言。

- [ ] **Step 5: Commit**

```bash
git add frontend/components/Inspiration/HotspotsWorkspace.tsx frontend/components/Inspiration/HotspotsWorkspace.test.tsx
git commit -m "feat(inspiration): Hotspots tab workspace — ranked list + detail"
```

---

### Task 6: HotspotsSidePanel(Notes tab 侧栏 Top3)

**Files:**
- Create: `frontend/components/Inspiration/HotspotsSidePanel.tsx`
- Test: `frontend/components/Inspiration/HotspotsSidePanel.test.tsx`

**Interfaces:**
- Consumes: Task 2 `useHotspots`、`topHotspots`
- Produces:
```tsx
<HotspotsSidePanel day={string | null}
  onSaveAsNote={(h: Hotspot) => void} onOpenAll={() => void} />
```
标题 `Hotspots · {day 或 Today}` + Top3 排名行(每行 `+` 存灵感)+ 底部 "All hotspots →"(onOpenAll 切 tab)。enabled 常 true(侧栏常驻)。

- [ ] **Step 1: 写失败测试**

```tsx
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';

const getHotspots = vi.fn();
vi.mock('../../services/topicService', () => ({
  getHotspots: (...a: unknown[]) => getHotspots(...a),
  setHotspotState: vi.fn(),
}));
vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: (_k: string, f: string) => f }) }));

import { HotspotsSidePanel } from './HotspotsSidePanel';

const HS = (id: string) => ({ id, title: `T${id}`, tags: [], heat: 90 - Number(id), source_label: 'WEIBO' });

describe('HotspotsSidePanel', () => {
  beforeEach(() => {
    getHotspots.mockReset();
    getHotspots.mockResolvedValue([HS('1'), HS('2'), HS('3'), HS('4')]);
  });

  it('shows top 3 ranked rows', async () => {
    render(<HotspotsSidePanel day={null} onSaveAsNote={vi.fn()} onOpenAll={vi.fn()} />);
    await waitFor(() => expect(screen.getByText('T1')).toBeTruthy());
    expect(screen.getByText('T3')).toBeTruthy();
    expect(screen.queryByText('T4')).toBeNull();
  });

  it('+ button saves the row as a note', async () => {
    const onSaveAsNote = vi.fn();
    render(<HotspotsSidePanel day={null} onSaveAsNote={onSaveAsNote} onOpenAll={vi.fn()} />);
    await waitFor(() => expect(screen.getByText('T1')).toBeTruthy());
    fireEvent.click(screen.getAllByLabelText('Save as note')[0]);
    expect(onSaveAsNote).toHaveBeenCalledWith(expect.objectContaining({ id: '1' }));
  });

  it('All hotspots link opens the tab', async () => {
    const onOpenAll = vi.fn();
    render(<HotspotsSidePanel day={null} onSaveAsNote={vi.fn()} onOpenAll={onOpenAll} />);
    await waitFor(() => expect(screen.getByText('T1')).toBeTruthy());
    fireEvent.click(screen.getByText(/all hotspots/i));
    expect(onOpenAll).toHaveBeenCalled();
  });
});
```

- [ ] **Step 2: 跑测试确认失败**

Run: `npx vitest run components/Inspiration/HotspotsSidePanel.test.tsx`
Expected: FAIL

- [ ] **Step 3: 实现**

```tsx
// frontend/components/Inspiration/HotspotsSidePanel.tsx
// Notes-tab sidebar: top-3 hotspots for the selected day + save-as-note + open-all.
import React, { useMemo } from 'react';
import { useTranslation } from 'react-i18next';
import { Flame, Plus } from 'lucide-react';
import { useHotspots } from './useHotspots';
import { topHotspots } from '../TopicInspiration/hotspotRanking';
import type { Hotspot } from '../../services/topicService';

interface Props {
  day: string | null;
  onSaveAsNote: (h: Hotspot) => void;
  onOpenAll: () => void;
}

export const HotspotsSidePanel: React.FC<Props> = ({ day, onSaveAsNote, onOpenAll }) => {
  const { t } = useTranslation();
  const { hotspots } = useHotspots({ enabled: true, day: day ?? undefined });
  const top3 = useMemo(() => topHotspots(hotspots.filter((h) => !h.is_hidden), 3), [hotspots]);

  if (top3.length === 0) return null;

  return (
    <div className="rounded-xl bg-island px-4 py-3.5">
      <h3 className="mb-2.5 flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wider text-content-3">
        <Flame size={12} />
        {t('inspiration.hotspots', 'Hotspots')}
        {day && <span className="font-normal normal-case tracking-normal text-content-4">· {day}</span>}
      </h3>
      <div className="space-y-1">
        {top3.map((h, i) => (
          <div key={h.id} className="flex items-start gap-2 border-t border-line py-2 first:border-t-0 first:pt-0">
            <span className="w-3.5 pt-0.5 text-[11px] font-bold text-content-4 tabular-nums">{i + 1}</span>
            <div className="min-w-0 flex-1">
              <div className="text-[12.5px] leading-snug text-content">{h.title}</div>
              <div className="mt-0.5 flex items-center gap-2 text-[10px] text-content-4 tabular-nums">
                {h.source_label && <span>{h.source_label}</span>}
                {typeof h.heat === 'number' && <span>{h.heat}</span>}
              </div>
            </div>
            <button
              aria-label="Save as note"
              onClick={() => onSaveAsNote(h)}
              className="shrink-0 rounded-md bg-island-2 p-1 text-content-3 hover:bg-indigo-500/15 hover:text-indigo-300"
            >
              <Plus size={13} />
            </button>
          </div>
        ))}
      </div>
      <div className="mt-2.5 border-t border-line pt-2.5">
        <button onClick={onOpenAll} className="text-[11.5px] text-content-3 hover:text-content-2">
          {t('inspiration.allHotspots', 'All hotspots →')}
        </button>
      </div>
    </div>
  );
};
```

- [ ] **Step 4: 跑测试确认全过**

Run: `npx vitest run components/Inspiration/HotspotsSidePanel.test.tsx`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add frontend/components/Inspiration/HotspotsSidePanel.tsx frontend/components/Inspiration/HotspotsSidePanel.test.tsx
git commit -m "feat(inspiration): Notes-tab hotspots side panel — top 3 + save-as-note"
```

---

### Task 7: Composer 引用卡 chip(prefill 可视化)

**Files:**
- Modify: `frontend/components/Inspiration/Composer.tsx`(prefill.refHotspot 时显示可移除引用卡 chip)
- Test: `frontend/components/Inspiration/Composer.refchip.test.tsx`

**Interfaces:**
- Consumes: 现有 Composer 的 `prefill?: { content: string; refHotspot?: RefHotspot }`
- Produces: Composer 在 `prefill?.refHotspot` 存在时,textarea 上方渲染一张紧凑引用卡(来源+标题,可 `×` 移除);移除后本次 createNote 不带 refHotspot。保持现有所有行为(⌘Enter/上传/tag 补全)不变。

- [ ] **Step 1: 写失败测试**

```tsx
import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';

const createNote = vi.fn();
vi.mock('../../services/inspirationService', () => ({
  createNote: (...a: unknown[]) => createNote(...a),
  uploadAttachment: vi.fn(),
}));
vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: (_k: string, f: string) => f }) }));
vi.mock('../Toast', () => ({ useToast: () => ({ addToast: vi.fn() }) }));

import { Composer } from './Composer';

const REF = { hotspot_id: '42', title: 'Silent vlog 2.1B', source: 'DOUYIN', heat: 98.4 };

describe('Composer ref-hotspot chip', () => {
  it('shows the reference card when prefill.refHotspot is set', () => {
    render(<Composer onCreated={vi.fn()} tagSuggestions={[]} prefill={{ content: '', refHotspot: REF }} />);
    expect(screen.getByText('Silent vlog 2.1B')).toBeTruthy();
    expect(screen.getByText('DOUYIN')).toBeTruthy();
  });

  it('save passes the refHotspot to createNote', async () => {
    createNote.mockResolvedValue({ id: '1', attachments: [], tags: [] });
    render(<Composer onCreated={vi.fn()} tagSuggestions={[]} prefill={{ content: 'my take', refHotspot: REF }} />);
    fireEvent.click(screen.getByText('Save'));
    await waitFor(() => expect(createNote).toHaveBeenCalledWith('my take', REF));
  });

  it('removing the chip drops the ref from the save', async () => {
    createNote.mockResolvedValue({ id: '1', attachments: [], tags: [] });
    render(<Composer onCreated={vi.fn()} tagSuggestions={[]} prefill={{ content: 'my take', refHotspot: REF }} />);
    fireEvent.click(screen.getByLabelText('Remove hotspot reference'));
    fireEvent.click(screen.getByText('Save'));
    await waitFor(() => expect(createNote).toHaveBeenCalledWith('my take', undefined));
  });
});
```

- [ ] **Step 2: 跑测试确认失败**

Run: `npx vitest run components/Inspiration/Composer.refchip.test.tsx`
Expected: FAIL(现在 Composer submit 用 `prefill?.refHotspot`,无 chip、无移除、无独立 state)

- [ ] **Step 3: 实现**(Read 现有 Composer.tsx,做最小改动)

在 Composer 内把 refHotspot 提为可移除 state 并渲染 chip:
```tsx
// 现有 state 区加:
const [ref, setRef] = useState(prefill?.refHotspot ?? null);
```
`submit()` 里把 `createNote(content, prefill?.refHotspot)` 改为 `createNote(content, ref ?? undefined)`。
textarea 上方(saving/staged 区之前)加 chip:
```tsx
{ref && (
  <div className="mb-2 flex items-center gap-2 rounded-lg border border-line border-l-2 border-l-indigo-500 bg-island-2 px-3 py-2">
    <div className="min-w-0 flex-1">
      {ref.source && <span className="text-[10px] font-bold text-content-2">{ref.source}</span>}
      <div className="truncate text-[12px] text-content">{ref.title}</div>
    </div>
    <button
      aria-label="Remove hotspot reference"
      onClick={() => setRef(null)}
      className="shrink-0 text-content-3 hover:text-content"
    >
      <X size={13} />
    </button>
  </div>
)}
```
(`X` 已从 lucide import;若未 import 则补。)保持其余逻辑不动。

- [ ] **Step 4: 跑测试确认全过 + Composer 回归**

Run: `npx vitest run components/Inspiration/Composer.refchip.test.tsx` → 3 passed
Run: `npx vitest run components/Inspiration/Composer.test.tsx` → P2 既有 Composer 测试无回归

- [ ] **Step 5: Commit**

```bash
git add frontend/components/Inspiration/Composer.tsx frontend/components/Inspiration/Composer.refchip.test.tsx
git commit -m "feat(inspiration): composer shows removable hotspot reference chip"
```

---

### Task 8: InspirationPage 集成 + i18n + 收口

**Files:**
- Modify: `frontend/pages/InspirationPage.tsx`(tab 状态、Hotspots tab、侧栏对称面板、存灵感闭环、全局 Parse、热点 category 标签)
- Modify: `frontend/public/locales/en.json` + `zh.json`(新增 key)
- Test: `frontend/pages/InspirationPage.hotspots.test.tsx`

**Interfaces:**
- Consumes: Task 2/5/6 组件、Task 1 `hotspotToRef`、Task 3 受控 FloatingParse、现有 Composer(Task 7 支持 ref chip)
- Produces: 完整 P3 页面。新增状态:`tab: 'notes' | 'hotspots'`、`prefill: {content, refHotspot} | null`、`prefillNonce: number`(Composer key)、`parseOpen: boolean`。

- [ ] **Step 1: 写失败测试**

```tsx
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';

const listNotes = vi.fn();
const getTagCounts = vi.fn();
const getActivity = vi.fn();
const getHotspots = vi.fn();
const getHotspot = vi.fn();
vi.mock('../services/inspirationService', () => ({
  listNotes: (...a: unknown[]) => listNotes(...a),
  getTagCounts: (...a: unknown[]) => getTagCounts(...a),
  getActivity: (...a: unknown[]) => getActivity(...a),
  createNote: vi.fn(), updateNote: vi.fn(), deleteNote: vi.fn(),
  uploadAttachment: vi.fn(), attachmentUrlWithToken: (id: string) => `u/${id}`,
}));
vi.mock('../services/topicService', () => ({
  getHotspots: (...a: unknown[]) => getHotspots(...a),
  getHotspot: (...a: unknown[]) => getHotspot(...a),
  setHotspotState: vi.fn(),
}));
vi.mock('../components/AILibrary/MarkdownBody', () => ({ default: ({ source }: { source: string }) => <div>{source}</div> }));
vi.mock('../contexts/AuthContext', () => ({ useAuth: () => ({ mediaToken: 'tok' }) }));
vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: (_k: string, f: string) => f }) }));
vi.mock('../components/Toast', () => ({ useToast: () => ({ addToast: vi.fn() }) }));
// FloatingParse 常驻,mock 掉避免它的 service 依赖
vi.mock('../components/TopicInspiration/FloatingParse', () => ({ FloatingParse: () => <div data-testid="floating-parse" /> }));

import { InspirationPage } from './InspirationPage';

const HS = (id: string) => ({ id, title: `Hot ${id}`, tags: [], heat: 90 - Number(id), source_label: 'WEIBO' });

describe('InspirationPage P3 hotspots', () => {
  beforeEach(() => {
    listNotes.mockResolvedValue([]);
    getTagCounts.mockResolvedValue([]);
    getActivity.mockResolvedValue([]);
    getHotspots.mockResolvedValue([HS('1'), HS('2'), HS('3')]);
    getHotspot.mockResolvedValue(HS('1'));
  });

  it('renders Notes/Hotspots tabs and a global Parse button', async () => {
    render(<InspirationPage />);
    expect(screen.getByText('Notes')).toBeTruthy();
    expect(screen.getByText('Hotspots')).toBeTruthy();
    expect(screen.getByText('Parse URL')).toBeTruthy();
  });

  it('switching to Hotspots tab shows the ranked workspace', async () => {
    render(<InspirationPage />);
    fireEvent.click(screen.getByText('Hotspots'));
    await waitFor(() => expect(screen.getAllByText('Hot 1').length).toBeGreaterThan(0));
  });

  it('save-as-note from the sidebar switches to Notes with the composer ref chip', async () => {
    render(<InspirationPage />);
    // Notes-tab sidebar Top3 renders after hotspots load
    await waitFor(() => expect(screen.getByText('Hot 1')).toBeTruthy());
    fireEvent.click(screen.getAllByLabelText('Save as note')[0]);
    // composer now shows the referenced hotspot title
    await waitFor(() => expect(screen.getAllByText('Hot 1').length).toBeGreaterThan(0));
  });
});
```

- [ ] **Step 2: 跑测试确认失败**

Run: `npx vitest run pages/InspirationPage.hotspots.test.tsx`
Expected: FAIL

- [ ] **Step 3: 实现集成**(Read 现有 InspirationPage.tsx 全文后改)

关键改动(在 P2 InspirationPage 基础上):
1. import:`useState` 已有;加 `HotspotsWorkspace`/`HotspotsSidePanel`/`hotspotToRef`/`FloatingParse`(from '../components/TopicInspiration/FloatingParse')/`Hotspot`(topicService)。
2. 新状态:
```tsx
const [tab, setTab] = useState<'notes' | 'hotspots'>('notes');
const [prefill, setPrefill] = useState<{ content: string; refHotspot: import('../services/inspirationService').RefHotspot } | null>(null);
const [prefillNonce, setPrefillNonce] = useState(0);
const [parseOpen, setParseOpen] = useState(false);
```
3. save-as-note 处理器(sidebar 与 workspace 共用):
```tsx
const handleSaveAsNote = (h: Hotspot) => {
  setPrefill({ content: '', refHotspot: hotspotToRef(h) });
  setPrefillNonce((n) => n + 1);
  setTab('notes');
};
```
4. 顶栏:标题后加 tab 分段(Notes/Hotspots,复用 P2 mockup 的 seg 样式)、chips 保留、搜索框保留、**末尾加全局 Parse URL 按钮**(`onClick={() => setParseOpen(true)}`,accent 软底样式)。
5. Composer 挂载点加 key + prefill:
```tsx
<Composer key={prefillNonce} prefill={prefill} onCreated={(note) => { onCreated(note); setPrefill(null); }} onAttachmentUploaded={onAttachmentUploaded} tagSuggestions={tags.map((x) => x.tag)} />
```
6. 主区按 tab 分支:`tab === 'notes'` → 现有 Composer+NoteTimeline;`tab === 'hotspots'` → `<HotspotsWorkspace day={date} onSaveAsNote={handleSaveAsNote} onParse={(h) => setParseOpen(true)} />`。
7. 右栏按 tab:两 tab 都显 ActivityPanel(共享日历);`tab === 'notes'` 额外显 HotspotsSidePanel(`day={date}` `onSaveAsNote={handleSaveAsNote}` `onOpenAll={() => setTab('hotspots')}`)+ TagsPanel(笔记标签);`tab === 'hotspots'` 显热点 category 标签面板(客户端从当日热点聚合——见下)。
8. 页面根部常驻 `<FloatingParse open={parseOpen} onOpenChange={setParseOpen} />`。
9. 热点 category 标签(Hotspots tab 侧栏,复用 TagsPanel 展示形态):在页面内用 useHotspots 或把 workspace 已加载的 categories 提上来聚合——**为避免重复请求,简化为**:Hotspots tab 时右栏用一个内联小聚合(从一次 `getHotspots(date)` 结果 reduce category→count),点击 category 作为热点列表的客户端过滤。若嫌复杂,P3 首版**热点 category 标签仅展示不过滤**(spec 允许渐进),用一个只读 chip 列表;真过滤留 follow-up。**采用只读展示**以控制本任务体量。

**注意 rules-of-hooks**:tab 分支只能在 return JSX 里做,所有 hooks(含 useHotspots 若在页面直接调)必须无条件在顶部调用。HotspotsWorkspace/HotspotsSidePanel 内部各自调 useHotspots,页面本身不需要——保持页面 hooks 不随 tab 增减。

- [ ] **Step 4: i18n keys**

`en.json` 的 `inspiration` 命名空间补(zh.json 同 key 中文):`notes`(Notes)、`hotspotsTab`(Hotspots)、`parseUrl`(Parse URL)、`hotspots`(Hotspots)、`allHotspots`(All hotspots →)、`saveAsNote`(Save as note)、`parse`(Parse)、`notInterested`(Not interested)、`selectHotspot`(Select a hotspot to see details)、`noHotspots`(No hotspots for this day.)、`summary`(Summary)、`sourceCount`({{count}} sources)。zh:notes=笔记、hotspotsTab=热点、parseUrl=解析链接、hotspots=热点、allHotspots=全部热点 →、saveAsNote=存为灵感、parse=解析、notInterested=不感兴趣、selectHotspot=选择一个热点查看详情、noHotspots=当天没有热点。、summary=摘要、sourceCount={{count}} 个来源。JSON 改完 `node -e "JSON.parse(require('fs').readFileSync('public/locales/en.json'))"` 两文件校验。

- [ ] **Step 5: 跑测试 + 全量验证**

```bash
npx vitest run pages/InspirationPage.hotspots.test.tsx          # 3 passed
npx vitest run pages/InspirationPage.test.tsx                   # P2 既有页面测试无回归
npm run lint                                                    # rules-of-hooks 零 error
npx vitest run 2>&1 | tail -3                                   # 全量
npm run build                                                   # 过
```

- [ ] **Step 6: Commit**

```bash
git add frontend/pages/InspirationPage.tsx frontend/pages/InspirationPage.hotspots.test.tsx frontend/public/locales/en.json frontend/public/locales/zh.json
git commit -m "feat(inspiration): P3 integration — Notes/Hotspots tabs, save-as-note loop, global Parse"
```

---

### Task 9: 收口 — push + PR

- [ ] **Step 1**: `cd frontend && npm run lint && npm run build && npx vitest run 2>&1 | tail -3` 全绿
- [ ] **Step 2**: 合并最新 master(`git fetch origin && git merge origin/master --no-edit`),重跑全量 vitest 确认无回归
- [ ] **Step 3**: `git push -u origin feature/inspiration-p3-hotspots`,`gh pr create` 标题 `feat(inspiration): P3 hotspot fusion — tabs, save-as-note loop, global Parse (flag-dark)`,body 说明 flag off 零变化、组件清单、复用的老热点组件、纯前端零后端、测试数
- [ ] **Step 4**: CI 全绿后单独 `gh pr merge --squash --delete-branch`
