/**
 * Open/closed state for the `@` picker on a canvas prompt node.
 *
 * Nothing more. The picker itself (`PromptMentionPicker`) owns its tabs, its
 * rows and its active index, and the tiptap editor owns the text — so this
 * hook's whole job is "is a mention token live in front of the caret, and what
 * does it say".
 *
 * ⚠️ It used to own a great deal more: the `@query` was detected inside a
 * textarea `onChange`, selection spliced the token out of a plain string, and
 * arrow keys walked a row count reported back in. All of that moved when the
 * body became a contenteditable and the picker became two grids — the editor
 * reports the live query (`mentionQueryFromText`) and forwards keys to the
 * picker's imperative handle. Those members were kept for a while after their
 * last caller went away, which is how a hook ends up with an API nobody can
 * explain; they are gone.
 *
 * `setMentionQuery(null)` is the close signal: the token in front of the caret
 * was deleted, so there is nothing to pick for any more.
 */

import { useCallback, useState } from 'react';

export interface MentionPickerBag {
  pickerOpen: boolean;
  /** The text after the `@`, narrowing the picker as the user types. */
  query: string;
  /** Close it — `onBlur`, Escape, and after a pick. */
  closePicker: () => void;
  /**
   * Open the picker explicitly, with an empty query.
   *
   * The contenteditable reports a bare `@` from its own keydown handler, which
   * is earlier than the document change that would carry the query — so the
   * open and the first narrowing are two separate signals.
   */
  openPicker: () => void;
  /**
   * Drive the picker from the live `@query` the editor computes: a string
   * narrows it, `null` means the token is gone and the picker should close.
   */
  setMentionQuery: (q: string | null) => void;
}

export function useCanvasMentionPicker(): MentionPickerBag {
  const [pickerOpen, setPickerOpen] = useState(false);
  const [query, setQuery] = useState('');

  const openPicker = useCallback(() => {
    setQuery('');
    setPickerOpen(true);
  }, []);

  const setMentionQuery = useCallback((q: string | null) => {
    if (q === null) {
      setPickerOpen(false);
      return;
    }
    setQuery(q);
    setPickerOpen(true);
  }, []);

  const closePicker = useCallback(() => setPickerOpen(false), []);

  return { pickerOpen, query, closePicker, openPicker, setMentionQuery };
}
