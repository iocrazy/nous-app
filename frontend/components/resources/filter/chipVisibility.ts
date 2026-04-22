// frontend/components/resources/filter/chipVisibility.ts
//
// Pure helpers for applying a per-scope chip allowlist to the
// pinnable FilterBar. Extracted so unit tests can exercise the
// filtering contract without mounting the component.
//
// Contract:
//   - When `allowedChips` is undefined / nullish → pass through
//     the input list unchanged (no filtering).
//   - When provided → return a new array preserving the original
//     order, keeping only ids that appear in the allowlist.
//   - Input arrays are never mutated (immutability).
//
// A chip's persisted pin state lives in localStorage at the
// useFilterBarConfig layer. Hiding a chip here is purely a render
// concern — the user's pin choice for that chip is preserved and
// will resurface in any scope that allows it.

import type { ChipId } from './types';

export function filterVisibleChips(
  chips: ReadonlyArray<ChipId>,
  allowedChips: ReadonlyArray<ChipId> | null | undefined,
): ChipId[] {
  if (!allowedChips) return [...chips];
  const allowed = new Set<ChipId>(allowedChips);
  return chips.filter((id) => allowed.has(id));
}
