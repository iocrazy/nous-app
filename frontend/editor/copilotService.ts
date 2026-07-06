/**
 * Copilot structured-edit helpers (spec v3 §3.2 / §3.4, Task 11 — Phase 1).
 *
 * Phase 1 has no backend copilot endpoint, so the summoned card runs LOCAL,
 * deterministic transforms over the selected elements and dispatches the result
 * through the owning scene's op queue (actor stays the user's token). These are
 * pure functions so the transform is unit-testable without a DOM.
 */
import type { ElementOp, ElementType, ScriptElement } from './types';

/**
 * "Polish format" for one element's text: trim the ends and collapse internal
 * whitespace runs to a single space; character cues are additionally upcased
 * (screenplay convention). Returns the cleaned text (may equal the input).
 */
export function polishText(text: string, type: ElementType): string {
  const cleaned = text.trim().replace(/\s+/g, ' ');
  return type === 'character' ? cleaned.toUpperCase() : cleaned;
}

/**
 * Build the update ops that polish the selected elements. Only elements whose
 * text actually changes produce an op — a no-op selection yields an empty batch
 * (nothing dispatched, nothing to undo).
 */
export function buildPolishOps(elements: ScriptElement[], selectedIds: string[]): ElementOp[] {
  const selected = new Set(selectedIds);
  const ops: ElementOp[] = [];
  for (const el of elements) {
    if (!selected.has(el.id)) continue;
    const next = polishText(el.text, el.type);
    if (next !== el.text) {
      ops.push({ op: 'update', element_id: el.id, payload: { text: next } });
    }
  }
  return ops;
}
