# Inspiration Notes P4d — 开 flag 前挂账修正 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修掉 P3/P4 终审记账的、开 flag 前必须修的体验缺陷:存灵感闭环补全(热点标签+聚焦)、Parse 预填 URL、热点数据单实例(去重复请求)+category 点击过滤、NotesSidePanel 对称面板、pin 前置分组、过滤空态文案。全部 flag 后,纯前端。

**Architecture:** useHotspots 从三处独立调用收敛为 InspirationPage 单实例、props 下沉(顺带解决 hide 不同步);FloatingParse 加 initialUrl 受控 prop;NoteTimeline 加 Pinned 分组;新组件 NotesSidePanel。

**Tech Stack:** React 19 + TS + vitest,零新依赖,零后端。

**挂账来源:** `docs/superpowers/specs/2026-07-07-inspiration-notes-design.md` 契约 #7/#8/§2.1 对称原则;P3/P4 终审记账(memory project_inspiration_notes_epic)。

## Global Constraints

- 工作目录:本 worktree(分支 `feature/inspiration-p4d-polish`);全部命令在 `frontend/` 下
- 全部改动在 `VITE_FEATURE_INSPIRATION_NOTES` 后;flag off 零影响;不碰 TopicInspirationPage/共享组件
- i18n 英文兜底,en/zh 同步;零 emoji;禁 zinc
- 每 task:`npx vitest run <本任务测试>` 过 + 相关既有测试零回归 + commit;收口 `npm run lint`(rules-of-hooks 阻塞)/`npm run build`/全量 vitest
- **P2/P3/P4a 的既有守卫别破坏**:loadMore requestSeq、toggleSeq、TaskIndexContext、Composer key 重挂载机制

---

### Task 1: 存灵感闭环补全(热点标签 + 聚焦)

**Files:**
- Modify: `frontend/components/Inspiration/hotspotToRef.ts`(追加导出 `buildPrefillContent`;ref 快照本身不动——tags 走 prefill content 不进 ref)
- Modify: `frontend/pages/InspirationPage.tsx`(handleSaveAsNote 组 content)
- Modify: `frontend/components/Inspiration/Composer.tsx`(autoFocus + 光标行首)
- Test: `frontend/components/Inspiration/Composer.autofocus.test.tsx` + 改 `frontend/pages/InspirationPage.hotspots.test.tsx` 加 1 条

**Interfaces:**
- `handleSaveAsNote(h)` 的 prefill.content 变为:热点标签行 —— `buildPrefillContent(h)` 纯函数:取 `h.category` 与 `h.tags` 合并去重(小写、保序、category 在前),映射成 `\n\n#tag1 #tag2`(前置两个换行=用户想法写在上面);无任何标签时 content 为 `''`
- Composer 新增 prop `autoFocus?: boolean`;为 true 时挂载后 `taRef.current?.focus()` 且 `setSelectionRange(0, 0)`(光标落正文首行,在标签行之前)。InspirationPage 传 `autoFocus={!!prefill}`

- [ ] **Step 1: 写失败测试**

`Composer.autofocus.test.tsx`(mock 同现有 Composer 测试:inspirationService/react-i18next/Toast):
```tsx
it('autoFocus focuses the textarea with caret at start', () => {
  render(<Composer onCreated={vi.fn()} tagSuggestions={[]} autoFocus prefill={{ content: '\n\n#food #shortform', refHotspot: { title: 'x' } }} />);
  const ta = screen.getByRole('textbox') as HTMLTextAreaElement;
  expect(document.activeElement).toBe(ta);
  expect(ta.selectionStart).toBe(0);
  expect(ta.value).toContain('#food #shortform');
});
```
`InspirationPage.hotspots.test.tsx` 追加(现有 mock 基础上,热点数据带 category/tags):
```tsx
it('save-as-note prefills hotspot tags into the composer', async () => {
  getHotspots.mockResolvedValue([{ id: '1', title: 'Hot 1', tags: ['trend'], category: 'food', heat: 90, source_label: 'WEIBO' }]);
  render(<InspirationPage />);
  await waitFor(() => expect(screen.getByText('Hot 1')).toBeTruthy());
  fireEvent.click(screen.getAllByLabelText('Save as note')[0]);
  const ta = await screen.findByRole('textbox');
  expect((ta as HTMLTextAreaElement).value).toContain('#food');
  expect((ta as HTMLTextAreaElement).value).toContain('#trend');
});
```

- [ ] **Step 2: 跑测试确认失败**

Run: `npx vitest run components/Inspiration/Composer.autofocus.test.tsx pages/InspirationPage.hotspots.test.tsx`

- [ ] **Step 3: 实现**

InspirationPage.tsx——`handleSaveAsNote` 上方加纯函数(或放 hotspotToRef.ts 同文件导出 `buildPrefillContent`,更可测,推荐):
```ts
// hotspotToRef.ts 追加导出
export function buildPrefillContent(h: Hotspot): string {
  const tags: string[] = [];
  const push = (raw?: string | null) => {
    const t = (raw ?? '').trim().toLowerCase().replace(/\s+/g, '-');
    if (t && !tags.includes(t)) tags.push(t);
  };
  push(h.category);
  for (const t of h.tags ?? []) push(t);
  return tags.length ? `\n\n${tags.map((t) => `#${t}`).join(' ')}` : '';
}
```
`handleSaveAsNote`:`setPrefill({ content: buildPrefillContent(h), refHotspot: hotspotToRef(h) })`(其余不变)。Composer 挂载点加 `autoFocus={!!prefill}`。
Composer.tsx:props 加 `autoFocus?: boolean`;加
```tsx
useEffect(() => {
  if (!autoFocus) return;
  const ta = taRef.current;
  if (ta) {
    ta.focus();
    ta.setSelectionRange(0, 0);
  }
}, [autoFocus]);
```
(挂载级 effect;Composer 每次 prefill 都因 key 重挂载,天然只跑一次。)

- [ ] **Step 4: 跑测试全过 + Composer/页面既有测试零回归**

Run: `npx vitest run components/Inspiration/Composer.autofocus.test.tsx components/Inspiration/Composer.test.tsx components/Inspiration/Composer.refchip.test.tsx pages/InspirationPage.hotspots.test.tsx components/Inspiration/hotspotToRef.test.ts`
(hotspotToRef.test.ts 追加 buildPrefillContent 的 2 条:有 category+tags 去重小写连字符化;全空返回 ''。)

- [ ] **Step 5: Commit** — `feat(inspiration): save-as-note carries hotspot tags + focuses composer`

---

### Task 2: FloatingParse initialUrl + Parse 预填

**Files:**
- Modify: `frontend/components/TopicInspiration/FloatingParse.tsx`(加 `initialUrl?: string` 受控 prop)
- Modify: `frontend/pages/InspirationPage.tsx`(onParse(h) 传 url)
- Test: `frontend/components/TopicInspiration/FloatingParse.controlled.test.tsx` 追加 1 条

**Interfaces:**
- FloatingParse 新 prop `initialUrl?: string`:受控 open 变 true 时,若给了 initialUrl 且输入框为空,预填进 input state。非受控/未给时行为不变。
- InspirationPage:`parseUrl` state(string|null);HotspotsWorkspace 的 `onParse={(h) => { setParseUrl(h.origin_url || h.url || null); setParseOpen(true); }}`;顶栏全局按钮 `onClick={() => { setParseUrl(null); setParseOpen(true); }}`;`<FloatingParse open={parseOpen} onOpenChange={setParseOpen} initialUrl={parseUrl ?? undefined} />`

- [ ] **Step 1: 失败测试**(FloatingParse.controlled.test.tsx 追加,mock 沿用该文件现有)

```tsx
it('prefills the input with initialUrl when opened controlled', () => {
  render(<FloatingParse open onOpenChange={vi.fn()} initialUrl="https://douyin.com/x" />);
  expect(screen.getByDisplayValue('https://douyin.com/x')).toBeTruthy();
});
```

- [ ] **Step 2: 确认失败** → **Step 3: 实现**

FloatingParse:props 加 initialUrl;受控 open 的 useEffect 里(open 变 true 且 phase 进 input 时):
```tsx
useEffect(() => {
  if (open === undefined) return;
  if (open) {
    setPhase((p) => (p === 'collapsed' ? 'input' : p));
    if (initialUrl) setInput((prev) => (prev ? prev : initialUrl));
  } else {
    setPhase('collapsed');
  }
}, [open, initialUrl]);
```
(在现有受控 effect 上改,保持既有语义;`prev ? prev : initialUrl` 不覆盖用户已输入。)
InspirationPage 按 Interfaces 接线;HotspotDetail 的 onParse 已把 h 传出(P3 遗留正好用上)。

- [ ] **Step 4: 全过 + 回归** — `npx vitest run components/TopicInspiration/ pages/InspirationPage.hotspots.test.tsx`
- [ ] **Step 5: Commit** — `feat(inspiration): Parse from a hotspot prefills its URL`

---

### Task 3: useHotspots 单实例下沉 + category 点击过滤

**Files:**
- Modify: `frontend/pages/InspirationPage.tsx`(唯一 useHotspots 实例;category 过滤 state)
- Modify: `frontend/components/Inspiration/HotspotsWorkspace.tsx`(改收 props,不再自调 hook)
- Modify: `frontend/components/Inspiration/HotspotsSidePanel.tsx`(同)
- Test: 改 `HotspotsWorkspace.test.tsx`/`HotspotsSidePanel.test.tsx`(props 注入替代 service mock)+ 页面测试加 1 条 category 过滤

**Interfaces:**
- InspirationPage 持有唯一 `const { hotspots, loading: hotspotsLoading, applyState } = useHotspots({ enabled: true, day: date ?? undefined });` 和 `const [activeCategory, setActiveCategory] = useState<string | null>(null);`
- HotspotsWorkspace 新签名:`{ hotspots: Hotspot[]; loading: boolean; applyState: (h, patch) => Promise<void>; activeCategory: string | null; onSaveAsNote; onParse }`——内部 `ranked = topHotspots(hotspots.filter(h => !h.is_hidden && (!activeCategory || h.category === activeCategory)), 50)`;不再 import useHotspots
- HotspotsSidePanel 新签名:`{ hotspots: Hotspot[]; day: string | null; onSaveAsNote; onOpenAll }`(内部 top3 同前,不再自调 hook)
- 页面的 category chip 区(现有 categoryHotspots 聚合改用同一 hotspots):点 chip → setActiveCategory(同值再点=null);chip 高亮 activeCategory;传给 Workspace
- 删除页面里第二个 useHotspots 调用(categoryHotspots)

- [ ] **Step 1: 改测试为失败态**(Workspace/SidePanel 测试从 mock topicService 改为直接传 props——组件不再发请求;页面测试保留 topicService mock(页面 hook 仍调)并加:
```tsx
it('clicking a category chip filters the workspace list', async () => {
  getHotspots.mockResolvedValue([
    { id: '1', title: 'Food topic', tags: [], category: 'food', heat: 90 },
    { id: '2', title: 'Music topic', tags: [], category: 'music', heat: 80 },
  ]);
  render(<InspirationPage />);
  fireEvent.click(screen.getByText('Hotspots'));
  await waitFor(() => expect(screen.getByRole('button', { name: /Music topic/ })).toBeTruthy());
  fireEvent.click(screen.getByText('#food'));
  await waitFor(() => expect(screen.queryByRole('button', { name: /Music topic/ })).toBeNull());
  expect(screen.getByRole('button', { name: /Food topic/ })).toBeTruthy();
});
```
(category chip 的渲染文本按页面现状 `#food`;实现前 Read 页面 category 区确认文本形态,以现状为准调 selector。)

- [ ] **Step 2: 确认失败** → **Step 3: 实现**(按 Interfaces;注意 applyState 引用从页面传下后,SidePanel 不再需要它——它只读;Workspace 的 notInterested 用传入的 applyState)

- [ ] **Step 4: 全过 + 回归** — `npx vitest run components/Inspiration/HotspotsWorkspace.test.tsx components/Inspiration/HotspotsSidePanel.test.tsx pages/`(页面既有测试因 hook 收敛可能需调 mock 计数断言——语义不变)
- [ ] **Step 5: Commit** — `refactor(inspiration): single hotspots instance + clickable category filter`

---

### Task 4: NotesSidePanel(Hotspots tab 对称面板)

**Files:**
- Create: `frontend/components/Inspiration/NotesSidePanel.tsx`
- Modify: `frontend/pages/InspirationPage.tsx`(Hotspots tab 右栏挂载)
- Test: `frontend/components/Inspiration/NotesSidePanel.test.tsx`

**Interfaces:**
- `<NotesSidePanel recentNotes={InspirationNote[]} onQuickSave={(content: string) => Promise<void>} onOpenNotes={() => void} />`
- 渲染:标题 "Notes";一行 mini 输入(placeholder "Quick note…" + Save 按钮,空文本不提交,提交后清空);最近 3 条笔记(content_md 首行截断 + 时间 HH:MM);底部 "Open in Notes →"(onOpenNotes)
- 页面:`recentNotes={notes.slice(0, 3)}`;`onQuickSave` = `createNote(content)` 后 prepend + refreshKey bump(复用 onCreated 逻辑);`onOpenNotes={() => setTab('notes')}`;挂在 Hotspots tab 右栏(ActivityPanel 之后、category 面板之后)

- [ ] **Step 1: 失败测试**

```tsx
const NOTE = (id: string, md: string) => ({ id, content_md: md, tags: [], ref_hotspot: null, pinned: false, note_date: '2026-07-10', created_at: '2026-07-10T09:00:00+00:00', updated_at: '2026-07-10T09:00:00+00:00', attachments: [] });

it('renders up to 3 recent notes (first line, truncated)', () => {
  render(<NotesSidePanel recentNotes={[NOTE('1', 'first idea\nsecond line'), NOTE('2', 'b'), NOTE('3', 'c')]} onQuickSave={vi.fn()} onOpenNotes={vi.fn()} />);
  expect(screen.getByText('first idea')).toBeTruthy();
  expect(screen.queryByText(/second line/)).toBeNull();
});

it('quick save submits trimmed content and clears', async () => {
  const onQuickSave = vi.fn().mockResolvedValue(undefined);
  render(<NotesSidePanel recentNotes={[]} onQuickSave={onQuickSave} onOpenNotes={vi.fn()} />);
  const input = screen.getByPlaceholderText('Quick note…');
  fireEvent.change(input, { target: { value: '  quick #x  ' } });
  fireEvent.click(screen.getByText('Save'));
  await waitFor(() => expect(onQuickSave).toHaveBeenCalledWith('quick #x'));
  expect((input as HTMLInputElement).value).toBe('');
});

it('empty input does not submit; open-notes fires', () => {
  const onQuickSave = vi.fn(); const onOpenNotes = vi.fn();
  render(<NotesSidePanel recentNotes={[]} onQuickSave={onQuickSave} onOpenNotes={onOpenNotes} />);
  fireEvent.click(screen.getByText('Save'));
  expect(onQuickSave).not.toHaveBeenCalled();
  fireEvent.click(screen.getByText(/open in notes/i));
  expect(onOpenNotes).toHaveBeenCalled();
});
```
(mock react-i18next 同现有组件测试。)

- [ ] **Step 2: 确认失败** → **Step 3: 实现**

```tsx
// frontend/components/Inspiration/NotesSidePanel.tsx
// Hotspots-tab sidebar twin of HotspotsSidePanel (spec §2.1 symmetry): capture
// never requires leaving the tab you're in.
import React, { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Lightbulb } from 'lucide-react';
import type { InspirationNote } from '../../services/inspirationService';

interface Props {
  recentNotes: InspirationNote[];
  onQuickSave: (content: string) => Promise<void>;
  onOpenNotes: () => void;
}

function firstLine(md: string): string {
  return md.split('\n', 1)[0];
}

function timeOf(iso: string): string {
  const d = new Date(iso);
  return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
}

export const NotesSidePanel: React.FC<Props> = ({ recentNotes, onQuickSave, onOpenNotes }) => {
  const { t } = useTranslation();
  const [text, setText] = useState('');
  const [saving, setSaving] = useState(false);

  const submit = async () => {
    const content = text.trim();
    if (!content || saving) return;
    setSaving(true);
    try {
      await onQuickSave(content);
      setText('');
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="rounded-xl bg-island px-4 py-3.5">
      <h3 className="mb-2.5 flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wider text-content-3">
        <Lightbulb size={12} />
        {t('inspiration.notesPanel', 'Notes')}
      </h3>
      <div className="flex items-center gap-1.5 rounded-lg bg-island-2 py-1 pl-3 pr-1">
        <input
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && void submit()}
          placeholder={t('inspiration.quickNote', 'Quick note…')}
          className="w-full bg-transparent text-xs text-content placeholder:text-content-4 focus:outline-none"
        />
        <button
          onClick={() => void submit()}
          disabled={saving || !text.trim()}
          className="shrink-0 rounded-md bg-indigo-500 px-2.5 py-1 text-[10.5px] font-semibold text-white disabled:opacity-40"
        >
          {t('inspiration.save', 'Save')}
        </button>
      </div>
      {recentNotes.slice(0, 3).map((n) => (
        <div key={n.id} className="border-t border-line py-2 first-of-type:mt-2">
          <div className="truncate text-[12px] leading-snug text-content">{firstLine(n.content_md)}</div>
          <div className="mt-0.5 flex gap-2 text-[10px] text-content-4 tabular-nums">
            <span>{timeOf(n.created_at)}</span>
            {n.attachments.length > 0 && (
              <span>{t('inspiration.fileCount', '{{count}} files', { count: n.attachments.length })}</span>
            )}
          </div>
        </div>
      ))}
      <div className="mt-2 border-t border-line pt-2.5">
        <button onClick={onOpenNotes} className="text-[11.5px] text-content-3 hover:text-content-2">
          {t('inspiration.openInNotes', 'Open in Notes →')}
        </button>
      </div>
    </div>
  );
};
```
页面:Hotspots tab 右栏加 `<NotesSidePanel recentNotes={notes.slice(0, 3)} onQuickSave={async (c) => { const n = await createNote(c); onCreated(n); }} onOpenNotes={() => setTab('notes')} />`(createNote 需 import;失败让异常冒给面板的 try/finally——面板不吞错,页面层 toast:包一层 `try { ... } catch (err) { addToast((err as Error).message, 'error'); throw err; }` 或直接在 onQuickSave 里 toast 后 rethrow,选一致做法)。

- [ ] **Step 4: 全过** — `npx vitest run components/Inspiration/NotesSidePanel.test.tsx pages/InspirationPage.hotspots.test.tsx`
- [ ] **Step 5: Commit** — `feat(inspiration): NotesSidePanel — symmetric quick capture on the Hotspots tab`

---

### Task 5: pin 前置分组 + 过滤空态文案

**Files:**
- Modify: `frontend/components/Inspiration/NoteTimeline.tsx`(Pinned 分组 + emptyBecauseFiltered prop)
- Modify: `frontend/pages/InspirationPage.tsx`(传 filtered 标志)
- Test: `frontend/components/Inspiration/NoteTimeline.test.tsx`(新文件,3 条)

**Interfaces:**
- NoteTimeline:已加载 notes 中 `pinned===true` 的抽出为顶部 "Pinned" 组(组头样式同日分组,label `Pinned`),其余照日分组;新增 prop `filtered?: boolean`——空列表时文案 filtered ? `No matching notes.` : 现有 `No notes yet — capture your first idea above.`
- 页面:`filtered={!!(date || tag || q)}`
- 诚实边界(组件注释写明):Pinned 分组只作用于**已加载页**;旧 pinned 笔记若未加载进来不会浮上来(后端排序仍 id desc,真·全局置顶需后端复合 keyset,记 backlog)

- [ ] **Step 1: 失败测试**

```tsx
it('pinned notes float into a Pinned group above day groups', () => {
  const notes = [N('3', { pinned: false }), N('2', { pinned: true }), N('1', { pinned: false })];
  render(<NoteTimeline notes={notes} {...handlers} hasMore={false} loading={false} loadMore={vi.fn()} />);
  const headings = screen.getAllByText(/Pinned|JUL/i);
  expect(headings[0].textContent).toMatch(/Pinned/i);
});

it('empty with filters shows no-matching copy', () => {
  render(<NoteTimeline notes={[]} {...handlers} filtered hasMore={false} loading={false} loadMore={vi.fn()} />);
  expect(screen.getByText(/no matching notes/i)).toBeTruthy();
});

it('empty without filters keeps the capture-first copy', () => {
  render(<NoteTimeline notes={[]} {...handlers} hasMore={false} loading={false} loadMore={vi.fn()} />);
  expect(screen.getByText(/capture your first idea/i)).toBeTruthy();
});
```
(N() 工厂/handlers/mocks 照 NoteCard 测试模式;NoteCard mock 掉。)

- [ ] **Step 2: 确认失败** → **Step 3: 实现**(NoteTimeline 的 groups useMemo 前先 `const pinnedNotes = notes.filter(n => n.pinned); const rest = notes.filter(n => !n.pinned);`,渲染顺序:Pinned 组(若有)→ rest 的日分组;空态按 filtered 分支;i18n key `inspiration.pinned`/`inspiration.noMatching` 补 en/zh)

- [ ] **Step 4: 全过 + 回归** — `npx vitest run components/Inspiration/ pages/`
- [ ] **Step 5: Commit** — `feat(inspiration): pinned group at top + filtered empty-state copy`

---

### Task 6: 收口 — 全量验证 + PR

- [ ] `npm run lint && npm run build && npx vitest run 2>&1 | tail -3` 全绿
- [ ] 合并最新 master(`git fetch origin && git merge origin/master --no-edit`)重跑全量
- [ ] `git push -u origin feature/inspiration-p4d-polish`,`gh pr create` 标题 `feat(inspiration): P4d pre-launch polish — save-as-note tags/focus, parse prefill, single hotspots source, NotesSidePanel, pinned group`,body 列六项挂账逐一对应
- [ ] CI 全绿后单独 `gh pr merge --squash --delete-branch`
