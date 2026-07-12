/**
 * Hollywood layout engine (spec v3 §3.3 / D8 column 1).
 *
 * Maps each ScriptElement type to its Hollywood typeset class and renders the
 * shared ElementLine per row. This is a *layout engine*, not a theme: the Asian
 * engine (Task 7) consumes the same elements and only swaps the per-type class
 * map. All interaction (keydown machine, debounced input, paste) is owned by
 * SceneBlock and passed down as `handlers`, so this component stays presentational.
 */
import {
  ElementLine,
  type ElementLineProps,
  type ElementReorderApi,
  type LineMention,
} from './layoutShared';
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
  /** Hover-gutter drag-to-reorder wiring; absent = element reorder disabled. */
  elementReorder?: ElementReorderApi;
  /** A1 continuous numbering: this scene's cumulative block-index base (from
   *  `sceneBlockBases`). The heading consumed `blockIndexBase + 1`, so the
   *  first element here is `blockIndexBase + 2`. Defaults to 0. */
  blockIndexBase?: number;
}

export function HollywoodLayout({
  elements,
  focusedElementId,
  handlers,
  mention,
  selectedIds,
  onTickClick,
  elementReorder,
  blockIndexBase = 0,
}: HollywoodLayoutProps) {
  return (
    <>
      {elements.map((el, i) => (
        <ElementLine
          key={el.id}
          element={el}
          index={blockIndexBase + 1 + i}
          lineClass={HOLLYWOOD_LINE_CLASS[el.type]}
          focused={focusedElementId === el.id}
          selected={selectedIds?.has(el.id)}
          onTickClick={onTickClick}
          draggingElementId={elementReorder?.draggingElementId ?? null}
          dropElementEdge={
            elementReorder?.dropTarget && elementReorder.dropTarget.elementId === el.id
              ? elementReorder.dropTarget.edge
              : null
          }
          onElementDragStart={elementReorder?.onDragStart}
          onElementDragOver={elementReorder?.onDragOver}
          onElementDrop={elementReorder?.onDrop}
          onElementDragEnd={elementReorder?.onDragEnd}
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
