/**
 * Client-side anchor-op application + inverse — a faithful port of the backend
 * single-source-of-truth (`backend/app/services/script/scene_ops.py`).
 *
 * `applyLocal` mutates a scene's ordered element list in response to a batch of
 * ops, immutably (inputs are never touched — fresh array, fresh objects for the
 * elements an op reaches). `buildInverse` produces the batch that undoes those
 * ops, ordered so that `applyLocal(applyLocal(e, ops), buildInverse(ops, e))`
 * reproduces `e` exactly. Both power optimistic editing (editorMachine) and, in
 * a later task, useSceneSync's optimistic view + the copilot Undo.
 *
 * Semantics mirror the backend exactly:
 *  - insert: upsert-in-place-no-move when the id already exists; otherwise
 *    anchored insert (before_id wins, else after_id, else append).
 *  - update: shallow merge of the payload into the element.
 *  - delete: idempotent (missing element is a no-op with no inverse).
 *  - move: pop + re-anchor; a self-anchor is a no-op.
 * Inverses match the backend: insert→delete, update/delete→insert carrying the
 * FULL prior element payload (id preserved), move→move back to the original
 * neighbour anchor.
 */
import type { ElementOp, ScriptElement } from './types';

type ElementPayload = Omit<ScriptElement, 'id'>;

function indexOf(elements: ScriptElement[], id: string | null | undefined): number {
  if (id == null) return -1;
  return elements.findIndex((e) => e.id === id);
}

/** Full element payload minus its id (used to rebuild an element on undo). */
function payloadWithoutId(element: ScriptElement): ElementPayload {
  const { id: _id, ...rest } = element;
  return { ...rest };
}

/**
 * Resolve an insertion index from anchors (before_id wins). Returns null when
 * no anchor is supplied — the caller appends at the tail. Defensive: an anchor
 * that does not resolve also returns null (append) rather than throwing, so an
 * optimistic view never crashes on a stale anchor.
 */
function anchorIndex(
  elements: ScriptElement[],
  beforeId?: string | null,
  afterId?: string | null,
): number | null {
  if (beforeId != null) {
    const i = indexOf(elements, beforeId);
    return i < 0 ? null : i;
  }
  if (afterId != null) {
    const i = indexOf(elements, afterId);
    return i < 0 ? null : i + 1;
  }
  return null;
}

/**
 * Anchors that pin `index`'s current position for a later re-insert:
 * `[before_id, after_id]` where before_id = the id currently after `index`
 * (re-insert before it lands back at `index`), falling back to after_id = the
 * element before `index`. Both null when the list held a single element.
 */
function neighborAnchor(
  elements: ScriptElement[],
  index: number,
): [string | null, string | null] {
  const nextId = index + 1 < elements.length ? elements[index + 1].id : null;
  const prevId = index - 1 >= 0 ? elements[index - 1].id : null;
  if (nextId !== null) return [nextId, null];
  return [null, prevId];
}

interface StepResult {
  next: ScriptElement[];
  inverse: ElementOp | null;
}

function applyInsert(
  elements: ScriptElement[],
  op: Extract<ElementOp, { op: 'insert' }>,
): StepResult {
  const existing = indexOf(elements, op.element_id);
  if (existing >= 0) {
    // Upsert-in-place: replace payload, never move. Inverse upserts the prior.
    const priorPayload = payloadWithoutId(elements[existing]);
    const next = elements.slice();
    next[existing] = { id: op.element_id, ...op.payload };
    return {
      next,
      inverse: { op: 'insert', element_id: op.element_id, payload: priorPayload },
    };
  }
  const idx = anchorIndex(elements, op.before_id, op.after_id);
  const newEl: ScriptElement = { id: op.element_id, ...op.payload };
  const next = elements.slice();
  if (idx === null) next.push(newEl);
  else next.splice(idx, 0, newEl);
  return { next, inverse: { op: 'delete', element_id: op.element_id } };
}

function applyUpdate(
  elements: ScriptElement[],
  op: Extract<ElementOp, { op: 'update' }>,
): StepResult {
  const idx = indexOf(elements, op.element_id);
  if (idx < 0) return { next: elements, inverse: null };
  // Inverse is an insert-upsert carrying the FULL prior payload: a merge-back
  // update cannot remove a key this op introduced, but upsert's whole-payload
  // replace restores the element exactly.
  const priorPayload = payloadWithoutId(elements[idx]);
  const next = elements.slice();
  next[idx] = { ...elements[idx], ...op.payload };
  return {
    next,
    inverse: { op: 'insert', element_id: op.element_id, payload: priorPayload },
  };
}

function applyDelete(
  elements: ScriptElement[],
  op: Extract<ElementOp, { op: 'delete' }>,
): StepResult {
  const idx = indexOf(elements, op.element_id);
  if (idx < 0) return { next: elements, inverse: null }; // idempotent
  const [beforeId, afterId] = neighborAnchor(elements, idx);
  const priorPayload = payloadWithoutId(elements[idx]);
  const next = elements.slice();
  next.splice(idx, 1);
  return {
    next,
    inverse: {
      op: 'insert',
      element_id: op.element_id,
      payload: priorPayload,
      before_id: beforeId,
      after_id: afterId,
    },
  };
}

function applyMove(
  elements: ScriptElement[],
  op: Extract<ElementOp, { op: 'move' }>,
): StepResult {
  const idx = indexOf(elements, op.element_id);
  if (idx < 0) return { next: elements, inverse: null };
  const beforeId = op.before_id ?? null;
  const afterId = op.after_id ?? null;
  if (beforeId === op.element_id || afterId === op.element_id) {
    return { next: elements, inverse: null };
  }
  const [origBefore, origAfter] = neighborAnchor(elements, idx);
  const working = elements.slice();
  const [element] = working.splice(idx, 1);
  const target = anchorIndex(working, beforeId, afterId);
  if (target === null) working.push(element);
  else working.splice(target, 0, element);
  return {
    next: working,
    inverse: {
      op: 'move',
      element_id: op.element_id,
      before_id: origBefore,
      after_id: origAfter,
    },
  };
}

function applyOne(elements: ScriptElement[], op: ElementOp): StepResult {
  switch (op.op) {
    case 'insert':
      return applyInsert(elements, op);
    case 'update':
      return applyUpdate(elements, op);
    case 'delete':
      return applyDelete(elements, op);
    case 'move':
      return applyMove(elements, op);
    default:
      return { next: elements, inverse: null };
  }
}

/**
 * Apply `ops` to a fresh copy of `elements`, returning the new list. Inputs are
 * never mutated: the initial copy detaches from the caller's array, and every
 * touched element is a new object.
 */
export function applyLocal(elements: ScriptElement[], ops: ElementOp[]): ScriptElement[] {
  let working = elements.map((e) => ({ ...e }));
  for (const op of ops) {
    working = applyOne(working, op).next;
  }
  return working;
}

/**
 * Build the inverse batch that undoes `ops` when applied on top of the forward
 * result, computed against the same intermediate states the backend sees.
 * Returned reversed (last-applied undone first).
 */
export function buildInverse(ops: ElementOp[], baseElements: ScriptElement[]): ElementOp[] {
  let working = baseElements.map((e) => ({ ...e }));
  const inverses: ElementOp[] = [];
  for (const op of ops) {
    const { next, inverse } = applyOne(working, op);
    working = next;
    if (inverse !== null) inverses.push(inverse);
  }
  inverses.reverse();
  return inverses;
}
