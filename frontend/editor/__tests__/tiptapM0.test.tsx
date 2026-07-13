/**
 * M0 spike component smoke test — a REAL TipTap editor mounted in jsdom
 * (mirrors components/Inspiration/NoteEditor.test.tsx's polyfill setup,
 * the first real-tiptap test in this repo). Verifies:
 *  - the NodeView renders today's row DOM (spec D5): .mh-el-row > .mh-el-gutter
 *    [.mh-el-num + .mh-el-drag[.mh-el-dot x4]] + .mh-el-tick + the
 *    contentEditable line with hw-<type> / data-el-<attr> attrs.
 *  - continuous numbering (blockIndexBase + 2 + sibling index).
 *  - typing drives a real onUpdate → mapDocChange → onOps whose applyLocal
 *    result matches (the golden law, exercised end-to-end through a mounted
 *    editor instead of just the pure functions in tiptapM0.test.ts).
 */
import { describe, expect, it, vi } from 'vitest';
import { act, render, waitFor } from '@testing-library/react';
import type { Editor } from '@tiptap/core';
import { TipTapSceneEditor } from '../tiptap/TipTapSceneEditor';
import { applyLocal } from '../opBuilder';
import type { ElementOp, ScriptElement } from '../types';

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

const elements: ScriptElement[] = [
  { id: 'e1', type: 'action', text: 'INT. HOUSE - DAY', character_id: null },
  { id: 'e2', type: 'character', text: 'ALICE', character_id: null },
  { id: 'e3', type: 'dialogue', text: 'Hello there.', character_id: null },
];

function getEditor(): Editor {
  return (window as unknown as Record<string, unknown>).__tipTapSceneEditorInstance as Editor;
}

describe('TipTapSceneEditor (M0 smoke)', () => {
  it("renders today's row DOM with continuous numbering", async () => {
    render(<TipTapSceneEditor elements={elements} format="hollywood" />);
    await waitFor(() => expect(document.querySelectorAll('.mh-el-row').length).toBe(3));

    expect(document.querySelectorAll('.mh-el-row')).toHaveLength(3);
    expect(document.querySelectorAll('.mh-el-gutter')).toHaveLength(3);
    expect(document.querySelectorAll('.mh-el-drag')).toHaveLength(3);
    expect(document.querySelectorAll('.mh-el-dot')).toHaveLength(12);
    expect(document.querySelectorAll('.mh-el-tick')).toHaveLength(3);

    const editables = document.querySelectorAll('.mh-el-editable.mh-el-line');
    expect(editables).toHaveLength(3);
    expect(editables[0].className).toContain('hw-action');
    expect(editables[1].className).toContain('hw-character');
    expect(editables[2].className).toContain('hw-dialogue');
    expect(editables[0].getAttribute('data-el-type')).toBe('action');
    expect(editables[0].getAttribute('data-el-id')).toBe('e1');
    expect(editables[1].getAttribute('data-el-id')).toBe('e2');
    expect(editables[2].getAttribute('data-el-id')).toBe('e3');

    // blockIndexBase defaults to 0 → displayed number = 0 + 2 + siblingIndex.
    const nums = Array.from(document.querySelectorAll('.mh-el-num')).map((n) => n.textContent);
    expect(nums).toEqual(['2', '3', '4']);
  });

  it('offsets numbering by blockIndexBase', async () => {
    render(<TipTapSceneEditor elements={elements} format="hollywood" blockIndexBase={10} />);
    await waitFor(() => expect(document.querySelectorAll('.mh-el-row').length).toBe(3));
    const nums = Array.from(document.querySelectorAll('.mh-el-num')).map((n) => n.textContent);
    expect(nums).toEqual(['12', '13', '14']);
  });

  it('renders Asian-format classes when format="asian"', async () => {
    render(<TipTapSceneEditor elements={elements} format="asian" />);
    await waitFor(() => expect(document.querySelectorAll('.mh-el-row').length).toBe(3));
    const editables = document.querySelectorAll('.mh-el-editable.mh-el-line');
    expect(editables[0].className).toContain('as-action');
    expect(editables[1].className).toContain('as-character');
  });

  it('typing emits ops through onOps whose applyLocal matches the edit', async () => {
    const onOps = vi.fn();
    render(<TipTapSceneEditor elements={elements} format="hollywood" onOps={onOps} />);
    await waitFor(() => expect(getEditor()).toBeTruthy());

    act(() => {
      // Position 1 = the very start of the first scriptElement's text content
      // (position 0 is before the node itself opens).
      getEditor().commands.insertContentAt(1, '!! ');
    });

    await waitFor(() => expect(onOps).toHaveBeenCalled());
    const emittedOps: ElementOp[] = onOps.mock.calls.flatMap(
      (call) => call[0] as ElementOp[],
    );
    expect(emittedOps.length).toBeGreaterThan(0);
    const result = applyLocal(elements, emittedOps);
    expect(result.find((e) => e.id === 'e1')?.text).toBe('!! INT. HOUSE - DAY');
    // The other two rows were untouched by this edit.
    expect(result.find((e) => e.id === 'e2')?.text).toBe('ALICE');
    expect(result.find((e) => e.id === 'e3')?.text).toBe('Hello there.');
  });
});
