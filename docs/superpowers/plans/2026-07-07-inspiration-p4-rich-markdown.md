# Inspiration Notes P4a — 富 Markdown(代码高亮 + 交互 checkbox)Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 笔记正文渲染升级到 memos 级富格式的第一批:语法高亮代码块 + 可交互任务清单 checkbox(点击切换并回写 content_md)。GFM 表格/删除线由现有 remark-gfm 已覆盖。

**Architecture:** 新建**笔记专用** `NoteMarkdown` 组件(react-markdown + remark-gfm/breaks + rehype-highlight + 自定义交互 checkbox),NoteCard 从 MarkdownBody 切到它——**不改共享 MarkdownBody**(AI chat 零风险)。checkbox 点击回调带 index 上抛到 InspirationPage,纯函数 `toggleTaskItem(md, index)` 翻转第 N 个 `[ ]`↔`[x]`,`updateNote` 乐观回写(P2 已有)。

**Tech Stack:** react-markdown 10 + rehype-highlight 7 + highlight.js 11(新增 devDep→dep)+ vitest。

**Spec:** `docs/superpowers/specs/2026-07-07-inspiration-notes-design.md` §2.2 #5b(memos-parity rich markdown)。

**明确不做(本阶段,后续 P4b/P4c):** KaTeX 数学公式(katex 依赖重~280KB,niche;留 P4b)、PAT 外部录入 API(独立后端阶段;留 P4c)、inline #tag 正文高亮(P2 已降级为卡片底 chip)。

## Global Constraints

- 工作目录:本 worktree(分支 `feature/inspiration-p4-rich-md`);全部命令在 `frontend/` 下
- flag:功能只在 `VITE_FEATURE_INSPIRATION_NOTES` on 时可见(NoteCard 只在 InspirationPage 渲染);无需新 flag
- **不改** `frontend/components/AILibrary/MarkdownBody.tsx`(共享组件,AI chat 依赖它)
- UI 文本英文走 i18n;零 emoji;禁 zinc(用 ink/island/content token)
- 交互 checkbox 回写走 `updateNote(id, {content_md})`(P2 已有),乐观更新原位替换,失败 toast + 回滚(refetch 或还原)
- `toggleTaskItem` 是纯函数契约:文档顺序第 index 个任务标记 `[ ]`/`[x]` 翻转,其余不动(含代码块内的假标记不算——但首版可接受简单实现:只翻转行首任务列表标记 `^\s*[-*+] \[[ xX]\]`,不解析代码块;测试覆盖代码块内不误伤)
- 每 task 结束:`npx vitest run <本任务测试>` 过 + commit;收口跑 `npm run lint`(rules-of-hooks 阻塞)/ `npm run build` / 全量 vitest
- 依赖:`npm install -D rehype-highlight@^7 highlight.js@^11`(移到 dependencies——运行时用;highlight.js CSS 主题在 NoteMarkdown 里 import)

---

### Task 1: toggleTaskItem 纯函数

**Files:**
- Create: `frontend/components/Inspiration/toggleTaskItem.ts`
- Test: `frontend/components/Inspiration/toggleTaskItem.test.ts`

**Interfaces:**
- Produces: `toggleTaskItem(md: string, index: number): string` — 翻转文档顺序第 index(0-based)个任务列表标记;index 越界返回原串不变

- [ ] **Step 1: 写失败测试**

```ts
import { describe, expect, it } from 'vitest';
import { toggleTaskItem } from './toggleTaskItem';

describe('toggleTaskItem', () => {
  it('checks an unchecked item', () => {
    expect(toggleTaskItem('- [ ] buy milk', 0)).toBe('- [x] buy milk');
  });

  it('unchecks a checked item', () => {
    expect(toggleTaskItem('- [x] done', 0)).toBe('- [ ] done');
  });

  it('toggles the Nth item in document order, leaving others', () => {
    const md = '- [ ] a\n- [ ] b\n- [ ] c';
    expect(toggleTaskItem(md, 1)).toBe('- [ ] a\n- [x] b\n- [ ] c');
  });

  it('handles *, + and indented markers', () => {
    expect(toggleTaskItem('  * [ ] nested', 0)).toBe('  * [x] nested');
    expect(toggleTaskItem('+ [ ] plus', 0)).toBe('+ [x] plus');
  });

  it('uppercase X counts as checked and toggles off', () => {
    expect(toggleTaskItem('- [X] up', 0)).toBe('- [ ] up');
  });

  it('does not count a bracket inside a fenced code block', () => {
    const md = 'real:\n- [ ] task\n```\n- [ ] fake in code\n```';
    // index 0 is the real task; index 1 must not exist → out of range returns unchanged
    expect(toggleTaskItem(md, 0)).toBe('real:\n- [x] task\n```\n- [ ] fake in code\n```');
    expect(toggleTaskItem(md, 1)).toBe(md);
  });

  it('out-of-range index returns the source unchanged', () => {
    expect(toggleTaskItem('- [ ] only', 5)).toBe('- [ ] only');
  });
});
```

- [ ] **Step 2: 跑测试确认失败**

Run: `npx vitest run components/Inspiration/toggleTaskItem.test.ts`
Expected: FAIL

- [ ] **Step 3: 实现**

```ts
// frontend/components/Inspiration/toggleTaskItem.ts
// Flip the Nth (document-order, 0-based) GFM task-list checkbox in markdown
// source. Fenced code blocks are excluded so a `- [ ]` shown as a code sample
// isn't counted. Contract mirrored by NoteMarkdown's checkbox index counter.
const FENCE = /```[\s\S]*?```/g;
const TASK = /^(\s*[-*+] \[)([ xX])(\])/gm;

export function toggleTaskItem(md: string, index: number): string {
  // Mask fenced code so task markers inside it are neither counted nor edited,
  // but keep exact character offsets by replacing with same-length filler.
  const masked = md.replace(FENCE, (m) => ' '.repeat(m.length));
  let seen = -1;
  let hit: { start: number; ch: string } | null = null;
  for (const m of masked.matchAll(TASK)) {
    seen += 1;
    if (seen === index) {
      // offset of the [ ]/[x] char = match index + length of group 1
      hit = { start: (m.index ?? 0) + m[1].length, ch: m[2] };
      break;
    }
  }
  if (!hit) return md;
  const next = hit.ch === ' ' ? 'x' : ' ';
  return md.slice(0, hit.start) + next + md.slice(hit.start + 1);
}
```

- [ ] **Step 4: 跑测试确认全过**

Run: `npx vitest run components/Inspiration/toggleTaskItem.test.ts`
Expected: 7 passed

- [ ] **Step 5: Commit**

```bash
git add frontend/components/Inspiration/toggleTaskItem.ts frontend/components/Inspiration/toggleTaskItem.test.ts
git commit -m "feat(inspiration): toggleTaskItem — flip Nth task checkbox in md source"
```

---

### Task 2: NoteMarkdown 组件(代码高亮 + 交互 checkbox)

**Files:**
- Modify: `frontend/package.json`(加 rehype-highlight + highlight.js 到 dependencies)
- Create: `frontend/components/Inspiration/NoteMarkdown.tsx`
- Test: `frontend/components/Inspiration/NoteMarkdown.test.tsx`

**Interfaces:**
- Consumes: react-markdown/remark-gfm/remark-breaks(已有)、rehype-highlight(新)、highlight.js CSS
- Produces:
```tsx
<NoteMarkdown source={string} onToggleTask?={(index: number) => void} />
```
渲染 Markdown(gfm 表格/删除线/任务清单 + 代码块 rehype-highlight 高亮);任务清单 checkbox **可点击**(非 disabled),点击第 N 个 → `onToggleTask(N)`(N 按文档顺序,与 toggleTaskItem 契约一致);无 onToggleTask 时 checkbox 只读(disabled)。

- [ ] **Step 1: 装依赖**

Run: `npm install rehype-highlight@^7 highlight.js@^11`
确认 package.json 的 dependencies 出现两者;package-lock 更新。

- [ ] **Step 2: 写失败测试**

```tsx
import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import { NoteMarkdown } from './NoteMarkdown';

describe('NoteMarkdown', () => {
  it('renders gfm task lists as checkboxes', () => {
    const { container } = render(<NoteMarkdown source={'- [ ] todo\n- [x] done'} />);
    const boxes = container.querySelectorAll('input[type="checkbox"]');
    expect(boxes.length).toBe(2);
    expect((boxes[1] as HTMLInputElement).checked).toBe(true);
  });

  it('checkboxes are disabled when no onToggleTask is given', () => {
    const { container } = render(<NoteMarkdown source={'- [ ] todo'} />);
    expect((container.querySelector('input[type="checkbox"]') as HTMLInputElement).disabled).toBe(true);
  });

  it('clicking the Nth checkbox calls onToggleTask with its document index', () => {
    const onToggleTask = vi.fn();
    const { container } = render(
      <NoteMarkdown source={'- [ ] a\n- [ ] b\n- [ ] c'} onToggleTask={onToggleTask} />,
    );
    const boxes = container.querySelectorAll('input[type="checkbox"]');
    fireEvent.click(boxes[2]);
    expect(onToggleTask).toHaveBeenCalledWith(2);
  });

  it('renders fenced code with a language class for highlighting', () => {
    const { container } = render(<NoteMarkdown source={'```js\nconst x = 1;\n```'} />);
    // rehype-highlight adds hljs + language-* classes onto the <code>
    expect(container.querySelector('code.hljs, code[class*="language-"]')).toBeTruthy();
  });

  it('renders a gfm table', () => {
    render(<NoteMarkdown source={'| a | b |\n|---|---|\n| 1 | 2 |'} />);
    expect(screen.getByRole('table')).toBeTruthy();
  });
});
```

- [ ] **Step 3: 跑测试确认失败**

Run: `npx vitest run components/Inspiration/NoteMarkdown.test.tsx`
Expected: FAIL

- [ ] **Step 4: 实现**

```tsx
// frontend/components/Inspiration/NoteMarkdown.tsx
// Notes-only rich markdown renderer (spec §2.2 #5b). Adds syntax-highlighted
// code (rehype-highlight) and interactive task-list checkboxes on top of the
// GFM tables/strikethrough react-markdown already gives us. Kept separate from
// the shared AILibrary/MarkdownBody so AI-chat rendering is untouched.
import React, { useRef } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import remarkBreaks from 'remark-breaks';
import rehypeHighlight from 'rehype-highlight';
import 'highlight.js/styles/github-dark.css';

interface Props {
  source: string;
  /** When given, task-list checkboxes become clickable and report their
   *  document-order index; without it they render read-only. */
  onToggleTask?: (index: number) => void;
}

export const NoteMarkdown: React.FC<Props> = ({ source, onToggleTask }) => {
  // Assign each task checkbox its document-order index. react-markdown renders
  // synchronously in document order within one pass, so a per-render counter
  // (reset here, incremented as each checkbox renders) yields stable indices
  // matching toggleTaskItem's contract.
  const counter = useRef(0);
  counter.current = 0;

  return (
    <div className="text-[13.5px] leading-relaxed text-content markdown-note">
      <ReactMarkdown
        remarkPlugins={[remarkGfm, remarkBreaks]}
        rehypePlugins={[[rehypeHighlight, { detect: true, ignoreMissing: true }]]}
        components={{
          input: ({ type, checked, ...rest }) => {
            if (type !== 'checkbox') return <input type={type} {...rest} />;
            const idx = counter.current;
            counter.current += 1;
            return (
              <input
                type="checkbox"
                checked={!!checked}
                disabled={!onToggleTask}
                onChange={() => onToggleTask?.(idx)}
                className="mr-1.5 align-middle accent-indigo-500"
              />
            );
          },
          a: ({ children, href }) => (
            <a href={href} target="_blank" rel="noreferrer" className="text-indigo-300 hover:underline">
              {children}
            </a>
          ),
          pre: ({ children }) => (
            <pre className="my-2 overflow-x-auto rounded-lg bg-island-2 p-3 text-[12px]">{children}</pre>
          ),
          code: ({ className, children, ...rest }) => (
            <code className={`${className ?? ''} rounded bg-island-2 px-1 py-0.5 text-[12px]`} {...rest}>
              {children}
            </code>
          ),
          table: ({ children }) => (
            <div className="my-2 overflow-x-auto">
              <table className="w-full border-collapse text-[12.5px]">{children}</table>
            </div>
          ),
          th: ({ children }) => <th className="border border-line px-2 py-1 text-left font-semibold">{children}</th>,
          td: ({ children }) => <td className="border border-line px-2 py-1">{children}</td>,
          ul: ({ children }) => <ul className="my-1 list-disc pl-5">{children}</ul>,
          ol: ({ children }) => <ol className="my-1 list-decimal pl-5">{children}</ol>,
          p: ({ children }) => <p className="my-1">{children}</p>,
        }}
      >
        {source}
      </ReactMarkdown>
    </div>
  );
};
```
**注意**:`code` 组件同时处理块级(带 language 类,rehype-highlight 已注入 hljs 类)与行内(无 language 类)——上面统一给 bg,块级在 `pre` 里会双重 bg,可接受(或按 className 含 `language-`/`hljs` 时不加行内 bg;实现时若视觉丑,按 className 判断分流,但测试只断言 hljs 类存在)。task-list 的 `<li>` 默认会带 disc,gfm 的 task item 需要去掉 marker——若视觉有 bullet+checkbox 双重,给 `li` 组件按 `className?.includes('task-list-item')` 加 `list-none`;非必须,测试不覆盖。

- [ ] **Step 5: 跑测试确认全过**

Run: `npx vitest run components/Inspiration/NoteMarkdown.test.tsx`
Expected: 5 passed。若 rehype-highlight 在 jsdom 下对某语言告警,`ignoreMissing: true` 已兜;`detect: true` 让无语言块也尝试检测。若第 4 条(hljs 类)因 jsdom 不跑高亮而挂,放宽断言为 `code[class*="language-"]` 存在(rehype-highlight 至少保留 language 类)——这是允许的测试微调(渲染环境事实)。

- [ ] **Step 6: Commit**

```bash
git add frontend/package.json frontend/package-lock.json frontend/components/Inspiration/NoteMarkdown.tsx frontend/components/Inspiration/NoteMarkdown.test.tsx
git commit -m "feat(inspiration): NoteMarkdown — code highlighting + interactive checkboxes"
```

---

### Task 3: NoteCard 接 NoteMarkdown + checkbox 回写闭环

**Files:**
- Modify: `frontend/components/Inspiration/NoteCard.tsx`(MarkdownBody → NoteMarkdown,加 onToggleTask 透传)
- Modify: `frontend/pages/InspirationPage.tsx`(处理 checkbox 切换 → toggleTaskItem → updateNote 乐观)
- Test: `frontend/components/Inspiration/NoteCard.checkbox.test.tsx`

**Interfaces:**
- Consumes: Task 1 `toggleTaskItem`、Task 2 `NoteMarkdown`
- Produces: NoteCard 新增可选 prop `onToggleTask?: (note: InspirationNote, index: number) => void`,把 NoteMarkdown 的 `onToggleTask(index)` 包成 `onToggleTask(note, index)`;InspirationPage 实现该 handler:`updateNote(note.id, { content_md: toggleTaskItem(note.content_md, index) })` 乐观原位替换,失败 toast。

- [ ] **Step 1: 写失败测试**(NoteCard 层)

```tsx
import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render } from '@testing-library/react';

vi.mock('../../services/inspirationService', () => ({ attachmentUrlWithToken: (id: string) => `u/${id}` }));
vi.mock('../../contexts/AuthContext', () => ({ useAuth: () => ({ mediaToken: 'tok' }) }));
vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: (_k: string, f: string) => f }) }));

import { NoteCard } from './NoteCard';
import type { InspirationNote } from '../../services/inspirationService';

const note = (over: Partial<InspirationNote> = {}): InspirationNote => ({
  id: '1', content_md: '- [ ] a\n- [ ] b', tags: [], ref_hotspot: null, pinned: false,
  note_date: '2026-07-07', created_at: '2026-07-07T09:42:00+00:00',
  updated_at: '2026-07-07T09:42:00+00:00', attachments: [], ...over,
});

describe('NoteCard checkbox toggle', () => {
  it('clicking a task checkbox reports (note, index)', () => {
    const onToggleTask = vi.fn();
    const { container } = render(
      <NoteCard note={note()} onEdit={vi.fn()} onTogglePin={vi.fn()} onDelete={vi.fn()} onTagClick={vi.fn()} onToggleTask={onToggleTask} />,
    );
    const boxes = container.querySelectorAll('input[type="checkbox"]');
    fireEvent.click(boxes[1]);
    expect(onToggleTask).toHaveBeenCalledWith(expect.objectContaining({ id: '1' }), 1);
  });
});
```

- [ ] **Step 2: 跑测试确认失败**

Run: `npx vitest run components/Inspiration/NoteCard.checkbox.test.tsx`
Expected: FAIL(NoteCard 还用 MarkdownBody,无 onToggleTask)

- [ ] **Step 3: 实现**

NoteCard.tsx:
- import 改 `import { NoteMarkdown } from './NoteMarkdown';`(删 MarkdownBody import)
- Props interface 加 `onToggleTask?: (note: InspirationNote, index: number) => void;`
- 渲染处 `<MarkdownBody source={note.content_md} />` → `<NoteMarkdown source={note.content_md} onToggleTask={onToggleTask ? (index) => onToggleTask(note, index) : undefined} />`

InspirationPage.tsx:
- import `toggleTaskItem`
- 加 handler:
```tsx
const onToggleTask = async (note: InspirationNote, index: number) => {
  const nextMd = toggleTaskItem(note.content_md, index);
  if (nextMd === note.content_md) return;
  setNotes((prev) => prev.map((n) => (n.id === note.id ? { ...n, content_md: nextMd } : n)));
  try {
    const updated = await updateNote(note.id, { content_md: nextMd });
    setNotes((prev) => prev.map((n) => (n.id === note.id ? updated : n)));
  } catch (err) {
    setNotes((prev) => prev.map((n) => (n.id === note.id ? note : n))); // revert
    addToast((err as Error).message, 'error');
  }
};
```
- NoteTimeline 挂载处透传 `onToggleTask`(NoteTimeline 也要加透传 prop)。**注意 NoteTimeline.tsx 需加 `onToggleTask?` 到 props 并透传给 NoteCard**——这是第三个改动文件,一并做。

- [ ] **Step 4: 跑测试 + 回归**

Run: `npx vitest run components/Inspiration/NoteCard.checkbox.test.tsx` → 1 passed
Run: `npx vitest run components/Inspiration/NoteCard.test.tsx components/Inspiration/NoteTimeline*.test.tsx pages/InspirationPage.test.tsx` → P2/P3 既有零回归(NoteCard 既有测试若 mock 了 MarkdownBody,改成 mock NoteMarkdown 或让它真渲染——按实际最小改)

- [ ] **Step 5: Commit**

```bash
git add frontend/components/Inspiration/NoteCard.tsx frontend/components/Inspiration/NoteTimeline.tsx frontend/pages/InspirationPage.tsx frontend/components/Inspiration/NoteCard.checkbox.test.tsx
git commit -m "feat(inspiration): interactive checkbox toggle persists to the note"
```

---

### Task 4: 收口 — 全量验证 + PR

- [ ] **Step 1**: `cd frontend && npm run lint && npm run build && npx vitest run 2>&1 | tail -3` 全绿(NoteCard 既有测试改动确认无回归;highlight.js CSS import 不炸 build)
- [ ] **Step 2**: 合并最新 master(`git fetch origin && git merge origin/master --no-edit`),重跑全量 vitest 确认无回归
- [ ] **Step 3**: `git push -u origin feature/inspiration-p4-rich-md`,`gh pr create` 标题 `feat(inspiration): P4a rich markdown — code highlighting + interactive checkboxes (flag-dark)`,body 说明 flag off 零变化、不改共享 MarkdownBody、KaTeX/PAT 留后续、测试数
- [ ] **Step 4**: CI 全绿后单独 `gh pr merge --squash --delete-branch`
