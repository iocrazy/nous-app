/**
 * Shared row primitives for the layout engines (Hollywood now, Asian in Task 7).
 *
 * A layout engine is NOT a CSS theme — both engines consume the same
 * `content_json` (the ordered ScriptElement[]) and differ only in how each
 * element type is typeset. `ElementLine` is the one editable row both engines
 * render: a coloured gutter tick (type colour), then a single contentEditable
 * span carrying `data-el-id` so the keyboard machine can move focus by id.
 *
 * contentEditable + React is a known trap: React must NOT re-write the node's
 * text on every keystroke or the caret jumps. So the text is written to the DOM
 * imperatively and only when it actually diverges AND the row is not focused
 * (i.e. a remote/optimistic change, never the user's own in-flight typing).
 */
import {
  createContext,
  createElement,
  useContext,
  useEffect,
  useRef,
  type ClipboardEvent,
  type DragEvent,
  type FormEvent,
  type KeyboardEvent,
  type MouseEvent,
} from 'react';
import type { ElementType, ScriptElement } from '../types';

/**
 * The current script's mention candidates (distinct CAST names). SceneBlock
 * provides it around the layout engine so every ElementLine can render `@name`
 * tokens as chips and grey out unknown ones — without threading a prop through
 * both layout engines.
 */
export const MentionNamesContext = createContext<string[]>([]);

// The default (unknown-name) mention token: `@` + a run of non-space, non-`@`
// chars. A KNOWN multi-word CAST name overrides this and chips as a whole (see
// matchKnownName); an unknown name still stops at the first word.
const MENTION_WORD_RE = /^[^\s@]+/;

/**
 * The longest known CAST name that `after` starts with (case-insensitive) on a
 * word boundary — this is what lets `@John Smith` chip as one token when
 * "John Smith" is a known name. Returns the ORIGINAL-cased slice, or null.
 */
function matchKnownName(after: string, knownLongestFirst: string[]): string | null {
  const lower = after.toLowerCase();
  for (const cand of knownLongestFirst) {
    if (cand.length === 0) continue;
    if (lower.startsWith(cand.toLowerCase())) {
      const next = after.charAt(cand.length);
      // Boundary: end of line, or a non-alphanumeric follows (so "John Smith"
      // matches "@John Smith!" but not "@John Smithers").
      if (next === '' || !/[A-Za-z0-9]/.test(next)) {
        return after.slice(0, cand.length);
      }
    }
  }
  return null;
}

/**
 * Build the inner HTML for an element line: escaped text with any `@name`
 * tokens wrapped as chips. A name present in `mentionNames` (case-insensitive)
 * is a known chip — matched greedily so multi-word CAST names (`@John Smith`)
 * chip whole; an unknown name is a greyed-out fallback chip that stops at the
 * first word (never an error).
 */
export function buildElementHtml(text: string, mentionNames: string[]): string {
  const known = new Set(mentionNames.map((n) => n.toLowerCase()));
  // Longest-first so a multi-word name wins over a shorter one it contains.
  const knownLongestFirst = [...mentionNames].sort((a, b) => b.length - a.length);
  let out = '';
  let i = 0;
  while (i < text.length) {
    const at = text.indexOf('@', i);
    if (at === -1) {
      out += escapeHtml(text.slice(i));
      break;
    }
    out += escapeHtml(text.slice(i, at));
    const after = text.slice(at + 1);
    let name = matchKnownName(after, knownLongestFirst);
    let isKnown = name != null;
    if (name == null) {
      const m = after.match(MENTION_WORD_RE);
      name = m ? m[0] : '';
      isKnown = name.length > 0 && known.has(name.toLowerCase());
    }
    if (name.length === 0) {
      // A lone `@` with nothing after it — emit it literally, keep scanning.
      out += '@';
      i = at + 1;
      continue;
    }
    out +=
      `<span data-mention="${escapeHtml(name)}" ` +
      `class="mh-mention${isKnown ? '' : ' unknown'}">@${escapeHtml(name)}</span>`;
    i = at + 1 + name.length;
  }
  return out;
}

/**
 * When a mention picker is open on THIS line, the line itself becomes the ARIA
 * combobox (WAI-ARIA activedescendant pattern): the focused contentEditable owns
 * `role=combobox` + `aria-expanded` + `aria-controls` + `aria-activedescendant`,
 * while the popup is only the `role=listbox`. Screen readers announce the active
 * option because the descendant lives on the focused element, not an unfocused
 * popup (PR-F3 review carry-over).
 */
export interface LineMentionAria {
  /** id of the popup listbox this line controls. */
  listboxId: string;
  /** id of the active option, or undefined when the filtered list is empty. */
  activeOptionId?: string;
  /**
   * Whether the listbox currently offers options. When the filter matches
   * nothing the combobox is collapsed: aria-expanded=false and the dangling
   * aria-controls / aria-activedescendant are dropped (they'd point at an empty
   * listbox with no active option otherwise).
   */
  expanded: boolean;
}

/** A mention picker anchored to one element line, threaded through the engines. */
export interface LineMention extends LineMentionAria {
  elementId: string;
}

/** Which half of an element row the pointer is over → the drop edge. Mirrors
 *  SceneBlock's scene-level edgeFromPointer, but named for element rows: 'top'
 *  lands the dragged element BEFORE this row, 'bottom' lands it AFTER. */
export function elementEdgeFromPointer(el: HTMLElement, clientY: number): 'top' | 'bottom' {
  const rect = el.getBoundingClientRect();
  return clientY < rect.top + rect.height / 2 ? 'top' : 'bottom';
}

/**
 * Element-level drag-to-reorder wiring, owned by SceneBlock and threaded through
 * the layout engines to every ElementLine. Mirrors the scene-level SceneReorderApi
 * but stays WITHIN one scene (v1: no cross-scene element moves). The gutter's
 * 6-dot handle is the drag source; the row is the drop target.
 */
export interface ElementReorderApi {
  /** The element currently being dragged (drives handle :active + row guards). */
  draggingElementId: string | null;
  /** The active drop target: which element row and which edge the line lands on. */
  dropTarget: { elementId: string; edge: 'top' | 'bottom' } | null;
  onDragStart: (elementId: string) => void;
  onDragOver: (elementId: string, edge: 'top' | 'bottom') => void;
  onDrop: (elementId: string, edge: 'top' | 'bottom') => void;
  onDragEnd: () => void;
}

export const ELEMENT_TICK_CLASS: Record<ElementType, string> = {
  action: 't-action',
  dialogue: 't-dialogue',
  character: 't-character',
  paren: 't-paren',
  transition: 't-transition',
  comment: 't-comment',
  subtitle: 't-subtitle',
};

export interface ElementLineProps {
  element: ScriptElement;
  /** Layout-engine class for the editable line (e.g. 'hw-action'). */
  lineClass: string;
  focused: boolean;
  placeholder?: string;
  /** 0-based position of this element within its scene; rendered as `index + 1`
   *  in the hover gutter (laper/Notion-style block number). */
  index: number;
  /** When set, this line is the open mention combobox (ARIA lives here, not the popup). */
  mentionAria?: LineMentionAria;
  /** Copilot selection state for this row's gutter tick (Task 11). */
  selected?: boolean;
  /** Clicking the gutter tick selects the element for the copilot (Task 11). */
  onTickClick?: (elementId: string, shiftKey: boolean) => void;
  // ── Element drag-to-reorder (hover gutter handle) — all optional so the row
  //    stays backward-compatible when reorder is not wired. ─────────────────
  /** The element currently being dragged anywhere in this scene. */
  draggingElementId?: string | null;
  /** The drop-edge highlight for THIS row (null = not the current target). */
  dropElementEdge?: 'top' | 'bottom' | null;
  /** Drag started on THIS row's 6-dot handle. */
  onElementDragStart?: (elementId: string) => void;
  /** Pointer moved over THIS row while an element is being dragged. */
  onElementDragOver?: (elementId: string, edge: 'top' | 'bottom') => void;
  /** Element dropped onto THIS row. */
  onElementDrop?: (elementId: string, edge: 'top' | 'bottom') => void;
  /** The drag gesture ended (dropped or cancelled). */
  onElementDragEnd?: () => void;
  onInput: (elementId: string, text: string) => void;
  onKeyDown: (elementId: string, e: KeyboardEvent<HTMLDivElement>) => void;
  onFocus: (elementId: string) => void;
  onPaste: (elementId: string, e: ClipboardEvent<HTMLDivElement>) => void;
  onCompositionStart: () => void;
  onCompositionEnd: () => void;
}

export function ElementLine({
  element,
  lineClass,
  focused,
  placeholder,
  index,
  mentionAria,
  selected,
  onTickClick,
  draggingElementId,
  dropElementEdge,
  onElementDragStart,
  onElementDragOver,
  onElementDrop,
  onElementDragEnd,
  onInput,
  onKeyDown,
  onFocus,
  onPaste,
  onCompositionStart,
  onCompositionEnd,
}: ElementLineProps) {
  const editableRef = useRef<HTMLDivElement | null>(null);
  const mentionNames = useContext(MentionNamesContext);

  // THE EFFECT BELOW IS THE ONLY WRITER OF THE EDITABLE'S CONTENT.
  //
  // Rendering the content via dangerouslySetInnerHTML looked equivalent but
  // hid a caret/typing killer: React re-writes the innerHTML of that node on
  // EVERY parent re-render (verified empirically — even with an unchanged
  // __html string), and parent re-renders happen constantly mid-typing (the
  // toolbar highlight follows the cursor, the mention picker opens, sync
  // states change). Each rewrite reset the line to the last COMMITTED model
  // text, eating everything typed inside the 500ms input debounce window and
  // throwing the caret to line start.
  //
  // So the render pass emits an EMPTY editable and this effect (no dep array
  // — runs after every render) syncs model → DOM only when the text actually
  // diverges AND the row isn't focused. While the writer is typing
  // (activeElement === node) the DOM is never touched; remote/optimistic
  // changes land as soon as the row blurs. Repaints go through
  // buildElementHtml so `@name` chips render (chips never change textContent,
  // keeping the divergence check byte-exact).
  useEffect(() => {
    const node = editableRef.current;
    if (!node) return;
    if (node.textContent !== element.text && document.activeElement !== node) {
      node.innerHTML = buildElementHtml(element.text, mentionNames);
    }
  });

  const isTransition = element.type === 'transition';

  // The gutter tick is a colour marker by default; when copilot selection is
  // wired in it becomes a small toggle button that summons the card (Task 11).
  const tick = onTickClick
    ? createElement('button', {
        type: 'button',
        className: `mh-el-tick tick-btn ${ELEMENT_TICK_CLASS[element.type]}${
          selected ? ' selected' : ''
        }`,
        // Pointer-summon affordance; kept out of the tab order in Phase 1.
        tabIndex: -1,
        'aria-pressed': selected ? 'true' : 'false',
        'aria-label': 'Select element',
        'data-tick-id': element.id,
        onMouseDown: (e: MouseEvent) => e.preventDefault(),
        onClick: (e: MouseEvent) => onTickClick(element.id, e.shiftKey),
      })
    : createElement('span', {
        className: `mh-el-tick ${ELEMENT_TICK_CLASS[element.type]}`,
        'aria-hidden': 'true',
      });

  // Hover gutter (laper/Notion-style): a block number + a 6-dot drag handle,
  // rendered in the row's left margin and revealed on hover / focus via CSS.
  // The handle is the ONLY draggable element; the row is the drop target. This
  // is a SEPARATE affordance from the copilot-select tick above — neither
  // touches the other. The six dots are drawn as a 2×3 grid of `.mh-el-dot`
  // spans (crisper than a unicode glyph across fonts).
  const dragEnabled = !!onElementDragStart;
  const gutter = createElement(
    'div',
    { className: 'mh-el-gutter', contentEditable: false, 'aria-hidden': dragEnabled ? undefined : 'true' },
    createElement('span', { className: 'mh-el-num' }, String(index + 1)),
    createElement(
      'button',
      {
        type: 'button',
        className: `mh-el-drag${draggingElementId === element.id ? ' dragging' : ''}`,
        tabIndex: -1,
        'aria-label': 'Drag to reorder',
        draggable: dragEnabled,
        // Keep the handle from stealing focus/selection from the editable.
        onMouseDown: (e: MouseEvent) => e.preventDefault(),
        onDragStart: dragEnabled
          ? (e: DragEvent<HTMLButtonElement>) => {
              e.dataTransfer.effectAllowed = 'move';
              e.dataTransfer.setData('text/plain', element.id);
              onElementDragStart?.(element.id);
            }
          : undefined,
        onDragEnd: dragEnabled ? () => onElementDragEnd?.() : undefined,
      },
      [0, 1, 2, 3, 4, 5].map((d) =>
        createElement('span', { key: d, className: 'mh-el-dot', 'aria-hidden': 'true' }),
      ),
    ),
  );

  const dropClass =
    dropElementEdge === 'top' ? ' drop-top' : dropElementEdge === 'bottom' ? ' drop-bottom' : '';

  return createElement(
    'div',
    {
      className: `mh-el-row${focused ? ' focused' : ''}${
        isTransition ? ' transition-row' : ''
      }${dropClass}`,
      // Row is the drop target: allow the drop (preventDefault) and report the
      // edge only while an element in this scene is actually being dragged.
      onDragOver: onElementDragOver
        ? (e: DragEvent<HTMLDivElement>) => {
            if (!draggingElementId) return;
            e.preventDefault();
            onElementDragOver(element.id, elementEdgeFromPointer(e.currentTarget, e.clientY));
          }
        : undefined,
      onDrop: onElementDrop
        ? (e: DragEvent<HTMLDivElement>) => {
            if (!draggingElementId) return;
            e.preventDefault();
            onElementDrop(element.id, elementEdgeFromPointer(e.currentTarget, e.clientY));
          }
        : undefined,
    },
    gutter,
    tick,
    createElement('div', {
      ref: editableRef,
      className: `mh-el-editable mh-el-line ${lineClass}`,
      contentEditable: true,
      suppressContentEditableWarning: true,
      // When a mention picker is open on this line, the line IS the combobox so
      // AT announces the active option; otherwise it is a plain textbox. With no
      // matches the combobox collapses (aria-expanded=false) and the controls /
      // activedescendant refs are dropped rather than left dangling.
      role: mentionAria ? 'combobox' : 'textbox',
      'aria-expanded': mentionAria ? (mentionAria.expanded ? 'true' : 'false') : undefined,
      'aria-controls': mentionAria && mentionAria.expanded ? mentionAria.listboxId : undefined,
      'aria-haspopup': mentionAria ? 'listbox' : undefined,
      'aria-activedescendant':
        mentionAria && mentionAria.expanded ? mentionAria.activeOptionId : undefined,
      tabIndex: 0,
      'data-el-id': element.id,
      'data-el-type': element.type,
      'data-placeholder': placeholder ?? '',
      // Uncontrolled: no children and no dangerouslySetInnerHTML — the sync
      // effect above owns the DOM content exclusively (see its comment for
      // why render-pass HTML writes eat in-flight typing).
      onInput: (e: FormEvent<HTMLDivElement>) =>
        onInput(element.id, e.currentTarget.textContent ?? ''),
      onKeyDown: (e: KeyboardEvent<HTMLDivElement>) => onKeyDown(element.id, e),
      onFocus: () => onFocus(element.id),
      onPaste: (e: ClipboardEvent<HTMLDivElement>) => onPaste(element.id, e),
      onCompositionStart,
      onCompositionEnd,
    }),
  );
}

/** Minimal text escape — element text is plain text, never markup. */
function escapeHtml(text: string): string {
  // Quotes MUST be escaped: this output lands inside double-quoted HTML
  // attributes (data-mention="...") via innerHTML — a bare " breaks out of
  // the attribute and injects live event handlers (stored XSS).
  return text
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}
