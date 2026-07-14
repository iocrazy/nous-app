/**
 * Hollywood typeset class map (spec v3 §3.3 / D8 column 1).
 *
 * Maps each ScriptElement type to its Hollywood layout class. The TipTap
 * NodeView (`TipTapSceneEditor`) is the sole consumer — it reads
 * `HOLLYWOOD_LINE_CLASS[type]` to class each row's editable line. The legacy
 * contentEditable layout-engine component that used to live here was retired
 * when TipTap became the only editing surface.
 */
import type { ElementType } from '../types';

export const HOLLYWOOD_LINE_CLASS: Record<ElementType, string> = {
  action: 'hw-action',
  character: 'hw-character',
  dialogue: 'hw-dialogue',
  paren: 'hw-paren',
  transition: 'hw-transition',
  comment: 'hw-comment',
  subtitle: 'hw-subtitle',
};
