/**
 * Shared type-derivation tables for the screenplay keyboard semantics (spec
 * v3 §3.2 D7 / TipTap migration D4). Extracted from `editorMachine.ts` so the
 * legacy contentEditable machine and the TipTap keymap extension
 * (`tiptap/keymap.ts`) consume the EXACT SAME tables — parity by
 * construction rather than by hand-copied duplication that can drift.
 *
 * Pure data + two tiny pure functions. Zero React, zero DOM, zero op
 * building — everything else (anchoring, cursor movement, op emission) stays
 * local to each consumer because the two engines build ops very differently
 * (anchored ElementOp batches vs. PM transactions).
 */
import type { ElementType } from './types';

/**
 * Type ring for Tab cycling and the toolbar. NOTE this is deliberately a
 * different order from ELEMENT_TYPES: the spec's cycle is
 * action→character→dialogue→paren→transition→comment→subtitle→action.
 */
export const CYCLE: ElementType[] = [
  'action',
  'character',
  'dialogue',
  'paren',
  'transition',
  'comment',
  'subtitle',
];

export function cycleType(t: ElementType): ElementType {
  const i = CYCLE.indexOf(t);
  return CYCLE[(i + 1) % CYCLE.length];
}

export function cyclePrev(t: ElementType): ElementType {
  const i = CYCLE.indexOf(t);
  return CYCLE[(i - 1 + CYCLE.length) % CYCLE.length];
}

/** Tab: type change per current element type (spec §3.2 D7, Tab column). */
export const TAB_TYPE: Record<ElementType, ElementType> = {
  action: 'character',
  character: 'paren',
  dialogue: 'paren',
  paren: 'transition',
  transition: 'comment',
  comment: cycleType('comment'), // subtitle
  subtitle: cycleType('subtitle'), // action
};

/** Shift-Tab: type change per current element type (dialogue is special). */
export const SHIFT_TAB_TYPE: Record<ElementType, ElementType> = {
  action: cyclePrev('action'), // subtitle
  character: 'action',
  dialogue: 'character', // fallback only — see onShiftTab / keymap's dialogue branch
  paren: 'dialogue',
  transition: 'paren',
  comment: cyclePrev('comment'), // transition
  subtitle: cyclePrev('subtitle'), // comment
};

/** Enter: element type to create per current type (paren is special). */
export const ENTER_NEW_TYPE: Record<ElementType, ElementType> = {
  action: 'action',
  character: 'dialogue',
  dialogue: 'dialogue',
  paren: 'dialogue', // used only when no following dialogue exists
  transition: 'action',
  comment: 'action',
  subtitle: 'action',
};
