/**
 * Keyboard state machine for the v2 editor (spec v3 §3.2 D7). Pure TypeScript,
 * ZERO React — every transition is a function of (elements, cursor) → result,
 * so the whole thing is unit-testable without a DOM.
 *
 * Each keystroke returns a `MachineResult`: the ops to send to the server
 * (anchored: inserts carry `after_id`/`before_id`, type changes are `update`
 * ops), the cursor's landing spot, and `localElements` — the ops already
 * applied locally so the caller can render optimistically before the round
 * trip. Inputs are never mutated (all mutation goes through opBuilder, which
 * returns fresh structures).
 *
 * Scene-heading fields (int/ext, location, time) are NOT elements. Tab/Shift-Tab
 * on a heading field only move the cursor (empty ops); only Enter on a heading
 * materialises an element (a new action at the head of the scene).
 */
import type { ElementOp, ElementType, ScriptElement } from './types';
import { newElementId } from './sceneService';
import { applyLocal } from './opBuilder';
import { cycleType, TAB_TYPE, SHIFT_TAB_TYPE, ENTER_NEW_TYPE } from './machineTables';

export interface CursorState {
  sceneId: string;
  elementId: string | null;
  field: 'element' | 'heading_int_ext' | 'location' | 'time';
}

export interface MachineResult {
  cursor: CursorState;
  ops: ElementOp[];
  localElements: ScriptElement[];
}

// Type-derivation tables (CYCLE / TAB_TYPE / SHIFT_TAB_TYPE / ENTER_NEW_TYPE)
// live in `./machineTables` — shared verbatim with the TipTap keymap
// extension (`tiptap/keymap.ts`) so both engines stay in lockstep by
// construction. `cycleType` is re-exported here for existing callers.
export { cycleType };

/** Forward ring for heading-field navigation; last stop enters the elements. */
const HEADING_RING: CursorState['field'][] = [
  'heading_int_ext',
  'location',
  'time',
  'element',
];

function findElement(elements: ScriptElement[], id: string | null): ScriptElement | null {
  if (id == null) return null;
  return elements.find((e) => e.id === id) ?? null;
}

/** A cursor-only move / no-op: fresh cursor, empty ops, fresh element copy. */
function noOp(elements: ScriptElement[], cursor: CursorState): MachineResult {
  return { cursor: { ...cursor }, ops: [], localElements: applyLocal(elements, []) };
}

function focusMove(
  elements: ScriptElement[],
  cursor: CursorState,
  elementId: string | null,
): MachineResult {
  return {
    cursor: { sceneId: cursor.sceneId, elementId, field: 'element' },
    ops: [],
    localElements: applyLocal(elements, []),
  };
}

function updateTypeResult(
  elements: ScriptElement[],
  cursor: CursorState,
  newType: ElementType,
): MachineResult {
  if (cursor.elementId == null) return noOp(elements, cursor);
  const ops: ElementOp[] = [
    { op: 'update', element_id: cursor.elementId, payload: { type: newType } },
  ];
  return { cursor: { ...cursor }, ops, localElements: applyLocal(elements, ops) };
}

/** Insert a new element, anchored either after or before a sibling. */
function insertResult(
  elements: ScriptElement[],
  sceneId: string,
  anchor: { after_id?: string | null; before_id?: string | null },
  type: ElementType,
): MachineResult {
  const id = newElementId();
  const op: ElementOp = { op: 'insert', element_id: id, payload: { type, text: '' } };
  if (anchor.after_id != null) op.after_id = anchor.after_id;
  if (anchor.before_id != null) op.before_id = anchor.before_id;
  const ops = [op];
  return {
    cursor: { sceneId, elementId: id, field: 'element' },
    ops,
    localElements: applyLocal(elements, ops),
  };
}

/** Heading Tab/Shift-Tab: move around the field ring, never emit ops. */
function headingNav(
  elements: ScriptElement[],
  cursor: CursorState,
  dir: 1 | -1,
): MachineResult {
  const i = HEADING_RING.indexOf(cursor.field);
  const nextField = HEADING_RING[(i + dir + HEADING_RING.length) % HEADING_RING.length];
  let elementId: string | null = null;
  if (nextField === 'element') {
    // Forward entry lands on the first element, backward on the last.
    if (elements.length > 0) {
      elementId = dir > 0 ? elements[0].id : elements[elements.length - 1].id;
    }
  }
  return {
    cursor: { sceneId: cursor.sceneId, elementId, field: nextField },
    ops: [],
    localElements: applyLocal(elements, []),
  };
}

export function onEnter(elements: ScriptElement[], cursor: CursorState): MachineResult {
  // Heading field → materialise a new action at the head of the scene.
  if (cursor.field !== 'element') {
    const firstId = elements.length > 0 ? elements[0].id : null;
    return insertResult(elements, cursor.sceneId, { before_id: firstId }, 'action');
  }

  const el = findElement(elements, cursor.elementId);
  if (!el) {
    // Cursor sits in an empty scene body → seed the first action.
    return insertResult(elements, cursor.sceneId, {}, 'action');
  }

  if (el.type === 'paren') {
    // Paren usually sits inside a dialogue block: step into the following
    // dialogue if present, otherwise create one.
    const idx = elements.findIndex((e) => e.id === el.id);
    const next = idx + 1 < elements.length ? elements[idx + 1] : null;
    if (next && next.type === 'dialogue') {
      return focusMove(elements, cursor, next.id);
    }
    return insertResult(elements, cursor.sceneId, { after_id: el.id }, 'dialogue');
  }

  return insertResult(elements, cursor.sceneId, { after_id: el.id }, ENTER_NEW_TYPE[el.type]);
}

export function onTab(elements: ScriptElement[], cursor: CursorState): MachineResult {
  if (cursor.field !== 'element') return headingNav(elements, cursor, 1);
  const el = findElement(elements, cursor.elementId);
  if (!el) return noOp(elements, cursor);
  return updateTypeResult(elements, cursor, TAB_TYPE[el.type]);
}

export function onShiftTab(elements: ScriptElement[], cursor: CursorState): MachineResult {
  if (cursor.field !== 'element') return headingNav(elements, cursor, -1);
  const el = findElement(elements, cursor.elementId);
  if (!el) return noOp(elements, cursor);

  if (el.type === 'dialogue') {
    // Step back to the preceding character cue if there is one; else fall back
    // to turning this line into a character.
    const idx = elements.findIndex((e) => e.id === el.id);
    for (let i = idx - 1; i >= 0; i -= 1) {
      if (elements[i].type === 'character') {
        return focusMove(elements, cursor, elements[i].id);
      }
    }
    return updateTypeResult(elements, cursor, 'character');
  }

  return updateTypeResult(elements, cursor, SHIFT_TAB_TYPE[el.type]);
}

export function onBackspaceAtStart(
  elements: ScriptElement[],
  cursor: CursorState,
): MachineResult {
  if (cursor.field !== 'element' || cursor.elementId == null) return noOp(elements, cursor);
  const idx = elements.findIndex((e) => e.id === cursor.elementId);
  if (idx < 0) return noOp(elements, cursor);
  const el = elements[idx];
  // Non-empty line: let the browser delete a character.
  if (el.text !== '') return noOp(elements, cursor);
  // Empty line: remove it and pull the cursor up to the previous element.
  const prevId = idx - 1 >= 0 ? elements[idx - 1].id : null;
  const ops: ElementOp[] = [{ op: 'delete', element_id: cursor.elementId }];
  return {
    cursor: { sceneId: cursor.sceneId, elementId: prevId, field: 'element' },
    ops,
    localElements: applyLocal(elements, ops),
  };
}

/**
 * Insert a new element of `type` after `afterId` (append when null). Standalone
 * helper for the toolbar / paste paths — the returned cursor carries no scene
 * (sceneId '') because callers supply the scene context.
 */
export function insertAfter(
  elements: ScriptElement[],
  afterId: string | null,
  type: ElementType,
): MachineResult {
  return insertResult(elements, '', { after_id: afterId }, type);
}
