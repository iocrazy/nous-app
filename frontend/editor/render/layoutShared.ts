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
  createElement,
  useEffect,
  useRef,
  type ClipboardEvent,
  type FormEvent,
  type KeyboardEvent,
} from 'react';
import type { ElementType, ScriptElement } from '../types';

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
  onInput,
  onKeyDown,
  onFocus,
  onPaste,
  onCompositionStart,
  onCompositionEnd,
}: ElementLineProps) {
  const editableRef = useRef<HTMLDivElement | null>(null);

  // Sync DOM text from props only when it diverges and the row isn't the one
  // being typed into — protects the caret during live editing.
  useEffect(() => {
    const node = editableRef.current;
    if (!node) return;
    if (node.textContent !== element.text && document.activeElement !== node) {
      node.textContent = element.text;
    }
  }, [element.text]);

  const isTransition = element.type === 'transition';

  return createElement(
    'div',
    { className: `mh-el-row${focused ? ' focused' : ''}${isTransition ? ' transition-row' : ''}` },
    createElement('span', {
      className: `mh-el-tick ${ELEMENT_TICK_CLASS[element.type]}`,
      'aria-hidden': 'true',
    }),
    createElement('div', {
      ref: editableRef,
      className: `mh-el-editable mh-el-line ${lineClass}`,
      contentEditable: true,
      suppressContentEditableWarning: true,
      role: 'textbox',
      tabIndex: 0,
      'data-el-id': element.id,
      'data-el-type': element.type,
      'data-placeholder': placeholder ?? '',
      // Uncontrolled: initial text painted once on mount, then kept in sync by
      // the effect above. React never owns the children of a contentEditable.
      dangerouslySetInnerHTML: { __html: escapeHtml(element.text) },
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
  return text
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;');
}
