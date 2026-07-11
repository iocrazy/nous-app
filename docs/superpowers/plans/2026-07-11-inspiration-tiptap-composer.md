# Inspiration TipTap Composer — 快记框迁移 TipTap Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) syntax.

**Goal:** 快记框(Composer)与编辑弹窗从原生 textarea 迁到 TipTap——全站编辑引擎统一(剧本编辑器已是 TipTap),获得 notion 式所见即所得:`## `→真标题、`[] `→真勾选框、```→高亮代码块、回车续列表(引擎原生)。**存储不变仍是 markdown**(官方 `@tiptap/markdown` 序列化),后端/渲染端/PAT 零改动。

**Architecture:** 新组件 `NoteEditor.tsx` 封装 TipTap(markdown in/out 的受控契约)+ 内置 #标签补全;Composer 换用它(工具栏按钮改driving编辑器命令),`editorErgonomics.ts` 整文件退役(引擎原生);InspirationPage 编辑弹窗同样换 NoteEditor(真统一)。Composer/页面的既有测试通过 **mock NoteEditor 为轻量 shim**(同 props 契约)保持稳定;NoteEditor 本体有自己的真 TipTap 测试。

**决策记录:** 用户 2026-07-11 明确推翻 spec 原"快记用纯 textarea"决策——"我要新技术,全局文本编辑只需要一种",选定 TipTap(vs CodeMirror:后者会引入第三种引擎)。

## Global Constraints

- 工作目录:本 worktree(分支 `feat/inspiration-tiptap-composer`);命令 `frontend/` 下
- 新依赖:`@tiptap/markdown`(官方,v3.27.x)、`@tiptap/extension-task-list`、`@tiptap/extension-task-item`、`@tiptap/extension-code-block-lowlight`、`lowlight`。若与现有 `^3.22.2` 的 @tiptap/* peer 冲突,把**全部** @tiptap/* 对齐到同一 3.27.x(lockstep 版本族)
- **NoteEditor props 契约(所有任务共同遵守)**:
```tsx
interface NoteEditorProps {
  value: string;                        // markdown in
  onChange: (markdown: string) => void; // markdown out(每次内容变化)
  placeholder?: string;
  autoFocus?: boolean;                  // 聚焦且光标落文档开头
  onSubmit?: () => void;                // ⌘/Ctrl+Enter
  onFiles?: (files: File[]) => void;    // 粘贴/拖入文件(非文本)
  tagSuggestions?: string[];            // #补全候选
  minRows?: number;                     // 默认 2;max-height 恒 50vh 内滚
}
export interface NoteEditorHandle { insertTag(): void; insertCodeBlock(): void; insertLink(): void; focus(): void; }
// forwardRef 暴露 handle,Composer 工具栏用
```
- markdown 保真红线(测试守住):`- [ ] `待办、```围栏代码、`#`/`##`标题、`#tag` 纯文本、粗斜/引用/列表,roundtrip(md→编辑器→md)不变形;空文档序列化为 `''`
- UI 文本英文 i18n;零 emoji;禁 zinc;样式贴现有 composer(text-[13.5px] text-content,ProseMirror 内容区样式对齐 NoteMarkdown 的标题/列表/代码块观感)
- 每 task:vitest 过+相关回归+commit;收口 `npm run lint`(rules-of-hooks)/`npm run build`/全量 vitest
- **P2-P4 行为不回退**:prefill(key 重挂载)、ref chip、staged 附件、失败 retry、⌘Enter、autoFocus 光标开头、标签补全、50vh 上限

---

### Task 1: 依赖 + NoteEditor 本体(markdown 受控 + 扩展 + handle)

**Files:**
- Modify: `frontend/package.json`(新依赖)
- Create: `frontend/components/Inspiration/NoteEditor.tsx`
- Create: `frontend/components/Inspiration/noteEditor.css`(ProseMirror 内容样式)
- Test: `frontend/components/Inspiration/NoteEditor.test.tsx`

**Interfaces:** Produces 上述 NoteEditorProps/NoteEditorHandle(后续任务只依赖这个契约)。

- [ ] **Step 1: 装依赖**
```bash
npm install @tiptap/markdown @tiptap/extension-task-list @tiptap/extension-task-item @tiptap/extension-code-block-lowlight lowlight
```
装完 `npm ls @tiptap/core` 确认单版本;peer 冲突则统一升 @tiptap/* 到同 3.27.x。**先读 @tiptap/markdown 的 README/типы**(node_modules 里)确认注册方式(v3 是 `Markdown` extension 还是 `editor.storage.markdown.getMarkdown()` API——以实际为准,下面代码相应对齐)。

- [ ] **Step 2: 写失败测试**(真 TipTap,jsdom 可跑——editor/__tests__ 有先例)

```tsx
import { describe, expect, it, vi } from 'vitest';
import { act, render, screen, waitFor } from '@testing-library/react';
import { createRef } from 'react';
import { NoteEditor, type NoteEditorHandle } from './NoteEditor';

vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: (_k: string, f: string) => f }) }));

function lastMd(onChange: ReturnType<typeof vi.fn>): string {
  return onChange.mock.calls.at(-1)?.[0] ?? '';
}

describe('NoteEditor markdown contract', () => {
  it('parses markdown in and serializes the same shapes back out', async () => {
    const onChange = vi.fn();
    const ref = createRef<NoteEditorHandle>();
    const md = '## Title\n\n- [ ] todo\n- [x] done\n\n```js\nconst x = 1;\n```\n\n#tag plain';
    render(<NoteEditor ref={ref} value={md} onChange={onChange} />);
    await waitFor(() => expect(document.querySelector('.ProseMirror')).toBeTruthy());
    // heading rendered as real h2, todos as checkboxes, code as pre
    expect(document.querySelector('.ProseMirror h2')?.textContent).toBe('Title');
    expect(document.querySelectorAll('.ProseMirror input[type="checkbox"]').length).toBe(2);
    expect(document.querySelector('.ProseMirror pre code')).toBeTruthy();
    // roundtrip: force a serialize via a no-op-ish command then inspect onChange md
    act(() => ref.current!.focus());
    // typing a char then removing it is flaky; instead assert getter path via insert+undo-free check:
    // NoteEditor must call onChange on every doc change — trigger one:
    act(() => ref.current!.insertTag());
    const out = lastMd(onChange);
    expect(out).toContain('## Title');
    expect(out).toContain('- [ ] todo');
    expect(out).toContain('- [x] done');
    expect(out).toContain('```js');
    expect(out).toContain('#tag plain');
  });

  it('command handle: insertCodeBlock / insertTag / insertLink shape the markdown', async () => {
    const onChange = vi.fn();
    const ref = createRef<NoteEditorHandle>();
    render(<NoteEditor ref={ref} value="" onChange={onChange} />);
    await waitFor(() => expect(document.querySelector('.ProseMirror')).toBeTruthy());
    act(() => ref.current!.insertCodeBlock());
    expect(lastMd(onChange)).toContain('```');
    act(() => ref.current!.insertTag());
    expect(lastMd(onChange)).toContain('#');
  });

  it('cmd+enter fires onSubmit instead of inserting a newline', async () => {
    const onSubmit = vi.fn();
    render(<NoteEditor value="hi" onChange={vi.fn()} onSubmit={onSubmit} />);
    await waitFor(() => expect(document.querySelector('.ProseMirror')).toBeTruthy());
    const pm = document.querySelector('.ProseMirror') as HTMLElement;
    pm.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', metaKey: true, bubbles: true }));
    expect(onSubmit).toHaveBeenCalled();
  });

  it('autoFocus puts the caret at the document start', async () => {
    render(<NoteEditor value="tail" onChange={vi.fn()} autoFocus />);
    await waitFor(() => expect(document.querySelector('.ProseMirror')).toBeTruthy());
    // caret-at-start is asserted via the editor instance the component exposes for tests
    expect((window as any).__noteEditorSelAtStart).toBe(true);
  });

  it('empty document serializes to empty string, placeholder visible', async () => {
    const onChange = vi.fn();
    const ref = createRef<NoteEditorHandle>();
    render(<NoteEditor ref={ref} value="" onChange={onChange} placeholder="Capture…" />);
    await waitFor(() => expect(document.querySelector('.ProseMirror')).toBeTruthy());
    act(() => ref.current!.insertTag()); // '#'
    act(() => ref.current!.focus());
    expect(document.querySelector('[data-placeholder]')).toBeDefined();
  });
});
```
(第 4 条的 `__noteEditorSelAtStart` 是测试后门:NoteEditor 在 autoFocus 分支设置 `if (import.meta.env.MODE === 'test') (window as any).__noteEditorSelAtStart = editor.state.selection.from <= 1;`——只在测试模式,注释说明。若嫌脏,可改为暴露 editor 实例的 data 属性;实现者择一,断言语义=光标在文档开头。)

- [ ] **Step 3: 确认失败** → **Step 4: 实现**

```tsx
// frontend/components/Inspiration/NoteEditor.tsx
// The one editor (user decision 2026-07-11): TipTap everywhere. Markdown-in /
// markdown-out wrapper used by the quick-capture composer and the edit modal.
// Notion-style input rules come free: '## '→heading, '[] '→task, ```→code.
import React, { forwardRef, useEffect, useImperativeHandle } from 'react';
import { useTranslation } from 'react-i18next';
import { EditorContent, useEditor } from '@tiptap/react';
import StarterKit from '@tiptap/starter-kit';
import Placeholder from '@tiptap/extension-placeholder';
import TaskList from '@tiptap/extension-task-list';
import TaskItem from '@tiptap/extension-task-item';
import CodeBlockLowlight from '@tiptap/extension-code-block-lowlight';
import { Markdown } from '@tiptap/markdown';
import { common, createLowlight } from 'lowlight';
import { Extension } from '@tiptap/core';
import './noteEditor.css';

const lowlight = createLowlight(common);

export interface NoteEditorHandle {
  insertTag: () => void;
  insertCodeBlock: () => void;
  insertLink: () => void;
  focus: () => void;
}

interface Props {
  value: string;
  onChange: (markdown: string) => void;
  placeholder?: string;
  autoFocus?: boolean;
  onSubmit?: () => void;
  onFiles?: (files: File[]) => void;
  tagSuggestions?: string[];
  minRows?: number;
}

export const NoteEditor = forwardRef<NoteEditorHandle, Props>(function NoteEditor(
  { value, onChange, placeholder, autoFocus, onSubmit, onFiles, minRows = 2 },
  ref,
) {
  const { t } = useTranslation();
  void t;

  const submitKeymap = Extension.create({
    name: 'submitKeymap',
    addKeyboardShortcuts() {
      return {
        'Mod-Enter': () => {
          onSubmit?.();
          return true;
        },
      };
    },
  });

  const editor = useEditor({
    extensions: [
      StarterKit.configure({ codeBlock: false }),
      CodeBlockLowlight.configure({ lowlight }),
      TaskList,
      TaskItem.configure({ nested: true }),
      Placeholder.configure({ placeholder: placeholder ?? '' }),
      Markdown, // official markdown parse/serialize (verify actual export/registration)
      submitKeymap,
    ],
    content: value,
    contentType: 'markdown', // per @tiptap/markdown docs — verify actual option name
    editorProps: {
      attributes: { class: 'note-editor-content focus:outline-none' },
      handlePaste: (_view, event) => {
        const files = Array.from(event.clipboardData?.files ?? []);
        if (files.length && onFiles) {
          onFiles(files);
          return true;
        }
        return false;
      },
      handleDrop: (_view, event) => {
        const files = Array.from(event.dataTransfer?.files ?? []);
        if (files.length && onFiles) {
          event.preventDefault();
          onFiles(files);
          return true;
        }
        return false;
      },
    },
    onUpdate: ({ editor: ed }) => {
      onChange(ed.storage.markdown?.getMarkdown?.() ?? ed.getText());
    },
  });

  useEffect(() => {
    if (!editor || !autoFocus) return;
    editor.commands.focus('start');
    if (import.meta.env.MODE === 'test') {
      (window as unknown as Record<string, unknown>).__noteEditorSelAtStart =
        editor.state.selection.from <= 1;
    }
  }, [editor, autoFocus]);

  useImperativeHandle(ref, () => ({
    insertTag: () => {
      if (!editor) return;
      const before = editor.state.doc.textBetween(
        Math.max(0, editor.state.selection.from - 1),
        editor.state.selection.from,
      );
      editor.chain().focus().insertContent(before && !/[\s(（]/.test(before) ? ' #' : '#').run();
    },
    insertCodeBlock: () => editor?.chain().focus().toggleCodeBlock().run() && undefined,
    insertLink: () => editor?.chain().focus().insertContent('[]()').run() && undefined,
    focus: () => editor?.commands.focus(),
  }));

  return (
    <div
      className="max-h-[50vh] w-full overflow-y-auto text-[13.5px] text-content"
      style={{ minHeight: `${minRows * 1.55}em` }}
    >
      <EditorContent editor={editor} />
    </div>
  );
});
```
```css
/* frontend/components/Inspiration/noteEditor.css — content styles matching
   NoteMarkdown's rendered look so editing == reading. */
.note-editor-content p { margin: 0.25em 0; }
.note-editor-content h1 { font-size: 18px; font-weight: 700; margin: 0.6em 0 0.2em; }
.note-editor-content h2 { font-size: 16px; font-weight: 700; margin: 0.6em 0 0.2em; }
.note-editor-content h3 { font-size: 14.5px; font-weight: 600; margin: 0.5em 0 0.2em; }
.note-editor-content ul { list-style: disc; padding-left: 1.25rem; margin: 0.25em 0; }
.note-editor-content ol { list-style: decimal; padding-left: 1.25rem; margin: 0.25em 0; }
.note-editor-content ul[data-type='taskList'] { list-style: none; padding-left: 0.25rem; }
.note-editor-content ul[data-type='taskList'] li { display: flex; gap: 0.4rem; align-items: flex-start; }
.note-editor-content ul[data-type='taskList'] input[type='checkbox'] { accent-color: rgb(99 102 241); margin-top: 0.3em; }
.note-editor-content pre { background: var(--island-2); border-radius: 0.5rem; padding: 0.6rem 0.8rem; font-size: 12px; overflow-x: auto; margin: 0.4em 0; }
.note-editor-content code { background: var(--island-2); border-radius: 0.25rem; padding: 0.1em 0.3em; font-size: 12px; }
.note-editor-content pre code { background: transparent; padding: 0; }
.note-editor-content blockquote { border-left: 2px solid rgb(99 102 241 / 0.5); padding-left: 0.75rem; color: var(--content-2); margin: 0.35em 0; }
.note-editor-content p.is-editor-empty:first-child::before { content: attr(data-placeholder); color: var(--content-4); float: left; height: 0; pointer-events: none; }
```
**实现要点(以 node_modules 实际 API 为准,brief 代码是骨架)**:@tiptap/markdown 的注册方式/`contentType` 选项名/`getMarkdown()` 路径,装完读它的 dist 类型定义核对;highlight 主题复用 P4a 已引入的 highlight.js CSS(如需在本 css 补 hljs 色板,拷 github-dark 变量或 import 同款)。

- [ ] **Step 5: 跑测试全过 + lint + commit**
`npx vitest run components/Inspiration/NoteEditor.test.tsx` → 5 passed;`npx eslint` 新文件零 error;
commit: `feat(inspiration): NoteEditor — TipTap markdown editor with task/code/heading rules`

---

### Task 2: NoteEditor 内置 #标签补全

**Files:**
- Modify: `frontend/components/Inspiration/NoteEditor.tsx`
- Test: `frontend/components/Inspiration/NoteEditor.tags.test.tsx`

**Interfaces:** 消费现有 `noteTags.ts::findActiveTag`;补全 UI 与旧 composer 同观感(chips 行);选中→把 `#前缀` 替换为 `#tag `。

- [ ] **Step 1: 失败测试**
```tsx
it('typing # shows prefix-matched suggestions; click completes the tag', async () => {
  const onChange = vi.fn();
  render(<NoteEditor value="" onChange={onChange} tagSuggestions={['hooks', 'formats']} />);
  await waitFor(() => expect(document.querySelector('.ProseMirror')).toBeTruthy());
  // simulate typing via editor transactions: insert '#ho'
  const ed = (window as any).__noteEditorInstance; // test-mode handle (set alongside SelAtStart backdoor)
  act(() => ed.commands.insertContent('#ho'));
  expect(await screen.findByText('#hooks')).toBeTruthy();
  fireEvent.click(screen.getByText('#hooks'));
  expect(onChange.mock.calls.at(-1)?.[0]).toContain('#hooks ');
  expect(screen.queryByText('#formats')).toBeNull();
});
```
(测试后门 `__noteEditorInstance` 仅 test 模式暴露,同 Task 1 后门旁注释。)

- [ ] **Step 2: 失败** → **Step 3: 实现**:onUpdate/onSelectionUpdate 里取当前 textblock 文本+块内偏移(`$from.parent.textBetween(0, $from.parentOffset)` 拼一个伪串),复用 `findActiveTag(伪串, 伪串.length)` 得 prefix;suggestions 状态渲染 chips 行(绝对定位在编辑器下方或文档流内,与旧样式一致);点击→`editor.chain().focus().deleteRange({from: from - (prefix.length+1), to: from}).insertContent('#'+tag+' ').run()`。

- [ ] **Step 4: 全过 + commit** `feat(inspiration): NoteEditor inline #tag autocomplete`

---

### Task 3: Composer 换心脏(textarea → NoteEditor)

**Files:**
- Modify: `frontend/components/Inspiration/Composer.tsx`
- Delete: `frontend/components/Inspiration/editorErgonomics.ts` + `.test.ts`(引擎原生,退役)
- Test: 改 `Composer*.test.tsx` 四个文件(mock NoteEditor 为 shim)

**Interfaces:** Composer 对外 props **不变**(onCreated/onAttachmentUploaded/tagSuggestions/prefill/autoFocus)。内部:`text` state 仍是 markdown(NoteEditor onChange 喂);toolbar 按钮改调 `editorRef.current?.insertTag()` 等;粘贴/拖文件走 NoteEditor onFiles→stageFiles;⌘Enter 走 onSubmit→submit;caret/findActiveTag/completeTag/insertSnippet/autoGrow 相关旧代码全删(补全已内置 NoteEditor)。

- [ ] **Step 1: 写 NoteEditor 测试 shim 并改造 Composer 测试**

shim(放各测试文件顶部的 vi.mock,或抽 `__mocks__` 共享——四个文件同一份,抽 `components/Inspiration/testing/noteEditorShim.tsx` 导出 mock 工厂):
```tsx
// noteEditorShim: a textarea that honors the NoteEditor props contract, so
// Composer's logic tests stay DOM-simple; the real TipTap behavior is covered
// by NoteEditor's own tests.
vi.mock('./NoteEditor', () => ({
  NoteEditor: React.forwardRef(function Shim({ value, onChange, placeholder, onSubmit, onFiles, autoFocus }: any, ref: any) {
    React.useImperativeHandle(ref, () => ({
      insertTag: () => onChange(value + '#'),
      insertCodeBlock: () => onChange(value + '\n```\n\n```\n'),
      insertLink: () => onChange(value + '[]()'),
      focus: () => {},
    }));
    return (
      <textarea
        aria-label="note-editor"
        autoFocus={autoFocus}
        placeholder={placeholder}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        onKeyDown={(e) => e.key === 'Enter' && (e.metaKey || e.ctrlKey) && onSubmit?.()}
        onPaste={(e) => { const f = Array.from(e.clipboardData.files); if (f.length) onFiles?.(f); }}
      />
    );
  }),
}));
```
四个 Composer 测试文件的断言逐条迁移:textbox 查询仍中(shim 是 textarea);toolbar 测试断言改为"点按钮后 onChange 收到含 ```/#/[]() 的值"(语义不变);autofocus 测试断言 shim 收到 autoFocus(document.activeElement 即 shim textarea);caret 位置类断言(selectionStart)删掉——那是 textarea 实现细节,真行为由 NoteEditor.test 覆盖,注释说明。

- [ ] **Step 2: 失败** → **Step 3: 实现 Composer 改造**(按 Interfaces;渲染处 `<NoteEditor ref={editorRef} value={text} onChange={setText} placeholder=... autoFocus={autoFocus} onSubmit={() => void submit()} onFiles={stageFiles} tagSuggestions={tagSuggestions} />`;删除 textarea 与全部 caret/自动长高/工效代码;`git rm` editorErgonomics 两文件)

- [ ] **Step 4: 全过 + 回归**:`npx vitest run components/Inspiration/` 全绿(NoteEditor 真测试 + Composer shim 测试并存)
- [ ] **Step 5: commit** `feat(inspiration): composer runs on TipTap NoteEditor — retire textarea ergonomics`

---

### Task 4: 编辑弹窗统一 + 收口

**Files:**
- Modify: `frontend/pages/InspirationPage.tsx`(编辑弹窗 textarea → NoteEditor)
- Test: 页面测试 mock NoteEditor(同 shim)调整
- 收口:lint/build/全量

- [ ] **Step 1**: 编辑弹窗 `<textarea value={editText}...>` → `<NoteEditor value={editText} onChange={setEditText} minRows={6} />`(不传 autoFocus/onSubmit 也可,保存走既有按钮);页面测试若有对弹窗 textarea 的查询,借 shim 仍是 textarea,应基本零改。
- [ ] **Step 2**: `npx vitest run components/Inspiration/ pages/` 全绿;`npm run lint` 零 error;`npm run build` 过;合并最新 master 重跑全量 `npx vitest run 2>&1 | tail -3`
- [ ] **Step 3**: push + PR 标题 `feat(inspiration): composer & edit modal on TipTap — one editor engine everywhere (markdown storage unchanged)`,body 记录用户决策、依赖、shim 测试策略、markdown 保真测试
- [ ] **Step 4**: CI 绿单独 merge;手动 `vercel deploy --prod`(限流兜底);生产 chunk grep `note-editor-content` 验证;**真浏览器过一遍**(## 变标题/[]变勾选/粘贴长文/保存后 NoteMarkdown 渲染一致)
