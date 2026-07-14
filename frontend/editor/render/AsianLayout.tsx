/**
 * Asian typeset class map + ornament marks (spec v3 §3.3 / D8 column 2).
 *
 * The TipTap NodeView (`TipTapSceneEditor`, M3 spec D5) is the sole consumer:
 * it reads `ASIAN_LINE_CLASS[type]` to class each row, and renders the leading
 * `△` (action) / trailing `：` (character cue) ornaments from `ASIAN_PREFIX` /
 * `ASIAN_SUFFIX` — one source, no hand-kept copy. The legacy contentEditable
 * layout-engine component that used to live here was retired when TipTap became
 * the only editing surface.
 */
import type { ElementType } from '../types';

export const ASIAN_LINE_CLASS: Record<ElementType, string> = {
  action: 'as-action',
  character: 'as-character',
  dialogue: 'as-dialogue',
  paren: 'as-paren',
  transition: 'as-transition',
  comment: 'as-comment',
  subtitle: 'as-subtitle',
};

/** Rows that carry a leading ornament (rendered before the editable line). */
export const ASIAN_PREFIX: Partial<Record<ElementType, string>> = {
  action: '△',
};

/** Rows that carry a trailing label ornament (rendered after the editable line).
 *  Character cue uses the FULLWIDTH colon `：` — Chinese punctuation, per the
 *  国内剧本 "角色名：对白" convention (halfwidth `:` reads as a Latin colon). */
export const ASIAN_SUFFIX: Partial<Record<ElementType, string>> = {
  character: '：',
};
