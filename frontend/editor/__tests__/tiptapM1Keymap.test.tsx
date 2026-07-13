/**
 * M1 keymap parity — the TipTap keymap extension (`tiptap/keymap.ts`) drives
 * a REAL mounted editor (mirrors tiptapM0.test.tsx's polyfill setup) through
 * every legacy `editorMachine` scenario that applies inside the elements
 * area (Enter / Tab / Shift-Tab / Backspace), asserting the SAME golden law
 * the M0 mapper tests pin:
 *
 *   applyLocal(before, dispatchedOps)  deep-equals  docToElements(after)
 *
 * Expected type transitions are asserted against `machineTables` (the SAME
 * module both the legacy machine and this keymap import) rather than
 * hardcoded strings — parity by construction, not by hand-copied fixtures.
 * Enter additionally covers the M1-specific mid-text split (a genuine
 * enhancement over legacy's always-empty-new-line approximation — see
 * `tiptap/keymap.ts`'s module doc).
 */
import { describe, expect, it, vi, beforeEach } from 'vitest';
import { act, cleanup, render, waitFor } from '@testing-library/react';
import { afterEach } from 'vitest';
import type { Editor } from '@tiptap/core';
import type { Node as PMNode } from '@tiptap/pm/model';
import { TipTapSceneEditor } from '../tiptap/TipTapSceneEditor';
import { docToElements } from '../tiptap/docModel';
import { applyLocal } from '../opBuilder';
import { TAB_TYPE, SHIFT_TAB_TYPE, ENTER_NEW_TYPE } from '../machineTables';
import type { ElementOp, ScriptElement } from '../types';

// jsdom lacks layout/selection geometry PM reads when rendering/scrolling the
// selection — mirrors tiptapM0.test.tsx / NoteEditor.test.tsx's polyfill.
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

// Deterministic element ids for insert-driven assertions.
const svc = vi.hoisted(() => {
  let n = 0;
  return { nextId: () => `el_new${(n++).toString(16).padStart(4, '0')}` };
});
vi.mock('../sceneService', () => ({ newElementId: () => svc.nextId() }));

const el = (id: string, type: ScriptElement['type'], text: string): ScriptElement => ({
  id,
  type,
  text,
  character_id: null,
});

/** A canonical scene body covering every element type in reading order. */
const body = (): ScriptElement[] => [
  el('el_a0000000', 'action', 'They enter.'),
  el('el_c0000000', 'character', 'MARIA'),
  el('el_p0000000', 'paren', '(softly)'),
  el('el_d0000000', 'dialogue', 'Hello.'),
  el('el_t0000000', 'transition', 'CUT TO:'),
  el('el_m0000000', 'comment', 'note'),
  el('el_s0000000', 'subtitle', 'Later'),
];

function getEditor(): Editor {
  return (window as unknown as Record<string, unknown>).__tipTapSceneEditorInstance as Editor;
}

/** Absolute [content-start, content-end] positions + node for a given id. */
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

function setCaretAtEnd(editor: Editor, elementId: string) {
  const { start, end } = findElementPos(editor, elementId);
  act(() => {
    editor.commands.setTextSelection(end);
  });
  return end - start;
}

function pressKey(editor: Editor, key: string, opts: Partial<KeyboardEventInit> = {}) {
  act(() => {
    editor.view.dom.dispatchEvent(
      new KeyboardEvent('keydown', { key, bubbles: true, cancelable: true, ...opts }),
    );
  });
}

async function mount(elements: ScriptElement[], dispatchOps = vi.fn(), onFocusCursor = vi.fn()) {
  render(
    <TipTapSceneEditor
      initialElements={elements}
      format="hollywood"
      dispatchOps={dispatchOps}
      onFocusCursor={onFocusCursor}
    />,
  );
  await waitFor(() => expect(getEditor()).toBeTruthy());
  return { editor: getEditor(), dispatchOps, onFocusCursor };
}

/** The single ops batch handed to `dispatchOps` in the LAST call, or []. */
function lastOps(dispatchOps: ReturnType<typeof vi.fn>): ElementOp[] {
  const calls = dispatchOps.mock.calls;
  if (calls.length === 0) return [];
  return calls[calls.length - 1][0] as ElementOp[];
}

beforeEach(() => {
  vi.clearAllMocks();
});

afterEach(() => {
  cleanup();
});

describe('Enter — type transitions (ENTER_NEW_TYPE, parity with editorMachine)', () => {
  it('action → new action anchored after current, fresh id, empty text', async () => {
    const before = body();
    const { editor, dispatchOps } = await mount(before);
    setCaretAtEnd(editor, 'el_a0000000');
    pressKey(editor, 'Enter');

    const ops = lastOps(dispatchOps);
    expect(ops).toHaveLength(1);
    expect(ops[0]).toMatchObject({ op: 'insert', after_id: 'el_a0000000' });
    const inserted = ops[0] as Extract<ElementOp, { op: 'insert' }>;
    expect(inserted.payload.type).toBe(ENTER_NEW_TYPE.action);
    expect(inserted.payload.text).toBe('');
    expect(inserted.element_id).not.toBe('el_a0000000');

    const after = docToElements(editor.state.doc);
    expect(applyLocal(before, ops)).toEqual(after);
  });

  it('character → new dialogue after current', async () => {
    const before = body();
    const { editor, dispatchOps } = await mount(before);
    setCaretAtEnd(editor, 'el_c0000000');
    pressKey(editor, 'Enter');

    const ops = lastOps(dispatchOps);
    const inserted = ops[0] as Extract<ElementOp, { op: 'insert' }>;
    expect(inserted.after_id).toBe('el_c0000000');
    expect(inserted.payload.type).toBe(ENTER_NEW_TYPE.character);
    expect(applyLocal(before, ops)).toEqual(docToElements(editor.state.doc));
  });

  it('dialogue → new dialogue after current', async () => {
    const before = body();
    const { editor, dispatchOps } = await mount(before);
    setCaretAtEnd(editor, 'el_d0000000');
    pressKey(editor, 'Enter');

    const ops = lastOps(dispatchOps);
    const inserted = ops[0] as Extract<ElementOp, { op: 'insert' }>;
    expect(inserted.after_id).toBe('el_d0000000');
    expect(inserted.payload.type).toBe(ENTER_NEW_TYPE.dialogue);
    expect(applyLocal(before, ops)).toEqual(docToElements(editor.state.doc));
  });

  it('transition → new action after current', async () => {
    const before = body();
    const { editor, dispatchOps } = await mount(before);
    setCaretAtEnd(editor, 'el_t0000000');
    pressKey(editor, 'Enter');
    const inserted = lastOps(dispatchOps)[0] as Extract<ElementOp, { op: 'insert' }>;
    expect(inserted.payload.type).toBe(ENTER_NEW_TYPE.transition);
  });

  it('comment → new action after current', async () => {
    const before = body();
    const { editor, dispatchOps } = await mount(before);
    setCaretAtEnd(editor, 'el_m0000000');
    pressKey(editor, 'Enter');
    const inserted = lastOps(dispatchOps)[0] as Extract<ElementOp, { op: 'insert' }>;
    expect(inserted.payload.type).toBe(ENTER_NEW_TYPE.comment);
  });

  it('subtitle → new action after current', async () => {
    const before = body();
    const { editor, dispatchOps } = await mount(before);
    setCaretAtEnd(editor, 'el_s0000000');
    pressKey(editor, 'Enter');
    const inserted = lastOps(dispatchOps)[0] as Extract<ElementOp, { op: 'insert' }>;
    expect(inserted.payload.type).toBe(ENTER_NEW_TYPE.subtitle);
  });

  it('paren with a following dialogue → moves caret into it, NO ops dispatched', async () => {
    const before = body();
    const { editor, dispatchOps, onFocusCursor } = await mount(before);
    setCaretAtEnd(editor, 'el_p0000000');
    pressKey(editor, 'Enter');

    expect(dispatchOps).not.toHaveBeenCalled();
    expect(docToElements(editor.state.doc)).toEqual(before);
    expect(onFocusCursor).toHaveBeenLastCalledWith('el_d0000000');
  });

  it('paren with NO following dialogue → new dialogue inserted after it', async () => {
    const before = [el('el_c0000000', 'character', 'X'), el('el_p0000000', 'paren', '(x)')];
    const { editor, dispatchOps } = await mount(before);
    setCaretAtEnd(editor, 'el_p0000000');
    pressKey(editor, 'Enter');

    const ops = lastOps(dispatchOps);
    const inserted = ops[0] as Extract<ElementOp, { op: 'insert' }>;
    expect(inserted.after_id).toBe('el_p0000000');
    expect(inserted.payload.type).toBe('dialogue');
    expect(applyLocal(before, ops)).toEqual(docToElements(editor.state.doc));
  });

  it('mid-text Enter splits: current keeps the head, new node gets the tail + a fresh id', async () => {
    const before = body(); // el_a0000000 text = 'They enter.'
    const { editor, dispatchOps } = await mount(before);
    setCaret(editor, 'el_a0000000', 5); // caret between 'They ' and 'enter.'
    pressKey(editor, 'Enter');

    const ops = lastOps(dispatchOps);
    const after = docToElements(editor.state.doc);
    expect(applyLocal(before, ops)).toEqual(after);

    const headEl = after.find((e) => e.id === 'el_a0000000')!;
    expect(headEl.text).toBe('They ');
    const insertOp = ops.find((o) => o.op === 'insert') as Extract<ElementOp, { op: 'insert' }>;
    expect(insertOp.payload.text).toBe('enter.');
    expect(insertOp.element_id).not.toBe('el_a0000000');
    // The tail landed as a NEW node right after the head, matching the
    // ENTER_NEW_TYPE for 'action'.
    expect(insertOp.payload.type).toBe(ENTER_NEW_TYPE.action);
  });
});

describe('Tab — TAB_TYPE retype (parity with editorMachine)', () => {
  const cases: Array<[string, ScriptElement['type']]> = [
    ['el_a0000000', 'action'],
    ['el_c0000000', 'character'],
    ['el_p0000000', 'paren'],
    ['el_d0000000', 'dialogue'],
    ['el_t0000000', 'transition'],
    ['el_m0000000', 'comment'],
    ['el_s0000000', 'subtitle'],
  ];

  cases.forEach(([id, type]) => {
    it(`${type} → ${TAB_TYPE[type]} (update op, text/id unchanged)`, async () => {
      const before = body();
      const { editor, dispatchOps } = await mount(before);
      setCaret(editor, id, 0);
      pressKey(editor, 'Tab');

      const ops = lastOps(dispatchOps);
      expect(ops).toHaveLength(1);
      expect(ops[0]).toMatchObject({ op: 'update', element_id: id, payload: { type: TAB_TYPE[type] } });
      const after = docToElements(editor.state.doc);
      expect(applyLocal(before, ops)).toEqual(after);
      // Text and id survive a retype untouched.
      const beforeEl = before.find((e) => e.id === id)!;
      const afterEl = after.find((e) => e.id === id)!;
      expect(afterEl.text).toBe(beforeEl.text);
    });
  });
});

describe('Shift-Tab — SHIFT_TAB_TYPE retype + dialogue special case', () => {
  const cases: Array<[string, ScriptElement['type']]> = [
    ['el_a0000000', 'action'],
    ['el_c0000000', 'character'],
    ['el_p0000000', 'paren'],
    ['el_t0000000', 'transition'],
    ['el_m0000000', 'comment'],
    ['el_s0000000', 'subtitle'],
  ];

  cases.forEach(([id, type]) => {
    it(`${type} → ${SHIFT_TAB_TYPE[type]}`, async () => {
      const before = body();
      const { editor, dispatchOps } = await mount(before);
      setCaret(editor, id, 0);
      pressKey(editor, 'Tab', { shiftKey: true });

      const ops = lastOps(dispatchOps);
      expect(ops[0]).toMatchObject({
        op: 'update',
        element_id: id,
        payload: { type: SHIFT_TAB_TYPE[type] },
      });
      expect(applyLocal(before, ops)).toEqual(docToElements(editor.state.doc));
    });
  });

  it('dialogue with a preceding character → caret to its end, NO ops', async () => {
    const before = body();
    const { editor, dispatchOps, onFocusCursor } = await mount(before);
    setCaret(editor, 'el_d0000000', 0);
    pressKey(editor, 'Tab', { shiftKey: true });

    expect(dispatchOps).not.toHaveBeenCalled();
    expect(docToElements(editor.state.doc)).toEqual(before);
    expect(onFocusCursor).toHaveBeenLastCalledWith('el_c0000000');
    const { end } = findElementPos(editor, 'el_c0000000');
    expect(editor.state.selection.from).toBe(end);
  });

  it('dialogue with NO preceding character → retype to character', async () => {
    const before = [el('el_a0000000', 'action', 'x'), el('el_d0000000', 'dialogue', 'hi')];
    const { editor, dispatchOps } = await mount(before);
    setCaret(editor, 'el_d0000000', 0);
    pressKey(editor, 'Tab', { shiftKey: true });

    const ops = lastOps(dispatchOps);
    expect(ops[0]).toMatchObject({ op: 'update', element_id: 'el_d0000000', payload: { type: 'character' } });
    expect(applyLocal(before, ops)).toEqual(docToElements(editor.state.doc));
  });
});

describe('Backspace at node start', () => {
  it('empty node (not first) → delete + caret to previous node end', async () => {
    const before = [el('el_a0000000', 'action', 'x'), el('el_c0000000', 'character', '')];
    const { editor, dispatchOps } = await mount(before);
    setCaret(editor, 'el_c0000000', 0);
    pressKey(editor, 'Backspace');

    const ops = lastOps(dispatchOps);
    expect(ops).toEqual([{ op: 'delete', element_id: 'el_c0000000' }]);
    const after = docToElements(editor.state.doc);
    expect(applyLocal(before, ops)).toEqual(after);
    expect(after).toHaveLength(1);
    const { end } = findElementPos(editor, 'el_a0000000');
    expect(editor.state.selection.from).toBe(end);
  });

  it('empty FIRST node (siblings remain) → delete + caret to new first node start', async () => {
    const before = [el('el_a0000000', 'action', ''), el('el_c0000000', 'character', 'X')];
    const { editor, dispatchOps } = await mount(before);
    setCaret(editor, 'el_a0000000', 0);
    pressKey(editor, 'Backspace');

    const ops = lastOps(dispatchOps);
    expect(ops).toEqual([{ op: 'delete', element_id: 'el_a0000000' }]);
    const after = docToElements(editor.state.doc);
    expect(after).toEqual([el('el_c0000000', 'character', 'X')]);
    expect(editor.state.selection.from).toBe(1);
  });

  it('non-empty element at offset 0 → NO-OP (never joins lines)', async () => {
    const before = [el('el_a0000000', 'action', 'hello')];
    const { editor, dispatchOps } = await mount(before);
    setCaret(editor, 'el_a0000000', 0);
    pressKey(editor, 'Backspace');

    expect(dispatchOps).not.toHaveBeenCalled();
    expect(docToElements(editor.state.doc)).toEqual(before);
  });

  it('sole remaining node → NO-OP even when empty (schema requires scriptElement+)', async () => {
    const before = [el('el_a0000000', 'action', '')];
    const { editor, dispatchOps } = await mount(before);
    setCaret(editor, 'el_a0000000', 0);
    pressKey(editor, 'Backspace');

    expect(dispatchOps).not.toHaveBeenCalled();
    expect(docToElements(editor.state.doc)).toEqual(before);
    expect(editor.state.doc.childCount).toBe(1);
  });

  it('mid/end-of-text Backspace defers to the browser (not intercepted)', async () => {
    const before = [el('el_a0000000', 'action', 'hello')];
    const { editor, dispatchOps } = await mount(before);
    setCaret(editor, 'el_a0000000', 3);
    pressKey(editor, 'Backspace');

    // Our handler returns false at non-zero offset — PM's OWN default
    // character-delete still isn't wired without the core Keymap extension
    // present (we deliberately don't include StarterKit), so the doc is
    // simply unaffected either way; the important assertion is that our
    // machine did NOT fire (no ops).
    expect(dispatchOps).not.toHaveBeenCalled();
  });
});

describe('IME composition guard', () => {
  it('Enter/Tab/Backspace are inert while editor.view.composing is true', async () => {
    const before = body();
    const { editor, dispatchOps } = await mount(before);
    setCaretAtEnd(editor, 'el_a0000000');

    // jsdom cannot simulate a real native IME composition session — flip the
    // exact internal flag our handlers read (`editor.view.composing`, backed
    // by `view.input.composing`) the same way a real compositionstart event
    // would, per prosemirror-view's own implementation.
    (editor.view as unknown as { input: { composing: boolean } }).input.composing = true;
    try {
      pressKey(editor, 'Enter');
      pressKey(editor, 'Tab');
      pressKey(editor, 'Backspace');
      expect(dispatchOps).not.toHaveBeenCalled();
      expect(docToElements(editor.state.doc)).toEqual(before);
    } finally {
      (editor.view as unknown as { input: { composing: boolean } }).input.composing = false;
    }
  });
});
