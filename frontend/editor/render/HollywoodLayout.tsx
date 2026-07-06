/**
 * Hollywood layout engine (spec v3 §3.3 / D8 column 1).
 *
 * Maps each ScriptElement type to its Hollywood typeset class and renders the
 * shared ElementLine per row. This is a *layout engine*, not a theme: the Asian
 * engine (Task 7) consumes the same elements and only swaps the per-type class
 * map. All interaction (keydown machine, debounced input, paste) is owned by
 * SceneBlock and passed down as `handlers`, so this component stays presentational.
 */
import { ElementLine, type ElementLineProps, type LineMention } from './layoutShared';
import type { ElementType, ScriptElement } from '../types';

export const HOLLYWOOD_LINE_CLASS: Record<ElementType, string> = {
  action: 'hw-action',
  character: 'hw-character',
  dialogue: 'hw-dialogue',
  paren: 'hw-paren',
  transition: 'hw-transition',
  comment: 'hw-comment',
  subtitle: 'hw-subtitle',
};

export type LayoutHandlers = Pick<
  ElementLineProps,
  'onInput' | 'onKeyDown' | 'onFocus' | 'onPaste' | 'onCompositionStart' | 'onCompositionEnd'
>;

export interface HollywoodLayoutProps {
  elements: ScriptElement[];
  focusedElementId: string | null;
  handlers: LayoutHandlers;
  /** The open mention picker (if any) — its ARIA is applied to the matching line. */
  mention?: LineMention | null;
  /** Copilot-selected element ids (their gutter ticks render pressed). */
  selectedIds?: Set<string>;
  /** Gutter-tick click → copilot selection (Task 11). */
  onTickClick?: (elementId: string, shiftKey: boolean) => void;
}

export function HollywoodLayout({
  elements,
  focusedElementId,
  handlers,
  mention,
  selectedIds,
  onTickClick,
}: HollywoodLayoutProps) {
  return (
    <>
      {elements.map((el) => (
        <ElementLine
          key={el.id}
          element={el}
          lineClass={HOLLYWOOD_LINE_CLASS[el.type]}
          focused={focusedElementId === el.id}
          selected={selectedIds?.has(el.id)}
          onTickClick={onTickClick}
          mentionAria={
            mention && mention.elementId === el.id
              ? {
                  listboxId: mention.listboxId,
                  activeOptionId: mention.activeOptionId,
                  expanded: mention.expanded,
                }
              : undefined
          }
          {...handlers}
        />
      ))}
    </>
  );
}
