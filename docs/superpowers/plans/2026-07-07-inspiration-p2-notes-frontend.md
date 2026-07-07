# Inspiration Notes P2 — 前端 Notes 主体 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 灵感笔记页面(flag 暗启动):composer 快记 + 按日分组 timeline + 四型附件渲染 + 热力图⇄月历侧栏 + 标签面板 + 过滤 chips。P3 才做热点融合;本阶段旧页面在 flag off 时原样保留。

**Architecture:** 新组件树 `components/Inspiration/*` + `pages/InspirationPage.tsx`,由现有 `TopicInspirationPage` 顶部按 flag 分支渲染(ScriptEditor/index.tsx 模式)。API 走新 `services/inspirationService.ts`(P1 已上线的 `/api/v1/inspiration/*`)。MD 渲染复用现成 `components/AILibrary/MarkdownBody.tsx`。

**Tech Stack:** React 19 + TS + Tailwind(ink/island token)+ react-markdown(经 MarkdownBody)+ i18next + vitest/@testing-library。

**Spec:** `docs/superpowers/specs/2026-07-07-inspiration-notes-design.md` §2/§5。
**与 spec 的两处刻意收窄(P4 再补,不是遗漏):** ①timeline 用 keyset load-more,不上虚拟化(个人快记量级用不到;资源库虚拟化是 10 万行场景);②正文内 inline #tag 高亮降级为"卡片底部 tags chip 行"(inline 高亮需要自定义 text-node 渲染,归 P4 polish)。

## Global Constraints

- 工作目录:本 worktree(分支 `feature/inspiration-p2-notes`);全部命令在 `frontend/` 下执行
- flag:`VITE_FEATURE_INSPIRATION_NOTES === 'true'` 才渲染新页;off 时 `TopicInspirationPage` 行为逐字节不变
- UI 文本全英文走 i18n:`t('inspiration.<key>', '<English fallback>')`;en.json+zh.json 同步;**零 emoji 图标**(lucide);**禁 zinc 类**(用 ink/island/content/line token 类)
- API 类型:所有 id 是 **string**(后端 coerce_numbers_to_str);错误处理用 `jsonOrThrow` 模式 + `addToast(msg,'error')`,绝不 `catch {}` 静默
- #tag 解析契约必须镜像 `backend/app/services/inspiration/note_tags.py`(fence/inline-code 剥离、`#` 前导=行首/空白/左括号、`# `非 tag、CJK 允许、ASCII 小写、去重保序)
- 测试文件与源码同目录(仓库惯例);mock 用 `vi.mock('../../services/inspirationService', ...)`
- 每个 task 结束:`npx vitest run <本任务测试文件> --reporter=default` 过 + commit;收口 task 跑 `npm run lint`(rules-of-hooks 阻塞)/ `npm run build` / 全量 vitest
- 日期一律 `YYYY-MM-DD` 字符串在组件间传递;显示用 `utils/formatDate.ts` 的 `formatDateShort`

---

### Task 1: 类型 + inspirationService

**Files:**
- Create: `frontend/services/inspirationService.ts`
- Test: `frontend/services/inspirationService.test.ts`

**Interfaces:**
- Produces(后续所有任务消费):
```ts
export interface NoteAttachment { id: string; mime: string; size_bytes: number; original_name: string; }
export interface RefHotspot { hotspot_id?: string; title: string; source?: string; heat?: number; url?: string; captured_at?: string; }
export interface InspirationNote {
  id: string; content_md: string; tags: string[]; ref_hotspot: RefHotspot | null;
  pinned: boolean; note_date: string; created_at: string; updated_at: string;
  attachments: NoteAttachment[];
}
export interface NoteFilters { date?: string; tag?: string; q?: string; }
export async function listNotes(filters: NoteFilters, limit?: number, beforeId?: string): Promise<InspirationNote[]>
export async function createNote(contentMd: string, refHotspot?: RefHotspot): Promise<InspirationNote>
export async function updateNote(id: string, patch: { content_md?: string; pinned?: boolean }): Promise<InspirationNote>
export async function deleteNote(id: string): Promise<void>
export async function getActivity(dateFrom: string, dateTo: string): Promise<{ day: string; cnt: number }[]>
export async function getTagCounts(): Promise<{ tag: string; cnt: number }[]>
export async function uploadAttachment(noteId: string, file: File): Promise<NoteAttachment>
export function attachmentUrl(attachmentId: string): string  // GET 端点地址(302 签名),供 <img src>/<a href>
```

- [ ] **Step 1: 写失败测试**(mock 全局 fetch,断言 URL/method/headers/body 形状与返回解析)

```ts
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('./parserService', () => ({
  getAuthHeaders: vi.fn().mockResolvedValue({ Authorization: 'Bearer test-token' }),
}));
vi.mock('../utils/apiConfig', () => ({ getApiUrl: () => 'http://api.test' }));

import {
  attachmentUrl,
  createNote,
  deleteNote,
  getActivity,
  listNotes,
  updateNote,
  uploadAttachment,
} from './inspirationService';

const okJson = (data: unknown) =>
  ({ ok: true, status: 200, json: async () => data }) as Response;

describe('inspirationService', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(okJson([])));
  });
  afterEach(() => vi.unstubAllGlobals());

  it('listNotes composes query params and auth header', async () => {
    await listNotes({ date: '2026-07-07', tag: 'hooks', q: 'ferry' }, 20, '99');
    const [url, init] = (fetch as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(url).toContain('http://api.test/api/v1/inspiration/notes?');
    expect(url).toContain('date=2026-07-07');
    expect(url).toContain('tag=hooks');
    expect(url).toContain('q=ferry');
    expect(url).toContain('limit=20');
    expect(url).toContain('before_id=99');
    expect(init.headers.Authorization).toBe('Bearer test-token');
  });

  it('createNote POSTs content and optional ref_hotspot', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(okJson({ id: '1', attachments: [] })));
    await createNote('idea #x', { title: 'Hot', source: 'DOUYIN' });
    const [url, init] = (fetch as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(url).toBe('http://api.test/api/v1/inspiration/notes');
    expect(init.method).toBe('POST');
    const body = JSON.parse(init.body);
    expect(body.content_md).toBe('idea #x');
    expect(body.ref_hotspot.title).toBe('Hot');
  });

  it('updateNote PATCHes only provided fields', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(okJson({ id: '1' })));
    await updateNote('1', { pinned: true });
    const [, init] = (fetch as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(init.method).toBe('PATCH');
    expect(JSON.parse(init.body)).toEqual({ pinned: true });
  });

  it('deleteNote sends DELETE and tolerates empty body', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, status: 204 } as Response));
    await expect(deleteNote('9')).resolves.toBeUndefined();
  });

  it('non-ok response throws detail message', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({
        ok: false,
        status: 413,
        json: async () => ({ detail: 'file exceeds the 500 MB attachment limit' }),
      } as Response),
    );
    await expect(createNote('x')).rejects.toThrow('file exceeds the 500 MB attachment limit');
  });

  it('getActivity hits /notes/activity with range', async () => {
    await getActivity('2026-04-01', '2026-07-07');
    const [url] = (fetch as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(url).toContain('/api/v1/inspiration/notes/activity?date_from=2026-04-01&date_to=2026-07-07');
  });

  it('uploadAttachment posts FormData without manual Content-Type', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(okJson({ id: 'a1' })));
    const file = new File(['x'], 'pic.png', { type: 'image/png' });
    await uploadAttachment('42', file);
    const [url, init] = (fetch as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(url).toBe('http://api.test/api/v1/inspiration/attachments/upload?note_id=42');
    expect(init.body).toBeInstanceOf(FormData);
    expect(init.headers['Content-Type']).toBeUndefined();
  });

  it('attachmentUrl builds the signed-get endpoint', () => {
    expect(attachmentUrl('a1')).toBe('http://api.test/api/v1/inspiration/attachments/a1');
  });
});
```

- [ ] **Step 2: 跑测试确认失败**

Run: `npx vitest run services/inspirationService.test.ts`
Expected: FAIL — 模块不存在

- [ ] **Step 3: 实现**

```ts
// frontend/services/inspirationService.ts
// API layer for /api/v1/inspiration/* (P1 backend, PR #1137).
// All ids are strings — the backend serializes bigints via coerce_numbers_to_str.
import { getAuthHeaders } from './parserService';
import { getApiUrl } from '../utils/apiConfig';

export interface NoteAttachment {
  id: string;
  mime: string;
  size_bytes: number;
  original_name: string;
}

export interface RefHotspot {
  hotspot_id?: string;
  title: string;
  source?: string;
  heat?: number;
  url?: string;
  captured_at?: string;
}

export interface InspirationNote {
  id: string;
  content_md: string;
  tags: string[];
  ref_hotspot: RefHotspot | null;
  pinned: boolean;
  note_date: string;
  created_at: string;
  updated_at: string;
  attachments: NoteAttachment[];
}

export interface NoteFilters {
  date?: string;
  tag?: string;
  q?: string;
}

const base = () => `${getApiUrl()}/api/v1/inspiration`;

async function jsonOrThrow(resp: Response) {
  if (!resp.ok) {
    const e = await resp.json().catch(() => ({ detail: 'Request failed' }));
    throw new Error(e.detail || e.error || `HTTP ${resp.status}`);
  }
  return resp.json();
}

export async function listNotes(
  filters: NoteFilters,
  limit = 50,
  beforeId?: string,
): Promise<InspirationNote[]> {
  const params = new URLSearchParams();
  if (filters.date) params.set('date', filters.date);
  if (filters.tag) params.set('tag', filters.tag);
  if (filters.q) params.set('q', filters.q);
  params.set('limit', String(limit));
  if (beforeId) params.set('before_id', beforeId);
  const resp = await fetch(`${base()}/notes?${params.toString()}`, {
    headers: await getAuthHeaders(),
  });
  return jsonOrThrow(resp);
}

export async function createNote(
  contentMd: string,
  refHotspot?: RefHotspot,
): Promise<InspirationNote> {
  const body: Record<string, unknown> = { content_md: contentMd };
  if (refHotspot) body.ref_hotspot = refHotspot;
  const resp = await fetch(`${base()}/notes`, {
    method: 'POST',
    headers: { ...(await getAuthHeaders()), 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  return jsonOrThrow(resp);
}

export async function updateNote(
  id: string,
  patch: { content_md?: string; pinned?: boolean },
): Promise<InspirationNote> {
  const resp = await fetch(`${base()}/notes/${id}`, {
    method: 'PATCH',
    headers: { ...(await getAuthHeaders()), 'Content-Type': 'application/json' },
    body: JSON.stringify(patch),
  });
  return jsonOrThrow(resp);
}

export async function deleteNote(id: string): Promise<void> {
  const resp = await fetch(`${base()}/notes/${id}`, {
    method: 'DELETE',
    headers: await getAuthHeaders(),
  });
  if (!resp.ok) {
    const e = await resp.json().catch(() => ({ detail: 'Delete failed' }));
    throw new Error(e.detail || `HTTP ${resp.status}`);
  }
}

export async function getActivity(
  dateFrom: string,
  dateTo: string,
): Promise<{ day: string; cnt: number }[]> {
  const resp = await fetch(
    `${base()}/notes/activity?date_from=${dateFrom}&date_to=${dateTo}`,
    { headers: await getAuthHeaders() },
  );
  return jsonOrThrow(resp);
}

export async function getTagCounts(): Promise<{ tag: string; cnt: number }[]> {
  const resp = await fetch(`${base()}/notes/tags`, {
    headers: await getAuthHeaders(),
  });
  return jsonOrThrow(resp);
}

export async function uploadAttachment(
  noteId: string,
  file: File,
): Promise<NoteAttachment> {
  const headers = { ...(await getAuthHeaders()) } as Record<string, string>;
  delete headers['Content-Type']; // browser sets the multipart boundary
  const form = new FormData();
  form.append('file', file);
  const resp = await fetch(`${base()}/attachments/upload?note_id=${noteId}`, {
    method: 'POST',
    headers,
    body: form,
  });
  return jsonOrThrow(resp);
}

export async function deleteAttachment(attachmentId: string): Promise<void> {
  const resp = await fetch(`${base()}/attachments/${attachmentId}`, {
    method: 'DELETE',
    headers: await getAuthHeaders(),
  });
  if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
}

export function attachmentUrl(attachmentId: string): string {
  return `${base()}/attachments/${attachmentId}`;
}
```

- [ ] **Step 4: 跑测试确认全过**

Run: `npx vitest run services/inspirationService.test.ts`
Expected: 8 passed

- [ ] **Step 5: Commit**

```bash
git add frontend/services/inspirationService.ts frontend/services/inspirationService.test.ts
git commit -m "feat(inspiration): frontend service layer for /api/v1/inspiration"
```

---

### Task 2: noteTags 镜像解析器

**Files:**
- Create: `frontend/components/Inspiration/noteTags.ts`
- Test: `frontend/components/Inspiration/noteTags.test.ts`

**Interfaces:**
- Produces: `parseTags(contentMd: string): string[]`(Composer 实时预览用)与 `TAG_TOKEN_RE`(autocomplete 定位当前正在输入的 tag 用:`findActiveTag(text: string, caret: number): { start: number; prefix: string } | null`)
- 契约镜像 `backend/app/services/inspiration/note_tags.py`(实现前先 Read 它,规则以它为准)

- [ ] **Step 1: 写失败测试**(与后端 7 条测试逐一对应 + findActiveTag 3 条)

```ts
import { describe, expect, it } from 'vitest';
import { findActiveTag, parseTags } from './noteTags';

describe('parseTags — mirrors backend note_tags.py contract', () => {
  it('extracts in order', () => {
    expect(parseTags('idea #hooks and #formats now')).toEqual(['hooks', 'formats']);
  });
  it('dedups case-insensitively to lowercase', () => {
    expect(parseTags('#Hooks #hooks #HOOKS')).toEqual(['hooks']);
  });
  it('allows cjk, hyphen, underscore', () => {
    expect(parseTags('试试 #灵感 #short-form #a_b')).toEqual(['灵感', 'short-form', 'a_b']);
  });
  it('strips trailing punctuation via char-class boundary', () => {
    expect(parseTags('end #hooks. and (#formats)')).toEqual(['hooks', 'formats']);
  });
  it('ignores mid-word and url fragments', () => {
    expect(parseTags('c# is a language, see x.com/a#b')).toEqual([]);
  });
  it('ignores code fences and inline code', () => {
    const md = 'text #real\n```\n# comment not a tag\nfoo #fake\n```\n`inline #fake2`';
    expect(parseTags(md)).toEqual(['real']);
  });
  it('empty and markdown headings are not tags', () => {
    expect(parseTags('')).toEqual([]);
    expect(parseTags('# heading text')).toEqual([]);
  });
});

describe('findActiveTag — caret sits inside a #token being typed', () => {
  it('returns prefix while typing', () => {
    const text = 'idea #hoo';
    expect(findActiveTag(text, text.length)).toEqual({ start: 5, prefix: 'hoo' });
  });
  it('returns null when caret after space', () => {
    const text = 'idea #hooks ';
    expect(findActiveTag(text, text.length)).toBeNull();
  });
  it('returns null with no hash token', () => {
    expect(findActiveTag('plain', 5)).toBeNull();
  });
});
```

- [ ] **Step 2: 跑测试确认失败**

Run: `npx vitest run components/Inspiration/noteTags.test.ts`
Expected: FAIL — 模块不存在

- [ ] **Step 3: 实现**(先 Read 后端 `backend/app/services/inspiration/note_tags.py` 对齐正则语义)

```ts
// frontend/components/Inspiration/noteTags.ts
// Mirror of backend/app/services/inspiration/note_tags.py — keep the two in
// lockstep (change one → change the other → update the spec §2.4).
// Rules: '#' preceded by start/whitespace/'('/'（'; body = unicode letters,
// digits, '-', '_'; '# ' (heading) is not a tag; fenced/inline code stripped;
// output lowercased (ASCII), deduped, first-seen order.

const CODE_FENCE = /```[\s\S]*?```/g;
const INLINE_CODE = /`[^`\n]*`/g;
const TAG = /(^|[\s(（])#([\p{L}\p{N}_-]+)/gu;

export function parseTags(contentMd: string): string[] {
  if (!contentMd) return [];
  const text = contentMd.replace(CODE_FENCE, ' ').replace(INLINE_CODE, ' ');
  const seen = new Set<string>();
  const out: string[] = [];
  for (const match of text.matchAll(TAG)) {
    const tag = match[2].toLowerCase();
    if (tag && !seen.has(tag)) {
      seen.add(tag);
      out.push(tag);
    }
  }
  return out;
}

/** While typing in the composer: the #token the caret is currently inside. */
export function findActiveTag(
  text: string,
  caret: number,
): { start: number; prefix: string } | null {
  const upto = text.slice(0, caret);
  const hash = upto.lastIndexOf('#');
  if (hash === -1) return null;
  const before = hash === 0 ? '' : upto[hash - 1];
  if (before && !/[\s(（]/.test(before)) return null;
  const token = upto.slice(hash + 1);
  if (token.length === 0) return { start: hash, prefix: '' };
  if (!/^[\p{L}\p{N}_-]+$/u.test(token)) return null;
  return { start: hash, prefix: token.toLowerCase() };
}
```

- [ ] **Step 4: 跑测试确认全过**

Run: `npx vitest run components/Inspiration/noteTags.test.ts`
Expected: 10 passed

- [ ] **Step 5: Commit**

```bash
git add frontend/components/Inspiration/noteTags.ts frontend/components/Inspiration/noteTags.test.ts
git commit -m "feat(inspiration): frontend #tag parser mirroring backend contract"
```

---

### Task 3: AttachmentView 四型渲染

**Files:**
- Create: `frontend/components/Inspiration/AttachmentView.tsx`
- Test: `frontend/components/Inspiration/AttachmentView.test.tsx`

**Interfaces:**
- Consumes: Task 1 `NoteAttachment` / `attachmentUrl`
- Produces: `<AttachmentView attachments={NoteAttachment[]} />`(image→缩略图格,点开新标签;audio→原生 `<audio controls>`;video→`<video controls>` 卡;其余→带扩展名徽标的下载 chip)

- [ ] **Step 1: 写失败测试**

```tsx
import { describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';

vi.mock('../../services/inspirationService', () => ({
  attachmentUrl: (id: string) => `http://api.test/att/${id}`,
}));

import { AttachmentView } from './AttachmentView';

const att = (id: string, mime: string, name: string) => ({
  id,
  mime,
  size_bytes: 1024,
  original_name: name,
});

describe('AttachmentView', () => {
  it('renders nothing for empty list', () => {
    const { container } = render(<AttachmentView attachments={[]} />);
    expect(container.firstChild).toBeNull();
  });

  it('renders images as thumbnails linking to source', () => {
    render(<AttachmentView attachments={[att('1', 'image/png', 'pic.png')]} />);
    const img = screen.getByRole('img');
    expect(img.getAttribute('src')).toBe('http://api.test/att/1');
  });

  it('renders audio with native controls', () => {
    const { container } = render(
      <AttachmentView attachments={[att('2', 'audio/mpeg', 'memo.mp3')]} />,
    );
    expect(container.querySelector('audio')).not.toBeNull();
  });

  it('renders video with native controls', () => {
    const { container } = render(
      <AttachmentView attachments={[att('3', 'video/mp4', 'clip.mp4')]} />,
    );
    expect(container.querySelector('video')).not.toBeNull();
  });

  it('renders other files as a download chip with name and size', () => {
    render(
      <AttachmentView attachments={[att('4', 'application/pdf', 'report.pdf')]} />,
    );
    expect(screen.getByText('report.pdf')).toBeTruthy();
    expect(screen.getByText(/1(\.0)? KB/)).toBeTruthy();
  });
});
```

- [ ] **Step 2: 跑测试确认失败**

Run: `npx vitest run components/Inspiration/AttachmentView.test.tsx`
Expected: FAIL

- [ ] **Step 3: 实现**

```tsx
// frontend/components/Inspiration/AttachmentView.tsx
// Renders a note's attachments by mime family (spec §2.2 #5):
// image grid / inline audio / video card / typed download chip.
import React from 'react';
import { FileText } from 'lucide-react';
import {
  attachmentUrl,
  type NoteAttachment,
} from '../../services/inspirationService';

function formatSize(bytes: number): string {
  if (bytes >= 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  if (bytes >= 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${bytes} B`;
}

function extOf(name: string): string {
  const dot = name.lastIndexOf('.');
  return dot > 0 ? name.slice(dot + 1).toUpperCase().slice(0, 5) : 'FILE';
}

export const AttachmentView: React.FC<{ attachments: NoteAttachment[] }> = ({
  attachments,
}) => {
  if (!attachments.length) return null;
  const images = attachments.filter((a) => a.mime.startsWith('image/'));
  const audios = attachments.filter((a) => a.mime.startsWith('audio/'));
  const videos = attachments.filter((a) => a.mime.startsWith('video/'));
  const files = attachments.filter(
    (a) =>
      !a.mime.startsWith('image/') &&
      !a.mime.startsWith('audio/') &&
      !a.mime.startsWith('video/'),
  );

  return (
    <div className="mt-2 space-y-2">
      {images.length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          {images.map((a) => (
            <a
              key={a.id}
              href={attachmentUrl(a.id)}
              target="_blank"
              rel="noreferrer"
              className="block"
            >
              <img
                src={attachmentUrl(a.id)}
                alt={a.original_name}
                loading="lazy"
                className="h-24 w-32 rounded-lg object-cover bg-island-2"
              />
            </a>
          ))}
        </div>
      )}
      {videos.map((a) => (
        <video
          key={a.id}
          src={attachmentUrl(a.id)}
          controls
          preload="metadata"
          className="max-h-64 rounded-lg bg-island-2"
        />
      ))}
      {audios.map((a) => (
        <audio key={a.id} src={attachmentUrl(a.id)} controls className="h-9 w-full max-w-xs" />
      ))}
      {files.length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          {files.map((a) => (
            <a
              key={a.id}
              href={attachmentUrl(a.id)}
              target="_blank"
              rel="noreferrer"
              className="inline-flex items-center gap-2 rounded-lg bg-island-2 px-3 py-1.5 text-xs text-content-2 hover:bg-line"
            >
              <FileText size={14} className="text-content-3" />
              <span className="max-w-[180px] truncate">{a.original_name}</span>
              <span className="rounded bg-indigo-500/15 px-1.5 py-0.5 text-[10px] font-bold text-indigo-300">
                {extOf(a.original_name)}
              </span>
              <span className="text-content-3">{formatSize(a.size_bytes)}</span>
            </a>
          ))}
        </div>
      )}
    </div>
  );
};
```

- [ ] **Step 4: 跑测试确认全过**

Run: `npx vitest run components/Inspiration/AttachmentView.test.tsx`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add frontend/components/Inspiration/AttachmentView.tsx frontend/components/Inspiration/AttachmentView.test.tsx
git commit -m "feat(inspiration): attachment renderer — image/audio/video/file chips"
```

---

### Task 4: NoteCard(MD 渲染 + tags + refcard + 菜单)

**Files:**
- Create: `frontend/components/Inspiration/NoteCard.tsx`
- Test: `frontend/components/Inspiration/NoteCard.test.tsx`

**Interfaces:**
- Consumes: Task 1 types、Task 3 `AttachmentView`、现有 `components/AILibrary/MarkdownBody.tsx`(default export,props `{ content: string }` — 实现前 Read 一眼确认 props 名)、`utils/formatDate.ts`
- Produces:
```tsx
<NoteCard note={InspirationNote}
  onEdit={(note) => void} onTogglePin={(note) => void} onDelete={(note) => void}
  onTagClick={(tag: string) => void} />
```
时间戳(HH:MM)+ pinned 图钉标记 + `···` 菜单(Edit/Pin/Delete,英文 i18n)+ MD 正文 + ref_hotspot 引用卡(来源徽标/标题/热度/链接)+ tags chip 行 + 附件区

- [ ] **Step 1: 写失败测试**

```tsx
import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';

vi.mock('../../services/inspirationService', () => ({
  attachmentUrl: (id: string) => `http://api.test/att/${id}`,
}));
vi.mock('../AILibrary/MarkdownBody', () => ({
  default: ({ content }: { content: string }) => <div data-testid="md">{content}</div>,
}));
vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (_k: string, fallback: string) => fallback }),
}));

import { NoteCard } from './NoteCard';
import type { InspirationNote } from '../../services/inspirationService';

const note = (over: Partial<InspirationNote> = {}): InspirationNote => ({
  id: '1',
  content_md: 'hello #hooks',
  tags: ['hooks'],
  ref_hotspot: null,
  pinned: false,
  note_date: '2026-07-07',
  created_at: '2026-07-07T09:42:00+00:00',
  updated_at: '2026-07-07T09:42:00+00:00',
  attachments: [],
  ...over,
});

describe('NoteCard', () => {
  it('renders markdown body and tag chips', () => {
    render(
      <NoteCard note={note()} onEdit={vi.fn()} onTogglePin={vi.fn()} onDelete={vi.fn()} onTagClick={vi.fn()} />,
    );
    expect(screen.getByTestId('md').textContent).toBe('hello #hooks');
    expect(screen.getByText('#hooks')).toBeTruthy();
  });

  it('tag chip click bubbles the tag', () => {
    const onTagClick = vi.fn();
    render(
      <NoteCard note={note()} onEdit={vi.fn()} onTogglePin={vi.fn()} onDelete={vi.fn()} onTagClick={onTagClick} />,
    );
    fireEvent.click(screen.getByText('#hooks'));
    expect(onTagClick).toHaveBeenCalledWith('hooks');
  });

  it('renders hotspot reference card when present', () => {
    render(
      <NoteCard
        note={note({ ref_hotspot: { title: 'Silent vlog passes 2.1B', source: 'DOUYIN', heat: 98.4, url: 'https://x' } })}
        onEdit={vi.fn()} onTogglePin={vi.fn()} onDelete={vi.fn()} onTagClick={vi.fn()}
      />,
    );
    expect(screen.getByText('Silent vlog passes 2.1B')).toBeTruthy();
    expect(screen.getByText('DOUYIN')).toBeTruthy();
  });

  it('menu exposes edit, pin, delete actions', () => {
    const onDelete = vi.fn();
    render(
      <NoteCard note={note()} onEdit={vi.fn()} onTogglePin={vi.fn()} onDelete={onDelete} onTagClick={vi.fn()} />,
    );
    fireEvent.click(screen.getByLabelText('Note actions'));
    fireEvent.click(screen.getByText('Delete'));
    expect(onDelete).toHaveBeenCalled();
  });
});
```

- [ ] **Step 2: 跑测试确认失败**

Run: `npx vitest run components/Inspiration/NoteCard.test.tsx`
Expected: FAIL

- [ ] **Step 3: 实现**(实现前 Read `components/AILibrary/MarkdownBody.tsx` 顶部确认 default export 与 props;若 props 不是 `content`,按实际改并同步 mock)

```tsx
// frontend/components/Inspiration/NoteCard.tsx
import React, { useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { ExternalLink, Flame, MoreHorizontal, Pin } from 'lucide-react';
import MarkdownBody from '../AILibrary/MarkdownBody';
import { AttachmentView } from './AttachmentView';
import type { InspirationNote } from '../../services/inspirationService';

interface Props {
  note: InspirationNote;
  onEdit: (note: InspirationNote) => void;
  onTogglePin: (note: InspirationNote) => void;
  onDelete: (note: InspirationNote) => void;
  onTagClick: (tag: string) => void;
}

function timeOf(iso: string): string {
  const d = new Date(iso);
  return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
}

export const NoteCard: React.FC<Props> = ({ note, onEdit, onTogglePin, onDelete, onTagClick }) => {
  const { t } = useTranslation();
  const [menuOpen, setMenuOpen] = useState(false);
  const menuRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!menuOpen) return;
    const close = (e: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) setMenuOpen(false);
    };
    document.addEventListener('mousedown', close);
    return () => document.removeEventListener('mousedown', close);
  }, [menuOpen]);

  return (
    <div className="rounded-xl bg-island px-4 py-3">
      <div className="flex items-center gap-2 text-[11px] text-content-3 tabular-nums">
        <span>{timeOf(note.created_at)}</span>
        {note.pinned && <Pin size={11} className="text-indigo-400" />}
        <div className="relative ml-auto" ref={menuRef}>
          <button
            aria-label="Note actions"
            onClick={() => setMenuOpen((v) => !v)}
            className="rounded p-1 text-content-3 hover:bg-island-2 hover:text-content-2"
          >
            <MoreHorizontal size={15} />
          </button>
          {menuOpen && (
            <div className="absolute right-0 z-10 mt-1 w-32 rounded-lg border border-line bg-island-2 py-1 text-xs text-content-2 shadow-lg">
              <button className="block w-full px-3 py-1.5 text-left hover:bg-line" onClick={() => { setMenuOpen(false); onEdit(note); }}>
                {t('inspiration.edit', 'Edit')}
              </button>
              <button className="block w-full px-3 py-1.5 text-left hover:bg-line" onClick={() => { setMenuOpen(false); onTogglePin(note); }}>
                {note.pinned ? t('inspiration.unpin', 'Unpin') : t('inspiration.pin', 'Pin')}
              </button>
              <button className="block w-full px-3 py-1.5 text-left text-red-400 hover:bg-line" onClick={() => { setMenuOpen(false); onDelete(note); }}>
                {t('inspiration.delete', 'Delete')}
              </button>
            </div>
          )}
        </div>
      </div>

      <div className="mt-1.5 text-[13.5px] leading-relaxed text-content">
        <MarkdownBody content={note.content_md} />
      </div>

      {note.ref_hotspot && (
        <div className="mt-2 rounded-lg border border-line border-l-2 border-l-indigo-500 bg-island-2 px-3 py-2">
          <div className="flex items-center gap-2 text-[10px] text-content-3">
            {note.ref_hotspot.source && (
              <span className="rounded bg-line px-1.5 py-0.5 font-bold text-content-2">{note.ref_hotspot.source}</span>
            )}
          </div>
          <div className="mt-1 text-[13px] font-semibold text-content">{note.ref_hotspot.title}</div>
          <div className="mt-1 flex items-center gap-3 text-[11px]">
            {typeof note.ref_hotspot.heat === 'number' && (
              <span className="inline-flex items-center gap-1 text-amber-400">
                <Flame size={11} /> {note.ref_hotspot.heat}
              </span>
            )}
            {note.ref_hotspot.url && (
              <a href={note.ref_hotspot.url} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 text-indigo-300 hover:underline">
                {t('inspiration.openSource', 'Open source')} <ExternalLink size={10} />
              </a>
            )}
          </div>
        </div>
      )}

      {note.tags.length > 0 && (
        <div className="mt-2 flex flex-wrap gap-1.5">
          {note.tags.map((tag) => (
            <button
              key={tag}
              onClick={() => onTagClick(tag)}
              className="rounded bg-indigo-500/15 px-1.5 py-0.5 text-xs text-indigo-300 hover:bg-indigo-500/25"
            >
              #{tag}
            </button>
          ))}
        </div>
      )}

      <AttachmentView attachments={note.attachments} />
    </div>
  );
};
```

- [ ] **Step 4: 跑测试确认全过**

Run: `npx vitest run components/Inspiration/NoteCard.test.tsx`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add frontend/components/Inspiration/NoteCard.tsx frontend/components/Inspiration/NoteCard.test.tsx
git commit -m "feat(inspiration): note card — markdown body, refcard, tag chips, actions menu"
```

---

### Task 5: Composer(快记框)

**Files:**
- Create: `frontend/components/Inspiration/Composer.tsx`
- Test: `frontend/components/Inspiration/Composer.test.tsx`

**Interfaces:**
- Consumes: Task 1 `createNote`/`uploadAttachment`、Task 2 `findActiveTag`
- Produces:
```tsx
<Composer onCreated={(note: InspirationNote) => void}
  tagSuggestions={string[]}   // 页面传入(来自 getTagCounts)
  prefill?: { content: string; refHotspot?: RefHotspot } | null  // P3 存灵感闭环用,本期实现但页面暂不传
/>
```
行为:textarea(⌘/Ctrl+Enter 提交);`#` 输入时下拉补全(取 tagSuggestions 前缀匹配,点击替换当前 token);粘贴图片/拖放/选择文件 → 暂存 chip(文件名+移除钮);Save → `createNote` → 逐个 `uploadAttachment` → 全部完成后 `onCreated(带附件的note)` + 清空;上传或创建失败 toast 错误、已建 note 保留(不静默)

- [ ] **Step 1: 写失败测试**

```tsx
import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';

const createNote = vi.fn();
const uploadAttachment = vi.fn();
vi.mock('../../services/inspirationService', () => ({
  createNote: (...a: unknown[]) => createNote(...a),
  uploadAttachment: (...a: unknown[]) => uploadAttachment(...a),
}));
vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (_k: string, fallback: string) => fallback }),
}));
const addToast = vi.fn();
vi.mock('../Toast', () => ({ useToast: () => ({ addToast }) }));

import { Composer } from './Composer';

describe('Composer', () => {
  it('cmd+enter creates note and clears input', async () => {
    createNote.mockResolvedValue({ id: '1', attachments: [], tags: [] });
    render(<Composer onCreated={vi.fn()} tagSuggestions={[]} />);
    const ta = screen.getByRole('textbox');
    fireEvent.change(ta, { target: { value: 'quick idea #x' } });
    fireEvent.keyDown(ta, { key: 'Enter', metaKey: true });
    await waitFor(() => expect(createNote).toHaveBeenCalledWith('quick idea #x', undefined));
    expect((ta as HTMLTextAreaElement).value).toBe('');
  });

  it('empty content does not submit', () => {
    render(<Composer onCreated={vi.fn()} tagSuggestions={[]} />);
    fireEvent.click(screen.getByText('Save'));
    expect(createNote).not.toHaveBeenCalled();
  });

  it('typing # shows prefix-matched suggestions and click completes token', () => {
    render(<Composer onCreated={vi.fn()} tagSuggestions={['hooks', 'formats']} />);
    const ta = screen.getByRole('textbox') as HTMLTextAreaElement;
    fireEvent.change(ta, { target: { value: 'idea #ho', selectionStart: 8 } });
    fireEvent.click(screen.getByText('#hooks'));
    expect(ta.value).toBe('idea #hooks ');
  });

  it('staged files upload after note creation and onCreated gets merged note', async () => {
    createNote.mockResolvedValue({ id: '9', attachments: [], tags: [] });
    uploadAttachment.mockResolvedValue({ id: 'a1', mime: 'image/png', size_bytes: 4, original_name: 'p.png' });
    const onCreated = vi.fn();
    render(<Composer onCreated={onCreated} tagSuggestions={[]} />);
    const input = screen.getByLabelText('Attach files') as HTMLInputElement;
    const file = new File(['x'], 'p.png', { type: 'image/png' });
    fireEvent.change(input, { target: { files: [file] } });
    expect(screen.getByText('p.png')).toBeTruthy();
    const ta = screen.getByRole('textbox');
    fireEvent.change(ta, { target: { value: 'with file' } });
    fireEvent.click(screen.getByText('Save'));
    await waitFor(() => expect(uploadAttachment).toHaveBeenCalledWith('9', file));
    await waitFor(() =>
      expect(onCreated).toHaveBeenCalledWith(
        expect.objectContaining({ id: '9', attachments: [expect.objectContaining({ id: 'a1' })] }),
      ),
    );
  });

  it('create failure toasts error and keeps text', async () => {
    createNote.mockRejectedValue(new Error('boom'));
    render(<Composer onCreated={vi.fn()} tagSuggestions={[]} />);
    const ta = screen.getByRole('textbox') as HTMLTextAreaElement;
    fireEvent.change(ta, { target: { value: 'keep me' } });
    fireEvent.click(screen.getByText('Save'));
    await waitFor(() => expect(addToast).toHaveBeenCalledWith('boom', 'error'));
    expect(ta.value).toBe('keep me');
  });
});
```

- [ ] **Step 2: 跑测试确认失败**

Run: `npx vitest run components/Inspiration/Composer.test.tsx`
Expected: FAIL

- [ ] **Step 3: 实现**

```tsx
// frontend/components/Inspiration/Composer.tsx
// Quick-capture box: inline #tag autocomplete, staged multi-format
// attachments (paste / drop / picker), Cmd+Enter submit.
import React, { useCallback, useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Image as ImageIcon, Mic, Paperclip, X } from 'lucide-react';
import { useToast } from '../Toast';
import {
  createNote,
  uploadAttachment,
  type InspirationNote,
  type NoteAttachment,
  type RefHotspot,
} from '../../services/inspirationService';
import { findActiveTag } from './noteTags';

interface Props {
  onCreated: (note: InspirationNote) => void;
  tagSuggestions: string[];
  prefill?: { content: string; refHotspot?: RefHotspot } | null;
}

export const Composer: React.FC<Props> = ({ onCreated, tagSuggestions, prefill }) => {
  const { t } = useTranslation();
  const { addToast } = useToast();
  const [text, setText] = useState(prefill?.content ?? '');
  const [staged, setStaged] = useState<File[]>([]);
  const [saving, setSaving] = useState(false);
  const [caret, setCaret] = useState(0);
  const taRef = useRef<HTMLTextAreaElement>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  const active = useMemo(() => findActiveTag(text, caret), [text, caret]);
  const suggestions = useMemo(() => {
    if (!active) return [];
    return tagSuggestions.filter((s) => s.startsWith(active.prefix) && s !== active.prefix).slice(0, 6);
  }, [active, tagSuggestions]);

  const completeTag = (tag: string) => {
    if (!active) return;
    const next = `${text.slice(0, active.start)}#${tag} `;
    setText(next);
    setCaret(next.length);
    taRef.current?.focus();
  };

  const stageFiles = useCallback((files: FileList | File[]) => {
    setStaged((prev) => [...prev, ...Array.from(files)]);
  }, []);

  const submit = async () => {
    const content = text.trim();
    if (!content || saving) return;
    setSaving(true);
    try {
      const note = await createNote(content, prefill?.refHotspot);
      const uploaded: NoteAttachment[] = [];
      for (const file of staged) {
        try {
          uploaded.push(await uploadAttachment(note.id, file));
        } catch (err) {
          addToast(
            t('inspiration.uploadFailed', 'Upload failed: {{name}}', { name: file.name }) +
              `: ${(err as Error).message}`,
            'error',
          );
        }
      }
      onCreated({ ...note, attachments: [...note.attachments, ...uploaded] });
      setText('');
      setStaged([]);
    } catch (err) {
      addToast((err as Error).message, 'error');
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="rounded-xl bg-island px-4 pb-3 pt-4">
      <textarea
        ref={taRef}
        value={text}
        rows={2}
        placeholder={t('inspiration.placeholder', 'Capture an idea… #tag inline, paste an image, or drop any file')}
        onChange={(e) => {
          setText(e.target.value);
          setCaret(e.target.selectionStart ?? e.target.value.length);
        }}
        onKeyDown={(e) => {
          if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) {
            e.preventDefault();
            void submit();
          }
        }}
        onPaste={(e) => {
          const files = Array.from(e.clipboardData.files);
          if (files.length) {
            e.preventDefault();
            stageFiles(files);
          }
        }}
        onDrop={(e) => {
          e.preventDefault();
          if (e.dataTransfer.files.length) stageFiles(e.dataTransfer.files);
        }}
        onDragOver={(e) => e.preventDefault()}
        className="w-full resize-none bg-transparent text-[13.5px] text-content placeholder:text-content-4 focus:outline-none"
      />

      {suggestions.length > 0 && (
        <div className="flex flex-wrap gap-1.5 border-t border-line pt-2">
          {suggestions.map((s) => (
            <button
              key={s}
              onClick={() => completeTag(s)}
              className="rounded bg-indigo-500/15 px-2 py-0.5 text-xs text-indigo-300 hover:bg-indigo-500/25"
            >
              #{s}
            </button>
          ))}
        </div>
      )}

      {staged.length > 0 && (
        <div className="flex flex-wrap gap-1.5 pt-2">
          {staged.map((f, i) => (
            <span key={`${f.name}-${i}`} className="inline-flex items-center gap-1.5 rounded-lg bg-island-2 px-2.5 py-1 text-xs text-content-2">
              {f.name}
              <button
                aria-label={`Remove ${f.name}`}
                onClick={() => setStaged((prev) => prev.filter((_, j) => j !== i))}
                className="text-content-3 hover:text-content"
              >
                <X size={11} />
              </button>
            </span>
          ))}
        </div>
      )}

      <div className="mt-2 flex items-center gap-1 border-t border-line pt-2">
        <button
          aria-label="Attach image"
          onClick={() => fileRef.current?.click()}
          className="rounded-lg p-1.5 text-content-3 hover:bg-island-2"
        >
          <ImageIcon size={15} />
        </button>
        <button
          aria-label="Attach files"
          onClick={() => fileRef.current?.click()}
          className="rounded-lg p-1.5 text-content-3 hover:bg-island-2"
        >
          <Paperclip size={15} />
        </button>
        <button aria-label="Attach audio" onClick={() => fileRef.current?.click()} className="rounded-lg p-1.5 text-content-3 hover:bg-island-2">
          <Mic size={15} />
        </button>
        <input
          ref={fileRef}
          type="file"
          multiple
          aria-label="Attach files"
          className="hidden"
          onChange={(e) => {
            if (e.target.files?.length) stageFiles(e.target.files);
            e.target.value = '';
          }}
        />
        <button
          onClick={() => void submit()}
          disabled={saving || !text.trim()}
          className="ml-auto rounded-lg bg-indigo-500 px-4 py-1.5 text-xs font-semibold text-white disabled:opacity-40"
        >
          {saving ? t('inspiration.saving', 'Saving…') : t('inspiration.save', 'Save')}
        </button>
      </div>
    </div>
  );
};
```

注意:`aria-label="Attach files"` 同时出现在按钮和隐藏 input 上会让 getByLabelText 匹配到两个——把按钮的 aria-label 改成 `"Attach file"`(单数)避免冲突,测试用 input 的 `Attach files`。实现时直接按此处理。

- [ ] **Step 4: 跑测试确认全过**

Run: `npx vitest run components/Inspiration/Composer.test.tsx`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add frontend/components/Inspiration/Composer.tsx frontend/components/Inspiration/Composer.test.tsx
git commit -m "feat(inspiration): composer — tag autocomplete, staged attachments, cmd+enter"
```

---

### Task 6: ActivityPanel(热力图 ⇄ 迷你月历)

**Files:**
- Create: `frontend/components/Inspiration/ActivityPanel.tsx`
- Test: `frontend/components/Inspiration/ActivityPanel.test.tsx`

**Interfaces:**
- Consumes: Task 1 `getActivity`
- Produces:
```tsx
<ActivityPanel selectedDate={string | null} onSelectDate={(d: string | null) => void} refreshKey={number} />
```
行为:挂载(及 refreshKey 变化)拉近 16 周 activity;两模式角标切换(格子图标=heatmap / 日历图标=month),`localStorage['inspiration.activityMode']` 记忆;heatmap=16 列×7 行(周一起始),按 cnt 分 5 档 indigo 透明度,点格子 `onSelectDate(day)`(再点同一天=取消传 null);month=当月网格,密度圆点,今天描边,选中实心,‹›翻月;所有日期 `YYYY-MM-DD`

- [ ] **Step 1: 写失败测试**

```tsx
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';

const getActivity = vi.fn();
vi.mock('../../services/inspirationService', () => ({
  getActivity: (...a: unknown[]) => getActivity(...a),
}));
vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (_k: string, fallback: string) => fallback }),
}));

import { ActivityPanel } from './ActivityPanel';

describe('ActivityPanel', () => {
  beforeEach(() => {
    localStorage.clear();
    getActivity.mockResolvedValue([{ day: '2026-07-07', cnt: 3 }]);
  });

  it('loads ~16 weeks of activity on mount', async () => {
    render(<ActivityPanel selectedDate={null} onSelectDate={vi.fn()} refreshKey={0} />);
    await waitFor(() => expect(getActivity).toHaveBeenCalled());
    const [from, to] = getActivity.mock.calls[0];
    const days = (new Date(to).getTime() - new Date(from).getTime()) / 86400000;
    expect(days).toBeGreaterThanOrEqual(105);
    expect(days).toBeLessThanOrEqual(120);
  });

  it('clicking a heatmap cell selects that date; clicking again clears', async () => {
    const onSelectDate = vi.fn();
    const { rerender } = render(
      <ActivityPanel selectedDate={null} onSelectDate={onSelectDate} refreshKey={0} />,
    );
    await waitFor(() => expect(getActivity).toHaveBeenCalled());
    const cell = await screen.findByLabelText('2026-07-07: 3 notes');
    fireEvent.click(cell);
    expect(onSelectDate).toHaveBeenCalledWith('2026-07-07');
    rerender(<ActivityPanel selectedDate="2026-07-07" onSelectDate={onSelectDate} refreshKey={0} />);
    fireEvent.click(screen.getByLabelText('2026-07-07: 3 notes'));
    expect(onSelectDate).toHaveBeenLastCalledWith(null);
  });

  it('mode toggle switches to calendar and persists', async () => {
    render(<ActivityPanel selectedDate={null} onSelectDate={vi.fn()} refreshKey={0} />);
    await waitFor(() => expect(getActivity).toHaveBeenCalled());
    fireEvent.click(screen.getByLabelText('Calendar view'));
    expect(localStorage.getItem('inspiration.activityMode')).toBe('calendar');
    expect(screen.getByLabelText('Previous month')).toBeTruthy();
  });

  it('refreshKey change refetches', async () => {
    const { rerender } = render(
      <ActivityPanel selectedDate={null} onSelectDate={vi.fn()} refreshKey={0} />,
    );
    await waitFor(() => expect(getActivity).toHaveBeenCalledTimes(1));
    rerender(<ActivityPanel selectedDate={null} onSelectDate={vi.fn()} refreshKey={1} />);
    await waitFor(() => expect(getActivity).toHaveBeenCalledTimes(2));
  });
});
```

- [ ] **Step 2: 跑测试确认失败**

Run: `npx vitest run components/Inspiration/ActivityPanel.test.tsx`
Expected: FAIL

- [ ] **Step 3: 实现**

```tsx
// frontend/components/Inspiration/ActivityPanel.tsx
// One calendar for the whole page (spec §2.2 #2): heatmap ⇄ mini month,
// corner toggle persisted in localStorage; selected date is page state
// owned by the parent.
import React, { useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Calendar as CalendarIcon, ChevronLeft, ChevronRight, LayoutGrid } from 'lucide-react';
import { getActivity } from '../../services/inspirationService';

const MODE_KEY = 'inspiration.activityMode';
const WEEKS = 16;

function ymd(d: Date): string {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
}

function heatClass(cnt: number): string {
  if (cnt <= 0) return 'bg-indigo-500/10';
  if (cnt === 1) return 'bg-indigo-500/25';
  if (cnt === 2) return 'bg-indigo-500/45';
  if (cnt <= 4) return 'bg-indigo-500/70';
  return 'bg-indigo-500';
}

interface Props {
  selectedDate: string | null;
  onSelectDate: (date: string | null) => void;
  refreshKey: number;
}

export const ActivityPanel: React.FC<Props> = ({ selectedDate, onSelectDate, refreshKey }) => {
  const { t } = useTranslation();
  const [mode, setMode] = useState<'heatmap' | 'calendar'>(
    () => (localStorage.getItem(MODE_KEY) === 'calendar' ? 'calendar' : 'heatmap'),
  );
  const [counts, setCounts] = useState<Record<string, number>>({});
  const [month, setMonth] = useState(() => {
    const now = new Date();
    return new Date(now.getFullYear(), now.getMonth(), 1);
  });

  useEffect(() => {
    let alive = true;
    (async () => {
      const to = new Date();
      const from = new Date(to.getTime() - (WEEKS * 7 - 1) * 86400000);
      try {
        const rows = await getActivity(ymd(from), ymd(to));
        if (!alive) return;
        const map: Record<string, number> = {};
        for (const r of rows) map[r.day] = r.cnt;
        setCounts(map);
      } catch (err) {
        console.error('activity load failed', err);
      }
    })();
    return () => {
      alive = false;
    };
  }, [refreshKey]);

  const pick = (day: string) => onSelectDate(day === selectedDate ? null : day);

  const setModePersist = (m: 'heatmap' | 'calendar') => {
    setMode(m);
    localStorage.setItem(MODE_KEY, m);
  };

  // 16 weeks of columns, Monday-first, ending today.
  const heatDays = useMemo(() => {
    const today = new Date();
    const dow = (today.getDay() + 6) % 7; // Mon=0
    const end = new Date(today.getTime() + (6 - dow) * 86400000);
    const days: string[] = [];
    for (let i = WEEKS * 7 - 1; i >= 0; i--) days.push(ymd(new Date(end.getTime() - i * 86400000)));
    return days;
  }, []);

  const monthCells = useMemo(() => {
    const first = new Date(month);
    const lead = (first.getDay() + 6) % 7;
    const start = new Date(first.getTime() - lead * 86400000);
    return Array.from({ length: 42 }, (_, i) => {
      const d = new Date(start.getTime() + i * 86400000);
      return { date: ymd(d), inMonth: d.getMonth() === month.getMonth(), dayNum: d.getDate() };
    });
  }, [month]);

  const todayStr = ymd(new Date());

  return (
    <div className="rounded-xl bg-island px-4 py-3.5">
      <div className="mb-2.5 flex items-center">
        <h3 className="text-[11px] font-semibold uppercase tracking-wider text-content-3">
          {t('inspiration.activity', 'Activity')}
        </h3>
        <div className="ml-auto flex rounded-md bg-island-2 p-0.5">
          <button
            aria-label="Heatmap view"
            onClick={() => setModePersist('heatmap')}
            className={`rounded px-1.5 py-0.5 ${mode === 'heatmap' ? 'bg-island text-indigo-300' : 'text-content-4'}`}
          >
            <LayoutGrid size={11} />
          </button>
          <button
            aria-label="Calendar view"
            onClick={() => setModePersist('calendar')}
            className={`rounded px-1.5 py-0.5 ${mode === 'calendar' ? 'bg-island text-indigo-300' : 'text-content-4'}`}
          >
            <CalendarIcon size={11} />
          </button>
        </div>
      </div>

      {mode === 'heatmap' ? (
        <div className="grid grid-flow-col grid-rows-7 gap-[3px]">
          {heatDays.map((day) => {
            const cnt = counts[day] ?? 0;
            return (
              <button
                key={day}
                aria-label={`${day}: ${cnt} notes`}
                onClick={() => pick(day)}
                className={`aspect-square w-full rounded-[3px] ${heatClass(cnt)} ${
                  selectedDate === day ? 'ring-1 ring-content ring-offset-1 ring-offset-island' : ''
                }`}
              />
            );
          })}
        </div>
      ) : (
        <div>
          <div className="mb-1.5 flex items-center gap-1.5">
            <span className="text-xs font-semibold tabular-nums text-content">
              {month.toLocaleDateString('en-US', { month: 'long', year: 'numeric' })}
            </span>
            <button
              aria-label="Previous month"
              onClick={() => setMonth(new Date(month.getFullYear(), month.getMonth() - 1, 1))}
              className="ml-auto rounded bg-island-2 p-1 text-content-3"
            >
              <ChevronLeft size={11} />
            </button>
            <button
              aria-label="Next month"
              onClick={() => setMonth(new Date(month.getFullYear(), month.getMonth() + 1, 1))}
              className="rounded bg-island-2 p-1 text-content-3"
            >
              <ChevronRight size={11} />
            </button>
          </div>
          <div className="grid grid-cols-7 gap-0.5">
            {['M', 'T', 'W', 'T2', 'F', 'S', 'S2'].map((d) => (
              <div key={d} className="pb-0.5 text-center text-[9px] font-semibold uppercase text-content-4">
                {d.charAt(0)}
              </div>
            ))}
            {monthCells.map(({ date, inMonth, dayNum }) => {
              const cnt = counts[date] ?? 0;
              const sel = selectedDate === date;
              return (
                <button
                  key={date}
                  aria-label={`${date}: ${cnt} notes`}
                  onClick={() => pick(date)}
                  className={`flex h-8 flex-col items-center justify-center gap-0.5 rounded-md text-[11px] tabular-nums ${
                    sel
                      ? 'bg-indigo-500 font-bold text-white'
                      : date === todayStr
                        ? 'font-bold text-indigo-300 shadow-[inset_0_0_0_1.5px] shadow-indigo-500'
                        : inMonth
                          ? 'text-content-2 hover:bg-island-2'
                          : 'text-content-4 opacity-50'
                  }`}
                >
                  {dayNum}
                  {cnt > 0 && <i className={`h-1 w-1 rounded-full ${sel ? 'bg-white/80' : heatClass(cnt)}`} />}
                </button>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
};
```

- [ ] **Step 4: 跑测试确认全过**

Run: `npx vitest run components/Inspiration/ActivityPanel.test.tsx`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add frontend/components/Inspiration/ActivityPanel.tsx frontend/components/Inspiration/ActivityPanel.test.tsx
git commit -m "feat(inspiration): activity panel — heatmap/calendar toggle, shared date state"
```

---

### Task 7: TagsPanel + NoteTimeline + InspirationPage 组装

**Files:**
- Create: `frontend/components/Inspiration/TagsPanel.tsx`
- Create: `frontend/components/Inspiration/NoteTimeline.tsx`
- Create: `frontend/pages/InspirationPage.tsx`
- Test: `frontend/pages/InspirationPage.test.tsx`

**Interfaces:**
- Consumes: Task 1-6 全部
- Produces:
  - `<TagsPanel tags={{tag,cnt}[]} activeTag={string|null} onTagClick={(tag|null)=>void} />`(点激活 tag=取消)
  - `<NoteTimeline notes={InspirationNote[]} onEdit onTogglePin onDelete onTagClick hasMore loadMore loading />`(按 note_date 分组,组头 `WED JUL 7 · n notes`(formatDateShort 大写)+ 分隔线;底部 Load more 按钮)
  - `pages/InspirationPage.tsx` export `InspirationPage`:组合 topbar(标题+过滤 chips+搜索框 300ms debounce)/ Composer / NoteTimeline / 右栏 ActivityPanel+TagsPanel;状态:`filters{date,tag,q}`、notes、hasMore(len===limit)、refreshKey(创建/删除后 bump 让 ActivityPanel 重拉);操作:创建→prepend、pin→PATCH 后原位替换、edit→window.prompt 简易弹层?**不**——edit 走 NoteCard 菜单回调,页面里用内联方案:把该 note 的 content_md 放回 Composer 不现实,P2 用 `updateNote(id,{content_md})` + 简易 modal(textarea)实现;delete→confirm 后 DELETE+移除

- [ ] **Step 1: 写失败测试**(页面级,mock service)

```tsx
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';

const listNotes = vi.fn();
const getTagCounts = vi.fn();
const getActivity = vi.fn();
const deleteNote = vi.fn();
const updateNote = vi.fn();
vi.mock('../services/inspirationService', () => ({
  listNotes: (...a: unknown[]) => listNotes(...a),
  getTagCounts: (...a: unknown[]) => getTagCounts(...a),
  getActivity: (...a: unknown[]) => getActivity(...a),
  deleteNote: (...a: unknown[]) => deleteNote(...a),
  updateNote: (...a: unknown[]) => updateNote(...a),
  createNote: vi.fn(),
  uploadAttachment: vi.fn(),
  attachmentUrl: (id: string) => `http://api.test/att/${id}`,
}));
vi.mock('../components/AILibrary/MarkdownBody', () => ({
  default: ({ content }: { content: string }) => <div>{content}</div>,
}));
vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (_k: string, fallback: string) => fallback }),
}));
const addToast = vi.fn();
vi.mock('../components/Toast', () => ({ useToast: () => ({ addToast }) }));

import { InspirationPage } from './InspirationPage';

const NOTE = {
  id: '1', content_md: 'first idea #hooks', tags: ['hooks'], ref_hotspot: null,
  pinned: false, note_date: '2026-07-07',
  created_at: '2026-07-07T09:42:00+00:00', updated_at: '2026-07-07T09:42:00+00:00',
  attachments: [],
};

describe('InspirationPage', () => {
  beforeEach(() => {
    listNotes.mockResolvedValue([NOTE]);
    getTagCounts.mockResolvedValue([{ tag: 'hooks', cnt: 3 }]);
    getActivity.mockResolvedValue([]);
  });

  it('loads and renders notes grouped by day', async () => {
    render(<InspirationPage />);
    await waitFor(() => expect(listNotes).toHaveBeenCalled());
    expect(await screen.findByText('first idea #hooks')).toBeTruthy();
  });

  it('tag panel click sets filter chip and refetches with tag', async () => {
    render(<InspirationPage />);
    await waitFor(() => expect(getTagCounts).toHaveBeenCalled());
    fireEvent.click(await screen.findByText('#hooks (3)'));
    await waitFor(() =>
      expect(listNotes).toHaveBeenLastCalledWith(
        expect.objectContaining({ tag: 'hooks' }),
        expect.anything(),
        undefined,
      ),
    );
    expect(screen.getByLabelText('Clear tag filter')).toBeTruthy();
  });

  it('search input debounces into q filter', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    render(<InspirationPage />);
    await waitFor(() => expect(listNotes).toHaveBeenCalled());
    fireEvent.change(screen.getByPlaceholderText('Search notes…'), { target: { value: 'ferry' } });
    await vi.advanceTimersByTimeAsync(350);
    await waitFor(() =>
      expect(listNotes).toHaveBeenLastCalledWith(
        expect.objectContaining({ q: 'ferry' }),
        expect.anything(),
        undefined,
      ),
    );
    vi.useRealTimers();
  });

  it('delete flows through confirm and removes the card', async () => {
    vi.spyOn(window, 'confirm').mockReturnValue(true);
    deleteNote.mockResolvedValue(undefined);
    render(<InspirationPage />);
    await screen.findByText('first idea #hooks');
    fireEvent.click(screen.getByLabelText('Note actions'));
    fireEvent.click(screen.getByText('Delete'));
    await waitFor(() => expect(deleteNote).toHaveBeenCalledWith('1'));
    await waitFor(() => expect(screen.queryByText('first idea #hooks')).toBeNull());
  });
});
```

- [ ] **Step 2: 跑测试确认失败**

Run: `npx vitest run pages/InspirationPage.test.tsx`
Expected: FAIL

- [ ] **Step 3: 实现三个文件**

```tsx
// frontend/components/Inspiration/TagsPanel.tsx
import React from 'react';
import { useTranslation } from 'react-i18next';
import { X } from 'lucide-react';

interface Props {
  tags: { tag: string; cnt: number }[];
  activeTag: string | null;
  onTagClick: (tag: string | null) => void;
}

export const TagsPanel: React.FC<Props> = ({ tags, activeTag, onTagClick }) => {
  const { t } = useTranslation();
  if (!tags.length) return null;
  return (
    <div className="rounded-xl bg-island px-4 py-3.5">
      <h3 className="mb-2.5 text-[11px] font-semibold uppercase tracking-wider text-content-3">
        {t('inspiration.tags', 'Tags')}
      </h3>
      <div className="flex flex-wrap gap-1.5">
        {tags.map(({ tag, cnt }) => {
          const on = tag === activeTag;
          return (
            <button
              key={tag}
              onClick={() => onTagClick(on ? null : tag)}
              className={`inline-flex items-center gap-1 rounded-full px-2.5 py-1 text-xs ${
                on ? 'bg-indigo-500/20 text-indigo-300' : 'bg-island-2 text-content-2 hover:bg-line'
              }`}
            >
              {`#${tag} (${cnt})`}
              {on && <X size={10} aria-label="Clear tag filter" />}
            </button>
          );
        })}
      </div>
    </div>
  );
};
```

```tsx
// frontend/components/Inspiration/NoteTimeline.tsx
import React, { useMemo } from 'react';
import { useTranslation } from 'react-i18next';
import { NoteCard } from './NoteCard';
import type { InspirationNote } from '../../services/inspirationService';
import { formatDateShort } from '../../utils/formatDate';

interface Props {
  notes: InspirationNote[];
  onEdit: (n: InspirationNote) => void;
  onTogglePin: (n: InspirationNote) => void;
  onDelete: (n: InspirationNote) => void;
  onTagClick: (tag: string) => void;
  hasMore: boolean;
  loading: boolean;
  loadMore: () => void;
}

export const NoteTimeline: React.FC<Props> = ({
  notes, onEdit, onTogglePin, onDelete, onTagClick, hasMore, loading, loadMore,
}) => {
  const { t } = useTranslation();
  const groups = useMemo(() => {
    const byDay = new Map<string, InspirationNote[]>();
    for (const n of notes) {
      const list = byDay.get(n.note_date) ?? [];
      list.push(n);
      byDay.set(n.note_date, list);
    }
    return Array.from(byDay.entries());
  }, [notes]);

  if (!notes.length && !loading) {
    return (
      <div className="rounded-xl bg-island px-4 py-10 text-center text-sm text-content-3">
        {t('inspiration.empty', 'No notes yet — capture your first idea above.')}
      </div>
    );
  }

  return (
    <div className="space-y-2.5">
      {groups.map(([day, dayNotes]) => (
        <React.Fragment key={day}>
          <div className="flex items-center gap-2.5 px-0.5 pt-1 text-[11px] font-semibold uppercase tracking-wide text-content-3 tabular-nums">
            {formatDateShort(day)}
            <span className="font-normal text-content-4">
              · {t('inspiration.noteCount', '{{count}} notes', { count: dayNotes.length })}
            </span>
            <span className="h-px flex-1 bg-line" />
          </div>
          {dayNotes.map((n) => (
            <NoteCard key={n.id} note={n} onEdit={onEdit} onTogglePin={onTogglePin} onDelete={onDelete} onTagClick={onTagClick} />
          ))}
        </React.Fragment>
      ))}
      {hasMore && (
        <button
          onClick={loadMore}
          disabled={loading}
          className="w-full rounded-xl bg-island py-2 text-xs text-content-3 hover:text-content-2 disabled:opacity-50"
        >
          {loading ? t('inspiration.loading', 'Loading…') : t('inspiration.loadMore', 'Load more')}
        </button>
      )}
    </div>
  );
};
```

```tsx
// frontend/pages/InspirationPage.tsx
// New notes-first Inspiration workspace (spec §2, mockup v7). Rendered by
// TopicInspirationPage when VITE_FEATURE_INSPIRATION_NOTES is on; hotspot
// integration (tab, side panel, save-as-note) arrives in P3.
import React, { useCallback, useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Search, X } from 'lucide-react';
import { useToast } from '../components/Toast';
import { Composer } from '../components/Inspiration/Composer';
import { NoteTimeline } from '../components/Inspiration/NoteTimeline';
import { ActivityPanel } from '../components/Inspiration/ActivityPanel';
import { TagsPanel } from '../components/Inspiration/TagsPanel';
import {
  deleteNote,
  getTagCounts,
  listNotes,
  updateNote,
  type InspirationNote,
} from '../services/inspirationService';

const PAGE_SIZE = 50;

export const InspirationPage: React.FC = () => {
  const { t } = useTranslation();
  const { addToast } = useToast();
  const [notes, setNotes] = useState<InspirationNote[]>([]);
  const [tags, setTags] = useState<{ tag: string; cnt: number }[]>([]);
  const [date, setDate] = useState<string | null>(null);
  const [tag, setTag] = useState<string | null>(null);
  const [queryInput, setQueryInput] = useState('');
  const [q, setQ] = useState('');
  const [hasMore, setHasMore] = useState(false);
  const [loading, setLoading] = useState(false);
  const [refreshKey, setRefreshKey] = useState(0);
  const [editing, setEditing] = useState<InspirationNote | null>(null);
  const [editText, setEditText] = useState('');

  useEffect(() => {
    const id = setTimeout(() => setQ(queryInput.trim()), 300);
    return () => clearTimeout(id);
  }, [queryInput]);

  const filters = { date: date ?? undefined, tag: tag ?? undefined, q: q || undefined };

  useEffect(() => {
    let alive = true;
    (async () => {
      setLoading(true);
      try {
        const rows = await listNotes(
          { date: date ?? undefined, tag: tag ?? undefined, q: q || undefined },
          PAGE_SIZE,
          undefined,
        );
        if (!alive) return;
        setNotes(rows);
        setHasMore(rows.length === PAGE_SIZE);
      } catch (err) {
        if (alive) addToast(`Failed to load notes: ${(err as Error).message}`, 'error');
      } finally {
        if (alive) setLoading(false);
      }
    })();
    return () => {
      alive = false;
    };
    // addToast is context-stable (Toast provider useCallback)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [date, tag, q, refreshKey]);

  useEffect(() => {
    let alive = true;
    getTagCounts()
      .then((rows) => alive && setTags(rows))
      .catch((err) => console.error('tag counts failed', err));
    return () => {
      alive = false;
    };
  }, [refreshKey]);

  const loadMore = useCallback(async () => {
    if (!notes.length || loading) return;
    setLoading(true);
    try {
      const rows = await listNotes(filters, PAGE_SIZE, notes[notes.length - 1].id);
      setNotes((prev) => [...prev, ...rows]);
      setHasMore(rows.length === PAGE_SIZE);
    } catch (err) {
      addToast(`Failed to load notes: ${(err as Error).message}`, 'error');
    } finally {
      setLoading(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [notes, loading, date, tag, q]);

  const onCreated = (note: InspirationNote) => {
    setNotes((prev) => [note, ...prev]);
    setRefreshKey((k) => k + 1);
  };

  const onTogglePin = async (note: InspirationNote) => {
    try {
      const updated = await updateNote(note.id, { pinned: !note.pinned });
      setNotes((prev) => prev.map((n) => (n.id === note.id ? updated : n)));
    } catch (err) {
      addToast((err as Error).message, 'error');
    }
  };

  const onDelete = async (note: InspirationNote) => {
    if (!window.confirm(t('inspiration.deleteConfirm', 'Delete this note?'))) return;
    try {
      await deleteNote(note.id);
      setNotes((prev) => prev.filter((n) => n.id !== note.id));
      setRefreshKey((k) => k + 1);
    } catch (err) {
      addToast((err as Error).message, 'error');
    }
  };

  const startEdit = (note: InspirationNote) => {
    setEditing(note);
    setEditText(note.content_md);
  };

  const saveEdit = async () => {
    if (!editing) return;
    try {
      const updated = await updateNote(editing.id, { content_md: editText });
      setNotes((prev) => prev.map((n) => (n.id === editing.id ? updated : n)));
      setEditing(null);
      setRefreshKey((k) => k + 1);
    } catch (err) {
      addToast((err as Error).message, 'error');
    }
  };

  return (
    <div className="mx-auto max-w-[1180px] px-6 py-6">
      <div className="mb-3 flex items-center gap-3 rounded-xl bg-island px-4 py-2.5">
        <h2 className="text-[15px] font-semibold text-content">
          {t('inspiration.title', 'Inspiration')}
        </h2>
        {tag && (
          <button
            onClick={() => setTag(null)}
            className="inline-flex items-center gap-1.5 rounded-full bg-indigo-500/15 px-2.5 py-1 text-xs text-indigo-300"
          >
            #{tag} <X size={10} aria-label="Clear tag filter" />
          </button>
        )}
        {date && (
          <button
            onClick={() => setDate(null)}
            className="inline-flex items-center gap-1.5 rounded-full bg-indigo-500/15 px-2.5 py-1 text-xs text-indigo-300"
          >
            {date} <X size={10} aria-label="Clear date filter" />
          </button>
        )}
        <div className="ml-auto flex w-64 items-center gap-2 rounded-lg bg-island-2 px-3 py-1.5">
          <Search size={13} className="shrink-0 text-content-4" />
          <input
            value={queryInput}
            onChange={(e) => setQueryInput(e.target.value)}
            placeholder={t('inspiration.searchPlaceholder', 'Search notes…')}
            className="w-full bg-transparent text-xs text-content placeholder:text-content-4 focus:outline-none"
          />
        </div>
      </div>

      <div className="flex gap-3">
        <div className="min-w-0 flex-1 space-y-2.5">
          <Composer onCreated={onCreated} tagSuggestions={tags.map((x) => x.tag)} />
          <NoteTimeline
            notes={notes}
            onEdit={startEdit}
            onTogglePin={onTogglePin}
            onDelete={onDelete}
            onTagClick={(tg) => setTag(tg)}
            hasMore={hasMore}
            loading={loading}
            loadMore={() => void loadMore()}
          />
        </div>
        <div className="hidden w-[292px] shrink-0 space-y-2.5 lg:block">
          <ActivityPanel selectedDate={date} onSelectDate={setDate} refreshKey={refreshKey} />
          <TagsPanel tags={tags} activeTag={tag} onTagClick={setTag} />
        </div>
      </div>

      {editing && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4">
          <div className="w-full max-w-xl rounded-xl bg-island p-4">
            <h4 className="mb-2 text-sm font-semibold text-content">
              {t('inspiration.editNote', 'Edit note')}
            </h4>
            <textarea
              value={editText}
              onChange={(e) => setEditText(e.target.value)}
              rows={6}
              className="w-full resize-y rounded-lg bg-island-2 p-3 text-[13.5px] text-content focus:outline-none"
            />
            <div className="mt-3 flex justify-end gap-2">
              <button onClick={() => setEditing(null)} className="rounded-lg bg-island-2 px-4 py-1.5 text-xs text-content-2">
                {t('inspiration.cancel', 'Cancel')}
              </button>
              <button onClick={() => void saveEdit()} className="rounded-lg bg-indigo-500 px-4 py-1.5 text-xs font-semibold text-white">
                {t('inspiration.save', 'Save')}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};
```

- [ ] **Step 4: 跑测试确认全过**

Run: `npx vitest run pages/InspirationPage.test.tsx`
Expected: 4 passed。注意 `filters` 对象在 render 间重建被 loadMore 闭包引用——测试若因引用不稳挂掉,把 filters 抽进 useMemo(deps [date,tag,q]),别改测试。

- [ ] **Step 5: Commit**

```bash
git add frontend/components/Inspiration/TagsPanel.tsx frontend/components/Inspiration/NoteTimeline.tsx frontend/pages/InspirationPage.tsx frontend/pages/InspirationPage.test.tsx
git commit -m "feat(inspiration): notes workspace page — timeline, filters, edit modal, sidebar"
```

---

### Task 8: flag 接线 + i18n + 收口

**Files:**
- Modify: `frontend/pages/TopicInspirationPage.tsx`(顶部加 flag 分支,3 行)
- Modify: `frontend/public/locales/en.json` + `frontend/public/locales/zh.json`(新增 `inspiration` 命名空间)
- Test: `frontend/pages/inspirationFlag.test.tsx`

**Interfaces:**
- Consumes: Task 7 `InspirationPage`
- flag 写法(参照 `pages/ScriptEditor/index.tsx`):

- [ ] **Step 1: 写失败测试**

```tsx
import { afterEach, describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';

vi.mock('./InspirationPage', () => ({
  InspirationPage: () => <div data-testid="new-page">new</div>,
}));
vi.mock('../hooks/useTopicModuleEnabled', () => ({
  useTopicModuleStatus: () => ({ visible: true, enabled: true }),
}));
vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (_k: string, fallback?: string) => fallback ?? _k }),
}));
vi.mock('../components/Toast', () => ({ useToast: () => ({ addToast: vi.fn() }) }));
vi.mock('../services/topicService', () => ({
  getHotspots: vi.fn().mockResolvedValue([]),
  getHotspotDates: vi.fn().mockResolvedValue([]),
  getInterest: vi.fn().mockResolvedValue({ interest_text: '', has_embedding: false }),
  setInterest: vi.fn(),
  setHotspotState: vi.fn(),
}));

describe('TopicInspirationPage flag switch', () => {
  afterEach(() => {
    vi.unstubAllEnvs();
    vi.resetModules();
  });

  it('renders the new InspirationPage when flag is on', async () => {
    vi.stubEnv('VITE_FEATURE_INSPIRATION_NOTES', 'true');
    const { TopicInspirationPage } = await import('./TopicInspirationPage');
    render(<TopicInspirationPage />);
    expect(screen.getByTestId('new-page')).toBeTruthy();
  });

  it('renders the legacy page when flag is off', async () => {
    vi.stubEnv('VITE_FEATURE_INSPIRATION_NOTES', 'false');
    const { TopicInspirationPage } = await import('./TopicInspirationPage');
    render(<TopicInspirationPage />);
    expect(screen.queryByTestId('new-page')).toBeNull();
  });
});
```

注意:flag 若写成模块级常量,`vi.stubEnv` 必须配合 `vi.resetModules()`+动态 import(如上)。若跑不通,把 flag 读取放进组件体内(`const enabled = import.meta.env.VITE_FEATURE_INSPIRATION_NOTES === 'true';` 在组件函数第一行),测试同样成立且更可测——**采用组件体内读取**。

- [ ] **Step 2: 跑测试确认失败**

Run: `npx vitest run pages/inspirationFlag.test.tsx`
Expected: FAIL(新页面未接)

- [ ] **Step 3: 实现 flag 分支**

`TopicInspirationPage.tsx` 顶部 import 区加:
```tsx
import { InspirationPage } from './InspirationPage';
```
组件函数体第一行(在现有 hooks 之前不行——**必须在所有 hooks 之后再条件 return**,否则违反 rules-of-hooks 被 CI eslint 拦;放在 `useTopicModuleStatus()` 调用之后、第一个 useEffect 之前的位置不行,hooks 必须全部执行——正确做法:在组件最顶部读 flag,**在 return JSX 处分支**):
```tsx
const notesEnabled = import.meta.env.VITE_FEATURE_INSPIRATION_NOTES === 'true';
```
然后把现有的 `if (!moduleVisible) return (...)` 之前加:
```tsx
if (notesEnabled) return <InspirationPage />;
```
(`notesEnabled` 是普通常量不是 hook,early return 放在**所有 hook 调用之后**——即现有 useState/useEffect 声明块的末尾、`if (!moduleVisible)` 分支之前。)

- [ ] **Step 4: i18n keys**

`en.json` 在 `"topic"` 命名空间同级加(zh.json 同 key 中文值):
```json
"inspiration": {
  "title": "Inspiration",
  "placeholder": "Capture an idea… #tag inline, paste an image, or drop any file",
  "save": "Save",
  "saving": "Saving…",
  "activity": "Activity",
  "tags": "Tags",
  "edit": "Edit",
  "pin": "Pin",
  "unpin": "Unpin",
  "delete": "Delete",
  "deleteConfirm": "Delete this note?",
  "editNote": "Edit note",
  "cancel": "Cancel",
  "empty": "No notes yet — capture your first idea above.",
  "loadMore": "Load more",
  "loading": "Loading…",
  "noteCount": "{{count}} notes",
  "searchPlaceholder": "Search notes…",
  "openSource": "Open source",
  "uploadFailed": "Upload failed: {{name}}"
}
```
zh.json 对应中文(title=灵感库、placeholder=记录灵感…正文打 #标签,粘贴图片或拖入任意文件、save=保存 等,逐 key 翻译)。

- [ ] **Step 5: 全量验证**

```bash
npx vitest run pages/inspirationFlag.test.tsx   # 2 passed
npm run lint                                     # rules-of-hooks 零 error(early return 位置就是为它设计的)
npx vitest run                                   # 全量,含既有 1879+ 与新增 ~32
npm run build                                    # 过
```

- [ ] **Step 6: Commit**

```bash
git add frontend/pages/TopicInspirationPage.tsx frontend/pages/inspirationFlag.test.tsx frontend/public/locales/en.json frontend/public/locales/zh.json
git commit -m "feat(inspiration): flag-gated page switch + i18n namespace (VITE_FEATURE_INSPIRATION_NOTES)"
```

---

### Task 9: 收口 — push + PR

- [ ] **Step 1**: `cd frontend && npm run lint && npm run build && npx vitest run 2>&1 | tail -3` 全绿
- [ ] **Step 2**: `git push -u origin feature/inspiration-p2-notes`,`gh pr create` 标题 `feat(inspiration): P2 notes workspace — composer/timeline/attachments/calendar (flag-dark)`,body 说明 flag off 时旧页零变化、列组件清单与测试数
- [ ] **Step 3**: CI 全绿后单独 `gh pr merge --squash --delete-branch`(不与等待同批)
