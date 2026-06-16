/**
 * Lightweight @-mention trigger for a native <textarea> inside a canvas node.
 *
 * Detects the pattern /@(\w*)$/ immediately before the caret and manages
 * picker open/close state, query extraction, and item selection. Does NOT
 * depend on tiptap — the canvas prompt textarea is a plain <textarea>, so
 * we handle @-detection via selectionStart rather than prosemirror positions.
 *
 * On select: replaces the `@query` token in the textarea value with
 * `@name ` (trailing space so typing continues naturally), then calls
 * `onSelectRef` so the caller can persist the PromptResourceRef.
 *
 * Keyboard navigation:
 *   ↓ / ↑  move activeIndex with wrap, bounded by the last setItemCount value
 *   Esc    close picker
 * Call `setItemCount(n)` after search results arrive to tighten the wrap bound.
 */

import {
  type ChangeEventHandler,
  type KeyboardEventHandler,
  useCallback,
  useRef,
  useState,
} from 'react';
import type { ResourceSearchResult } from '../../../../types';

/** Matches '@' followed by zero or more word characters at the end of string. */
const AT_BEFORE_CARET = /@(\w*)$/;

export interface MentionPickerBag {
  pickerOpen: boolean;
  query: string;
  activeIndex: number;
  /** Wire to textarea `onChange` */
  handleChange: ChangeEventHandler<HTMLTextAreaElement>;
  /** Wire to textarea `onKeyDown` — intercepts ↑ ↓ Esc while picker is open */
  handleKeyDown: KeyboardEventHandler<HTMLTextAreaElement>;
  /** Call when the user picks an item from the picker */
  handleSelect: (item: ResourceSearchResult) => void;
  /** Call to close the picker (e.g. `onBlur`) */
  closePicker: () => void;
  /**
   * Sync the item count for accurate keyboard-wrap.
   * Call whenever search results change: `mention.setItemCount(results.length)`.
   * Stored as a ref — does not trigger re-renders.
   */
  setItemCount: (n: number) => void;
}

interface Options {
  /** Current text value of the textarea. */
  value: string;
  /** Called with the updated text when the @ token is replaced on selection. */
  onValueChange: (text: string) => void;
  /** Called when the user selects a resource to add as a ref attachment. */
  onSelectRef: (item: ResourceSearchResult) => void;
}

export function useCanvasMentionPicker({
  value,
  onValueChange,
  onSelectRef,
}: Options): MentionPickerBag {
  const [pickerOpen, setPickerOpen] = useState(false);
  const [query, setQuery] = useState('');
  const [activeIndex, setActiveIndex] = useState(0);

  /**
   * Character offset of the '@' in `value` when picker is open.
   * Used by handleSelect to splice out the @-token on pick.
   */
  const tokenStartRef = useRef(0);

  /**
   * Upper bound for keyboard wrap — updated via setItemCount.
   * Using a ref (not state) so updating it never triggers re-renders.
   */
  const itemCountRef = useRef(20);

  const setItemCount = useCallback((n: number) => {
    itemCountRef.current = Math.max(1, n);
  }, []);

  const handleChange = useCallback(
    (e: React.ChangeEvent<HTMLTextAreaElement>) => {
      const text = e.target.value;
      onValueChange(text);

      const caret = e.target.selectionStart ?? text.length;
      const before = text.slice(0, caret);
      const match = AT_BEFORE_CARET.exec(before);

      if (match) {
        tokenStartRef.current = caret - match[0].length; // offset of '@'
        setQuery(match[1]);                               // text after '@'
        setActiveIndex(0);
        setPickerOpen(true);
      } else {
        setPickerOpen(false);
      }
    },
    [onValueChange],
  );

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
      if (!pickerOpen) return;
      const count = itemCountRef.current;
      if (e.key === 'ArrowDown') {
        e.preventDefault();
        setActiveIndex((i) => (i + 1) % count);
      } else if (e.key === 'ArrowUp') {
        e.preventDefault();
        setActiveIndex((i) => (i - 1 + count) % count);
      } else if (e.key === 'Escape') {
        e.preventDefault();
        setPickerOpen(false);
      }
    },
    [pickerOpen],
  );

  const handleSelect = useCallback(
    (item: ResourceSearchResult) => {
      // '@' + typed query = the token to replace
      const tokenLen = 1 + query.length;
      const before = value.slice(0, tokenStartRef.current);
      const after = value.slice(tokenStartRef.current + tokenLen);
      // Trailing space so the cursor lands after the inserted name
      onValueChange(`${before}@${item.name} ${after}`);
      onSelectRef(item);
      setPickerOpen(false);
    },
    [value, query, onValueChange, onSelectRef],
  );

  const closePicker = useCallback(() => setPickerOpen(false), []);

  return {
    pickerOpen,
    query,
    activeIndex,
    handleChange,
    handleKeyDown,
    handleSelect,
    closePicker,
    setItemCount,
  };
}
