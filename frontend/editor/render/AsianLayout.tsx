/**
 * Asian layout engine (spec v3 §3.3 / D8 column 2).
 *
 * A sibling of HollywoodLayout: it consumes the SAME ordered ScriptElement[]
 * and reuses the SAME editable row (ElementLine) — only the per-type typeset
 * differs. Where the Hollywood engine centres cues and right-aligns transitions,
 * the Asian engine lays the page out as a numbered manuscript: action lines get
 * a `△` prefix, character cues become a left-aligned `Name:` label, dialogue is
 * indented under that label, parens are inline italics, transitions right-align,
 * comments are a vertical-bar quote and subtitles are centred italics.
 *
 * The `△` prefix and the character `:` label are the only two rows that need a
 * decoration node; both are rendered as aria-hidden spans (screen readers read
 * the element text, not the typeset ornament). Everything else is pure CSS keyed
 * off the `as-*` classes in editorShellStyles.ts. All interaction (keydown
 * machine, debounced input, paste, IME) stays in SceneBlock and flows down
 * through `handlers`, so this component is purely presentational.
 */
import { ElementLine, type ElementReorderApi, type LineMention } from './layoutShared';
import type { LayoutHandlers } from './HollywoodLayout';
import type { ElementType, ScriptElement } from '../types';

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
const ASIAN_PREFIX: Partial<Record<ElementType, string>> = {
  action: '△',
};

/** Rows that carry a trailing label ornament (rendered after the editable line).
 *  Character cue uses the FULLWIDTH colon `：` — Chinese punctuation, per the
 *  国内剧本 "角色名：对白" convention (halfwidth `:` reads as a Latin colon). */
const ASIAN_SUFFIX: Partial<Record<ElementType, string>> = {
  character: '：',
};

export interface AsianLayoutProps {
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

export function AsianLayout({
  elements,
  focusedElementId,
  handlers,
  mention,
  selectedIds,
  onTickClick,
  elementReorder,
  blockIndexBase = 0,
}: AsianLayoutProps) {
  return (
    <>
      {elements.map((el, i) => {
        const prefix = ASIAN_PREFIX[el.type];
        const suffix = ASIAN_SUFFIX[el.type];
        return (
          <div key={el.id} className={`as-row as-row-${el.type}`} data-el-type={el.type}>
            {prefix && (
              <span className="as-mark as-prefix" aria-hidden="true">
                {prefix}
              </span>
            )}
            <ElementLine
              element={el}
              index={blockIndexBase + 1 + i}
              lineClass={ASIAN_LINE_CLASS[el.type]}
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
            {suffix && (
              <span className="as-mark as-suffix" aria-hidden="true">
                {suffix}
              </span>
            )}
          </div>
        );
      })}
    </>
  );
}
