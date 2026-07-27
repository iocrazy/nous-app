/**
 * Deliverable-form fill-state helpers (mig 390, M3 PR-I §2, task I4).
 *
 * Mirrors the backend's `_form_incomplete` predicate
 * (`app/services/workflow/advance_service.py`) EXACTLY, field type by field
 * type, so the Stage Board's required badge and CurrentNodeCard's "Form N/M"
 * completion count never disagree with what the advance gate will actually
 * enforce:
 *   - text/textarea/select/date: the value must be a non-empty string
 *     (a missing key counts as empty)
 *   - number: the KEY must be present in `data` — `0` counts as filled, so
 *     this is a presence check, not a truthiness check
 *   - checkbox: the value must be exactly `true` — `false` (or a missing
 *     key) does not count as filled
 *
 * Pure, no React — cheap to unit-test directly and reused by both
 * `StageNodeForm` (required badge, per-field) and `CurrentNodeCard` (the
 * aggregate N/M count, which counts ALL fields regardless of `required`).
 */
import type { FormFieldDef } from '../../types';

/** Whether a single field counts as "filled" per its type. Ignores
 * `field.required` entirely — callers decide whether an unfilled field
 * matters (the required badge does; the CurrentNodeCard completion count
 * does not, it counts fill state across every field). */
export function isFieldFilled(field: FormFieldDef, data: Record<string, unknown>): boolean {
  if (field.type === 'number') {
    return field.key in data;
  }
  if (field.type === 'checkbox') {
    return data[field.key] === true;
  }
  // text / textarea / select / date
  const value = data[field.key];
  return typeof value === 'string' && value.trim().length > 0;
}

/** Count of filled fields out of the total, across the WHOLE schema
 * (required or not) — the "Form 3/5" completion row's number pair. */
export function countFilledFields(
  schema: FormFieldDef[],
  data: Record<string, unknown>,
): { filled: number; total: number } {
  return {
    filled: schema.filter((f) => isFieldFilled(f, data)).length,
    total: schema.length,
  };
}
