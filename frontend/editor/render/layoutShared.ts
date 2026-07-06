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

// A mention token is `@` followed by a run of non-space, non-`@` chars. Multi-
// word names are a Phase-1 limitation (the token stops at the first space).
const MENTION_RE = /@([^\s@]+)/g;

/**
 * Build the inner HTML for an element line: escaped text with any `@name`
 * tokens wrapped as chips. A name present in `mentionNames` (case-insensitive)
 * is a known chip; anything else is a greyed-out fallback chip (never an error).
 */
export function buildElementHtml(text: string, mentionNames: string[]): string {
  const known = new Set(mentionNames.map((n) => n.toLowerCase()));
  let out = '';
  let last = 0;
  for (const match of text.matchAll(MENTION_RE)) {
    const name = match[1];
    const start = match.index ?? 0;
    out += escapeHtml(text.slice(last, start));
    const isKnown = known.has(name.toLowerCase());
    out +=
      `<span data-mention="${escapeHtml(name)}" ` +
      `class="mh-mention${isKnown ? '' : ' unknown'}">@${escapeHtml(name)}</span>`;
    last = start + match[0].length;
  }
  out += escapeHtml(text.slice(last));
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
}

/** A mention picker anchored to one element line, threaded through the engines. */
export interface LineMention extends LineMentionAria {
  elementId: string;
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
  /** When set, this line is the open mention combobox (ARIA lives here, not the popup). */
  mentionAria?: LineMentionAria;
  /** Copilot selection state for this row's gutter tick (Task 11). */
  selected?: boolean;
  /** Clicking the gutter tick selects the element for the copilot (Task 11). */
  onTickClick?: (elementId: string, shiftKey: boolean) => void;
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
  mentionAria,
  selected,
  onTickClick,
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

  return createElement(
    'div',
    { className: `mh-el-row${focused ? ' focused' : ''}${isTransition ? ' transition-row' : ''}` },
    tick,
    createElement('div', {
      ref: editableRef,
      className: `mh-el-editable mh-el-line ${lineClass}`,
      contentEditable: true,
      suppressContentEditableWarning: true,
      // When a mention picker is open on this line, the line IS the combobox so
      // AT announces the active option; otherwise it is a plain textbox.
      role: mentionAria ? 'combobox' : 'textbox',
      'aria-expanded': mentionAria ? 'true' : undefined,
      'aria-controls': mentionAria ? mentionAria.listboxId : undefined,
      'aria-haspopup': mentionAria ? 'listbox' : undefined,
      'aria-activedescendant': mentionAria?.activeOptionId,
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
