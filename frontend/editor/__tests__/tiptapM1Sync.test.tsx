/**
 * M1 sync pipeline — the structural-vs-text dispatch split, the 500ms text
 * debounce (+ flush on blur/unmount), and the `applyExternalElements`
 * imperative API's loop guard (spec D3 / M1 phase). Mirrors
 * tiptapM0.test.tsx / tiptapM1Keymap.test.tsx's jsdom geometry polyfill and
 * `window.__tipTapSceneEditorInstance` test backdoor.
 */
import { createRef } from 'react';
import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest';
import { act, cleanup, render, waitFor } from '@testing-library/react';
import type { Editor } from '@tiptap/core';
import type { Node as PMNode } from '@tiptap/pm/model';
import { TipTapSceneEditor, type TipTapSceneEditorHandle } from '../tiptap/TipTapSceneEditor';
import { docToElements } from '../tiptap/docModel';
import { applyLocal } from '../opBuilder';
import type { ElementOp, ScriptElement } from '../types';

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

vi.mock('../sceneService', () => ({ newElementId: () => 'el_unused' }));

const el = (id: string, type: ScriptElement['type'], text: string): ScriptElement => ({
  id,
  type,
  text,
  character_id: null,
});

function getEditor(): Editor {
  return (window as unknown as Record<string, unknown>).__tipTapSceneEditorInstance as Editor;
}

function findElementPos(editor: Editor, elementId: string): { start: number; end: number; node: PMNode } {
  let result: { start: number; end: number; node: PMNode } | null = null;
  let offset = 0;
  editor.state.doc.forEach((node) => {
    if (!result && node.attrs.id === elementId) {
      result = { start: offset + 1, end: offset + node.nodeSize - 1, node };
    }
    offset += node.nodeSize;
  });
  if (!result) throw new Error(`element ${elementId} not found in doc`);
  return result;
}

function setCaret(editor: Editor, elementId: string, charOffset: number) {
  const { start } = findElementPos(editor, elementId);
  act(() => {
    editor.commands.setTextSelection(start + charOffset);
  });
}

function pressTab(editor: Editor) {
  act(() => {
    editor.view.dom.dispatchEvent(
      new KeyboardEvent('keydown', { key: 'Tab', bubbles: true, cancelable: true }),
    );
  });
}

function lastOps(dispatchOps: ReturnType<typeof vi.fn>): ElementOp[] {
  const calls = dispatchOps.mock.calls;
  if (calls.length === 0) return [];
  return calls[calls.length - 1][0] as ElementOp[];
}

async function mount(elements: ScriptElement[]) {
  const dispatchOps = vi.fn();
  const onFocusCursor = vi.fn();
  const ref = createRef<TipTapSceneEditorHandle>();
  render(
    <TipTapSceneEditor
      ref={ref}
      initialElements={elements}
      format="hollywood"
      dispatchOps={dispatchOps}
      onFocusCursor={onFocusCursor}
    />,
  );
  await waitFor(() => expect(getEditor()).toBeTruthy());
  return { editor: getEditor(), dispatchOps, onFocusCursor, ref };
}

beforeEach(() => {
  vi.clearAllMocks();
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

describe('text-only debounce', () => {
  it('typing produces no dispatch until 500ms elapse, then fires exactly once', async () => {
    const before = [el('el_a', 'action', 'Hi')];
    const { editor, dispatchOps } = await mount(before);

    vi.useFakeTimers();
    const { end } = findElementPos(editor, 'el_a');
    act(() => {
      editor.commands.insertContentAt(end, '!');
    });
    expect(dispatchOps).not.toHaveBeenCalled();

    act(() => {
      vi.advanceTimersByTime(499);
    });
    expect(dispatchOps).not.toHaveBeenCalled();

    act(() => {
      vi.advanceTimersByTime(1);
    });
    expect(dispatchOps).toHaveBeenCalledTimes(1);
    const ops = lastOps(dispatchOps);
    expect(applyLocal(before, ops)).toEqual(docToElements(editor.state.doc));
  });

  it('a structural op (Tab) fires immediately and folds in any still-pending text diff', async () => {
    const before = [el('el_a', 'action', 'Hi')];
    const { editor, dispatchOps } = await mount(before);

    vi.useFakeTimers();
    const { end } = findElementPos(editor, 'el_a');
    act(() => {
      editor.commands.insertContentAt(end, '!');
    });
    expect(dispatchOps).not.toHaveBeenCalled(); // still debouncing

    pressTab(editor); // structural — dispatches NOW, no 500ms wait
    expect(dispatchOps).toHaveBeenCalledTimes(1);
    const ops = lastOps(dispatchOps);
    // One update op carrying BOTH the text and type change (same element id).
    expect(ops).toHaveLength(1);
    expect(ops[0]).toMatchObject({
      op: 'update',
      element_id: 'el_a',
      payload: { type: 'character', text: 'Hi!' },
    });
    expect(applyLocal(before, ops)).toEqual(docToElements(editor.state.doc));

    // The debounce timer that was pending got cleared — advancing time
    // further must not produce a second dispatch.
    act(() => {
      vi.advanceTimersByTime(1000);
    });
    expect(dispatchOps).toHaveBeenCalledTimes(1);
  });

  it('blur flushes a pending text debounce immediately', async () => {
    const before = [el('el_a', 'action', 'Hi')];
    const { editor, dispatchOps } = await mount(before);

    // jsdom's HTMLElement.blur() only fires a native 'blur' event when the
    // element is document.activeElement — focus it first so the subsequent
    // blur() actually dispatches (and PM's FocusEvents core extension, which
    // our onBlur config relies on, only listens for the real DOM event).
    // `editor.commands.focus()` routes through PM's selection-based focus
    // helper, which doesn't reliably move jsdom's activeElement — call
    // `.focus()` on the view's DOM node directly instead.
    act(() => {
      editor.view.dom.focus();
    });

    vi.useFakeTimers();
    const { end } = findElementPos(editor, 'el_a');
    act(() => {
      editor.commands.insertContentAt(end, '!');
    });
    expect(dispatchOps).not.toHaveBeenCalled();

    act(() => {
      editor.view.dom.blur();
    });
    expect(dispatchOps).toHaveBeenCalledTimes(1);
    const ops = lastOps(dispatchOps);
    expect(ops[0]).toMatchObject({ op: 'update', element_id: 'el_a', payload: { text: 'Hi!' } });
  });

  it('flushes a pending text debounce on unmount', async () => {
    const before = [el('el_a', 'action', 'Hi')];
    const dispatchOps = vi.fn();
    const { unmount } = render(
      <TipTapSceneEditor initialElements={before} format="hollywood" dispatchOps={dispatchOps} />,
    );
    await waitFor(() => expect(getEditor()).toBeTruthy());
    const editor = getEditor();

    const { end } = findElementPos(editor, 'el_a');
    act(() => {
      editor.commands.insertContentAt(end, '!');
    });
    expect(dispatchOps).not.toHaveBeenCalled();

    act(() => {
      unmount();
    });
    expect(dispatchOps).toHaveBeenCalledTimes(1);
    expect(dispatchOps.mock.calls[0][0]).toMatchObject([
      { op: 'update', element_id: 'el_a', payload: { text: 'Hi!' } },
    ]);
  });
});

describe('applyExternalElements — remote/reconcile/conflict apply + loop guard', () => {
  it('rebuilds the doc, emits NO ops, and a later local edit diffs against the new baseline', async () => {
    const before = [el('el_a', 'action', 'Hi'), el('el_b', 'character', 'BOB')];
    const { editor, dispatchOps, ref } = await mount(before);

    const remote: ScriptElement[] = [
      el('el_a', 'action', 'Hi remote'),
      el('el_b', 'character', 'BOB'),
      el('el_c', 'action', 'New line'),
    ];
    act(() => {
      ref.current?.applyExternalElements(remote);
    });

    expect(dispatchOps).not.toHaveBeenCalled();
    expect(docToElements(editor.state.doc)).toEqual(remote);

    // A subsequent structural edit (retype el_c) diffs against the NEW
    // baseline (remote), not the original `before` mount snapshot.
    setCaret(editor, 'el_c', 0);
    pressTab(editor);
    const ops = lastOps(dispatchOps);
    expect(ops).toEqual([{ op: 'update', element_id: 'el_c', payload: { type: 'character' } }]);
    expect(applyLocal(remote, ops)).toEqual(docToElements(editor.state.doc));
  });

  it('loop guard: re-applying identical elements is a true no-op (no transaction dispatched)', async () => {
    const before = [el('el_a', 'action', 'Hi'), el('el_b', 'character', 'BOB')];
    const { editor, ref } = await mount(before);

    let txCount = 0;
    editor.on('transaction', () => {
      txCount += 1;
    });
    act(() => {
      // Field-wise identical to `before` (fresh array/object references, but
      // same id/type/text/character_id) — must be recognized as unchanged.
      ref.current?.applyExternalElements(before.map((e) => ({ ...e })));
    });
    expect(txCount).toBe(0);
  });

  it('onUpdate never re-emits ops for an applyExternalElements transaction', async () => {
    const before = [el('el_a', 'action', 'Hi')];
    const { dispatchOps, ref } = await mount(before);
    act(() => {
      ref.current?.applyExternalElements([el('el_a', 'action', 'Changed remotely')]);
    });
    expect(dispatchOps).not.toHaveBeenCalled();
  });

  it('best-effort restores the caret into the focused element, clamped to its new length', async () => {
    const before = [el('el_a', 'action', 'Hello world')];
    const { editor, ref } = await mount(before);
    setCaret(editor, 'el_a', 5);

    const remote = [el('el_a', 'action', 'Hi')]; // shorter — offset 5 must clamp to 2
    act(() => {
      ref.current?.applyExternalElements(remote);
    });

    const { start } = findElementPos(editor, 'el_a');
    expect(editor.state.selection.from).toBe(start + 2);
  });

  it('is a no-op when passed an empty list (the PM schema requires scriptElement+)', async () => {
    const before = [el('el_a', 'action', 'Hi')];
    const { editor, ref } = await mount(before);
    act(() => {
      ref.current?.applyExternalElements([]);
    });
    expect(docToElements(editor.state.doc)).toEqual(before);
  });
});
