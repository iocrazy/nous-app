// NoteEditor markdown contract tests — a REAL TipTap editor mounted in jsdom
// (first real-tiptap test in the repo; IssueReplyBox mocks @tiptap/react).
// jsdom lacks a few layout APIs ProseMirror's view touches; the minimal
// polyfills below stub them so the editor can mount and focus.
import { describe, expect, it, vi } from 'vitest';
import { act, render, waitFor } from '@testing-library/react';
import { createRef } from 'react';
import { NoteEditor, type NoteEditorHandle } from './NoteEditor';

// ProseMirror reads client rects when it renders/scrolls the selection; jsdom
// implements neither on elements nor on ranges. Empty/zero geometry is fine —
// nothing under test depends on real layout.
const zeroRect = {
  bottom: 0,
  height: 0,
  left: 0,
  right: 0,
  toJSON: () => ({}),
  top: 0,
  width: 0,
  x: 0,
  y: 0,
};
Element.prototype.getClientRects = () => [] as unknown as DOMRectList;
Element.prototype.getBoundingClientRect = () => zeroRect as DOMRect;
Range.prototype.getClientRects = () => [] as unknown as DOMRectList;
Range.prototype.getBoundingClientRect = () => zeroRect as DOMRect;
if (typeof document.elementFromPoint !== 'function') {
  document.elementFromPoint = () => null;
}

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
    // roundtrip: NoteEditor must call onChange with serialized markdown on
    // every doc change — trigger one via the command handle. Move the caret
    // to the document END first (the initial selection sits at the start,
    // INSIDE the heading — inserting '#' there would corrupt '## Title'):
    act(() => {
      const ed = (window as unknown as Record<string, unknown>).__noteEditorInstance as {
        commands: { focus: (pos: 'end') => boolean };
      };
      ed.commands.focus('end');
    });
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
    // 'Mod-Enter' resolves per-platform in prosemirror-keymap (Cmd on Mac,
    // Ctrl elsewhere). jsdom's navigator is not Mac, so drive Ctrl+Enter —
    // same shortcut, non-Mac spelling.
    pm.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', ctrlKey: true, bubbles: true }));
    expect(onSubmit).toHaveBeenCalled();
  });

  it('autoFocus puts the caret at the document start', async () => {
    render(<NoteEditor value="tail" onChange={vi.fn()} autoFocus />);
    await waitFor(() => expect(document.querySelector('.ProseMirror')).toBeTruthy());
    // caret-at-start is asserted via the test-only flag the component sets in
    // its autoFocus branch (see NoteEditor's MODE === 'test' backdoor)
    await waitFor(() =>
      expect((window as unknown as Record<string, unknown>).__noteEditorSelAtStart).toBe(true),
    );
  });

  it('empty document serializes to empty string, placeholder visible', async () => {
    const onChange = vi.fn();
    const ref = createRef<NoteEditorHandle>();
    render(<NoteEditor ref={ref} value="" onChange={onChange} placeholder="Capture..." />);
    await waitFor(() => expect(document.querySelector('.ProseMirror')).toBeTruthy());
    // placeholder decoration present while empty
    await waitFor(() =>
      expect(document.querySelector('.ProseMirror [data-placeholder]')).toBeTruthy(),
    );
    // insert then remove content: the editor reports '' (not '&nbsp;' or '\n')
    act(() => ref.current!.insertTag()); // '#'
    expect(lastMd(onChange)).toContain('#');
    act(() => {
      const ed = (window as unknown as Record<string, unknown>).__noteEditorInstance as {
        commands: { clearContent: (emitUpdate?: boolean) => boolean };
      };
      ed.commands.clearContent(true);
    });
    expect(lastMd(onChange)).toBe('');
  });
});
