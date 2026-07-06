/**
 * Transition-table coverage for the keyboard state machine (spec v3 §3.2 D7).
 * One named test per table cell plus anchor/no-mutation/inverse invariants.
 */
import { describe, it, expect } from 'vitest';
import type { CursorState } from '../editorMachine';
import {
  onEnter,
  onTab,
  onShiftTab,
  onBackspaceAtStart,
  cycleType,
  insertAfter,
} from '../editorMachine';
import { applyLocal, buildInverse } from '../opBuilder';
import type { ScriptElement, ElementOp } from '../types';

const el = (id: string, type: ScriptElement['type'], text = ''): ScriptElement => ({
  id,
  type,
  text,
});

const cur = (elementId: string | null, field: CursorState['field'] = 'element'): CursorState => ({
  sceneId: 'sc1',
  elementId,
  field,
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

const isInsert = (op: ElementOp): op is Extract<ElementOp, { op: 'insert' }> => op.op === 'insert';

describe('cycleType', () => {
  it('walks action→character→dialogue→paren→transition→comment→subtitle→action', () => {
    expect(cycleType('action')).toBe('character');
    expect(cycleType('character')).toBe('dialogue');
    expect(cycleType('dialogue')).toBe('paren');
    expect(cycleType('paren')).toBe('transition');
    expect(cycleType('transition')).toBe('comment');
    expect(cycleType('comment')).toBe('subtitle');
    expect(cycleType('subtitle')).toBe('action');
  });
});

describe('Enter transitions', () => {
  it('heading field → inserts a new action as FIRST element and enters it', () => {
    const e = body();
    const r = onEnter(e, cur(null, 'heading_int_ext'));
    expect(r.ops).toHaveLength(1);
    const op = r.ops[0];
    expect(isInsert(op) && op.before_id).toBe('el_a0000000');
    expect(isInsert(op) && op.payload.type).toBe('action');
    expect(r.localElements).toHaveLength(8);
    expect(r.localElements[0].id).toBe(r.cursor.elementId);
    expect(r.cursor.field).toBe('element');
  });

  it('heading field with empty scene → appends the first action', () => {
    const r = onEnter([], cur(null, 'time'));
    expect(r.ops).toHaveLength(1);
    const op = r.ops[0];
    expect(isInsert(op) && op.before_id).toBeUndefined();
    expect(r.localElements).toHaveLength(1);
    expect(r.cursor.elementId).toBe(r.localElements[0].id);
  });

  it('action → new action anchored after current', () => {
    const e = body();
    const r = onEnter(e, cur('el_a0000000'));
    const op = r.ops[0];
    expect(isInsert(op) && op.after_id).toBe('el_a0000000');
    expect(isInsert(op) && op.payload.type).toBe('action');
    expect(r.cursor.elementId).toBe(isInsert(op) ? op.element_id : null);
  });

  it('character → new dialogue after current', () => {
    const r = onEnter(body(), cur('el_c0000000'));
    const op = r.ops[0];
    expect(isInsert(op) && op.after_id).toBe('el_c0000000');
    expect(isInsert(op) && op.payload.type).toBe('dialogue');
  });

  it('dialogue → new dialogue after current', () => {
    const r = onEnter(body(), cur('el_d0000000'));
    const op = r.ops[0];
    expect(isInsert(op) && op.after_id).toBe('el_d0000000');
    expect(isInsert(op) && op.payload.type).toBe('dialogue');
  });

  it('paren with a following dialogue → moves cursor into it, no ops', () => {
    const r = onEnter(body(), cur('el_p0000000'));
    expect(r.ops).toHaveLength(0);
    expect(r.cursor.elementId).toBe('el_d0000000');
  });

  it('paren with no following dialogue → new dialogue after current', () => {
    const e = [el('el_c0000000', 'character', 'X'), el('el_p0000000', 'paren', '(x)')];
    const r = onEnter(e, cur('el_p0000000'));
    const op = r.ops[0];
    expect(isInsert(op) && op.after_id).toBe('el_p0000000');
    expect(isInsert(op) && op.payload.type).toBe('dialogue');
  });

  it('transition → new action after current', () => {
    const r = onEnter(body(), cur('el_t0000000'));
    expect(isInsert(r.ops[0]) && (r.ops[0] as any).payload.type).toBe('action');
  });

  it('comment → new action after current', () => {
    const r = onEnter(body(), cur('el_m0000000'));
    expect(isInsert(r.ops[0]) && (r.ops[0] as any).payload.type).toBe('action');
  });

  it('subtitle → new action after current', () => {
    const r = onEnter(body(), cur('el_s0000000'));
    expect(isInsert(r.ops[0]) && (r.ops[0] as any).payload.type).toBe('action');
  });
});

describe('Tab transitions', () => {
  it('heading int_ext → location field, no ops', () => {
    const r = onTab(body(), cur(null, 'heading_int_ext'));
    expect(r.ops).toHaveLength(0);
    expect(r.cursor.field).toBe('location');
  });
  it('heading location → time field', () => {
    const r = onTab(body(), cur(null, 'location'));
    expect(r.cursor.field).toBe('time');
    expect(r.ops).toHaveLength(0);
  });
  it('heading time → first element', () => {
    const r = onTab(body(), cur(null, 'time'));
    expect(r.cursor.field).toBe('element');
    expect(r.cursor.elementId).toBe('el_a0000000');
    expect(r.ops).toHaveLength(0);
  });

  const tabType: [string, string][] = [
    ['el_a0000000', 'character'],
    ['el_c0000000', 'paren'],
    ['el_d0000000', 'paren'],
    ['el_p0000000', 'transition'],
    ['el_t0000000', 'comment'],
    ['el_m0000000', 'subtitle'],
    ['el_s0000000', 'action'],
  ];
  tabType.forEach(([id, expected]) => {
    it(`element ${id} → update type ${expected}`, () => {
      const r = onTab(body(), cur(id));
      expect(r.ops).toHaveLength(1);
      const op = r.ops[0];
      expect(op.op).toBe('update');
      expect(op.op === 'update' && op.payload.type).toBe(expected);
      expect(r.cursor.elementId).toBe(id);
      expect(r.localElements.find((x) => x.id === id)!.type).toBe(expected);
    });
  });
});

describe('Shift-Tab transitions', () => {
  it('heading time → location', () => {
    const r = onShiftTab(body(), cur(null, 'time'));
    expect(r.cursor.field).toBe('location');
    expect(r.ops).toHaveLength(0);
  });
  it('heading location → int_ext', () => {
    const r = onShiftTab(body(), cur(null, 'location'));
    expect(r.cursor.field).toBe('heading_int_ext');
  });
  it('heading int_ext → last element (backward)', () => {
    const r = onShiftTab(body(), cur(null, 'heading_int_ext'));
    expect(r.cursor.field).toBe('element');
    expect(r.cursor.elementId).toBe('el_s0000000');
    expect(r.ops).toHaveLength(0);
  });

  it('action → prev-in-cycle type (subtitle)', () => {
    const r = onShiftTab(body(), cur('el_a0000000'));
    expect(r.ops[0].op === 'update' && r.ops[0].payload.type).toBe('subtitle');
  });
  it('character → action', () => {
    const r = onShiftTab(body(), cur('el_c0000000'));
    expect(r.ops[0].op === 'update' && r.ops[0].payload.type).toBe('action');
  });
  it('dialogue with a preceding character → focus that character, no ops', () => {
    const r = onShiftTab(body(), cur('el_d0000000'));
    expect(r.ops).toHaveLength(0);
    expect(r.cursor.elementId).toBe('el_c0000000');
  });
  it('dialogue with no preceding character → update type character', () => {
    const e = [el('el_a0000000', 'action', 'x'), el('el_d0000000', 'dialogue', 'hi')];
    const r = onShiftTab(e, cur('el_d0000000'));
    expect(r.ops[0].op === 'update' && r.ops[0].payload.type).toBe('character');
  });
  it('paren → dialogue', () => {
    const r = onShiftTab(body(), cur('el_p0000000'));
    expect(r.ops[0].op === 'update' && r.ops[0].payload.type).toBe('dialogue');
  });
  it('transition → paren', () => {
    const r = onShiftTab(body(), cur('el_t0000000'));
    expect(r.ops[0].op === 'update' && r.ops[0].payload.type).toBe('paren');
  });
  it('comment → transition (prev in cycle)', () => {
    const r = onShiftTab(body(), cur('el_m0000000'));
    expect(r.ops[0].op === 'update' && r.ops[0].payload.type).toBe('transition');
  });
  it('subtitle → comment (prev in cycle)', () => {
    const r = onShiftTab(body(), cur('el_s0000000'));
    expect(r.ops[0].op === 'update' && r.ops[0].payload.type).toBe('comment');
  });
});

describe('Backspace at start', () => {
  it('empty element → delete op + cursor moves to previous element', () => {
    const e = [el('el_a0000000', 'action', 'x'), el('el_c0000000', 'character', '')];
    const r = onBackspaceAtStart(e, cur('el_c0000000'));
    expect(r.ops).toHaveLength(1);
    expect(r.ops[0].op).toBe('delete');
    expect(r.ops[0].op === 'delete' && r.ops[0].element_id).toBe('el_c0000000');
    expect(r.cursor.elementId).toBe('el_a0000000');
    expect(r.localElements).toHaveLength(1);
  });
  it('non-empty element → no ops (browser handles)', () => {
    const e = [el('el_a0000000', 'action', 'hello')];
    const r = onBackspaceAtStart(e, cur('el_a0000000'));
    expect(r.ops).toHaveLength(0);
    expect(r.cursor.elementId).toBe('el_a0000000');
  });
});

describe('insert op ids', () => {
  it('onEnter insert op element_id matches /^el_/ and is present in localElements', () => {
    const r = onEnter(body(), cur('el_a0000000'));
    const op = r.ops[0];
    expect(isInsert(op)).toBe(true);
    const id = isInsert(op) ? op.element_id : '';
    expect(id).toMatch(/^el_[0-9a-f]{8}$/);
    expect(r.localElements.some((x) => x.id === id)).toBe(true);
  });

  it('insertAfter anchors after the given id with the requested type', () => {
    const r = insertAfter(body(), 'el_a0000000', 'transition');
    const op = r.ops[0];
    expect(isInsert(op) && op.after_id).toBe('el_a0000000');
    expect(isInsert(op) && op.payload.type).toBe('transition');
    expect(r.cursor.elementId).toMatch(/^el_/);
  });
});

describe('immutability', () => {
  it('no machine call mutates the input elements array or its members', () => {
    const e = body();
    const snapshot = JSON.parse(JSON.stringify(e));
    onEnter(e, cur('el_a0000000'));
    onTab(e, cur('el_a0000000'));
    onShiftTab(e, cur('el_d0000000'));
    onBackspaceAtStart(e, cur('el_c0000000'));
    insertAfter(e, 'el_a0000000', 'action');
    expect(e).toEqual(snapshot);
  });
});

describe('buildInverse round-trip', () => {
  it('applyLocal(applyLocal(e,ops), buildInverse(ops,e)) deep-equals e for a mixed batch', () => {
    const e = body();
    const ops: ElementOp[] = [
      { op: 'insert', element_id: 'el_new00001', after_id: 'el_a0000000', payload: { type: 'action', text: 'new' } },
      { op: 'update', element_id: 'el_c0000000', payload: { character_id: 'ch_1' } }, // adds a new key
      { op: 'delete', element_id: 'el_t0000000' },
      { op: 'move', element_id: 'el_s0000000', before_id: 'el_a0000000' },
    ];
    const forward = applyLocal(e, ops);
    const inverse = buildInverse(ops, e);
    const back = applyLocal(forward, inverse);
    expect(back).toEqual(e);
  });

  it('buildInverse returns inverses in reversed application order', () => {
    const e = body();
    const ops: ElementOp[] = [
      { op: 'delete', element_id: 'el_a0000000' },
      { op: 'update', element_id: 'el_c0000000', payload: { text: 'X' } },
    ];
    const inverse = buildInverse(ops, e);
    // last-applied (update) undoes first
    expect(inverse[0].op).toBe('insert');
    expect(inverse[0].op === 'insert' && inverse[0].element_id).toBe('el_c0000000');
    expect(inverse[1].op).toBe('insert');
    expect(inverse[1].op === 'insert' && inverse[1].element_id).toBe('el_a0000000');
  });
});
