// NoteEditor — a #tag is marked as a tag WHILE composing, not only after save.
//
// Reported: typing "模型成功率统计 #nous" in the composer left "#nous" looking
// like plain text; it only became a chip once the note was saved and rendered
// by NoteMarkdown. Other platforms (the report cited Douyin's publish box)
// set a finished #tag apart as soon as it is typed.
//
// Two contracts matter more than the styling:
//   * PARITY — what is highlighted while typing is exactly what parseTags()
//     will store. Both go through tagPattern(); a highlight that disagreed
//     with storage would promise a tag the note never gets.
//   * PRESENTATION ONLY — the highlight is a ProseMirror decoration, so the
//     markdown the composer emits is unchanged plain "#tag".
import { describe, expect, it, vi } from 'vitest';
import { act, render, waitFor } from '@testing-library/react';
import { NoteEditor } from './NoteEditor';
import { parseTags } from './noteTags';

const zeroRect = {
  bottom: 0, height: 0, left: 0, right: 0, toJSON: () => ({}), top: 0, width: 0, x: 0, y: 0,
};
Element.prototype.getClientRects = () => [] as unknown as DOMRectList;
Element.prototype.getBoundingClientRect = () => zeroRect as DOMRect;
Range.prototype.getClientRects = () => [] as unknown as DOMRectList;
Range.prototype.getBoundingClientRect = () => zeroRect as DOMRect;
if (typeof document.elementFromPoint !== 'function') {
  document.elementFromPoint = () => null;
}

type Ed = {
  commands: {
    insertContent: (c: string) => boolean;
    focus: (pos?: 'start' | 'end') => boolean;
    setTextSelection: (pos: number) => boolean;
  };
  chain: () => { focus: () => { toggleCodeBlock: () => { run: () => boolean } } };
  state: { doc: { content: { size: number } } };
};
const editor = () =>
  (window as unknown as Record<string, unknown>).__noteEditorInstance as Ed;

const chips = () =>
  Array.from(document.querySelectorAll('.ProseMirror .note-tag-chip')).map((el) => el.textContent);

async function mount(value = '', onChange = vi.fn()) {
  render(<NoteEditor value={value} onChange={onChange} />);
  await waitFor(() => expect(document.querySelector('.ProseMirror')).toBeTruthy());
  return onChange;
}

describe('NoteEditor live #tag highlight', () => {
  it('marks the tag once a space follows it', async () => {
    await mount();
    act(() => {
      editor().commands.focus('end');
      editor().commands.insertContent('模型成功率统计 #nous ');
    });
    await waitFor(() => expect(chips()).toEqual(['#nous']));
  });

  it('leaves the tag unmarked while the caret is still at its end', async () => {
    // Still being typed: marking it mid-word would flicker on every keystroke
    // and sit on top of the autocomplete, which is active in exactly this state.
    await mount();
    act(() => {
      editor().commands.focus('end');
      editor().commands.insertContent('idea #nou');
    });
    await waitFor(() =>
      expect(document.querySelector('.ProseMirror')?.textContent).toContain('#nou'),
    );
    expect(chips()).toEqual([]);
  });

  it('marks the tag when the caret moves away without a space', async () => {
    await mount();
    act(() => {
      editor().commands.focus('end');
      editor().commands.insertContent('idea #nous');
    });
    act(() => {
      editor().commands.setTextSelection(1);
    });
    await waitFor(() => expect(chips()).toEqual(['#nous']));
  });

  it('marks tags in content loaded for editing', async () => {
    await mount('模型成功率统计  #nous and #抖音解析');
    await waitFor(() => expect(chips()).toEqual(['#nous', '#抖音解析']));
  });

  it('uses the storage boundary rule: no tag glued to a word, no heading', async () => {
    await mount('a#b and x #ok ');
    await waitFor(() => expect(chips()).toEqual(['#ok']));
  });

  it('does not mark a # inside a code block', async () => {
    await mount();
    act(() => {
      editor().commands.focus('end');
      editor().chain().focus().toggleCodeBlock().run();
      editor().commands.insertContent('#nous ');
    });
    await waitFor(() =>
      expect(document.querySelector('.ProseMirror pre code')?.textContent).toContain('#nous'),
    );
    expect(chips()).toEqual([]);
  });

  it('does not mark a # inside inline code', async () => {
    await mount('see `#nous` here and #real ');
    await waitFor(() => expect(chips()).toEqual(['#real']));
  });

  it('highlights exactly the tags parseTags() stores', async () => {
    const md = 'one #Alpha, (#beta) （#中文） x#nope #gamma-1 and `#code` #last ';
    await mount(md);
    await waitFor(() => expect(chips().length).toBeGreaterThan(0));
    const shown = chips().map((c) => (c as string).slice(1).toLowerCase());
    expect([...new Set(shown)]).toEqual(parseTags(md));
  });

  it('does not change the markdown the composer emits', async () => {
    const onChange = await mount();
    act(() => {
      editor().commands.focus('end');
      editor().commands.insertContent('idea #nous ');
    });
    await waitFor(() => expect(chips()).toEqual(['#nous']));
    const md = onChange.mock.calls.at(-1)?.[0] as string;
    expect(md).toContain('#nous');
    expect(md).not.toMatch(/note-tag-chip|<span/);
  });
});
