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
 *
 * ─ Dismissal is STICKY ──────────────────────────────────────────────────────
 *
 * Escape and a completed pick do not merely close the popover — they mark the
 * token in front of the caret as dismissed, and it stays dismissed while that
 * token is alive. Without the latch the very next keystroke reported a live
 * `@query` and the popover came straight back, so Escape read as a flicker
 * rather than a dismissal and there was no way to type a literal `@name`.
 *
 * The latch clears when the token does — a `null` query — or when the user
 * types a FRESH `@` (`openPicker`). Both are the user asking again.
 */

import { useCallback, useRef, useState } from 'react';

export interface MentionPickerBag {
  pickerOpen: boolean;
  /** The text after the `@`, narrowing the picker as the user types. */
  query: string;
  /**
   * Dismiss it: Escape, and after a pick.
   *
   * There is deliberately NO blur or outside-click handler. Every control in
   * the popover is bound to `mousedown` with `preventDefault` precisely so the
   * editor never loses focus, which means a blur listener would fire on the
   * one gesture that must not close it. Clicking away puts the caret
   * somewhere else, which produces a `null` query and closes it that way.
   */
  closePicker: () => void;
  /**
   * Open the picker explicitly, with an empty query, clearing any dismissal.
   *
   * The contenteditable reports a bare `@` from its own keydown handler, which
   * is earlier than the document change that would carry the query — so the
   * open and the first narrowing are two separate signals.
   */
  openPicker: () => void;
  /**
   * Drive the picker from the live `@query` the editor computes: a string
   * narrows it, `null` means the token is gone and the picker should close.
   * A string does NOT reopen a picker the user dismissed on this token.
   */
  setMentionQuery: (q: string | null) => void;
}

export function useCanvasMentionPicker(): MentionPickerBag {
  const [pickerOpen, setPickerOpen] = useState(false);
  const [query, setQuery] = useState('');
  /** The user said no to the token currently in front of the caret. */
  const dismissedRef = useRef(false);

  const openPicker = useCallback(() => {
    dismissedRef.current = false;
    setQuery('');
    setPickerOpen(true);
  }, []);

  const setMentionQuery = useCallback((q: string | null) => {
    if (q === null) {
      // The token is gone, so the dismissal it referred to is gone with it.
      dismissedRef.current = false;
      setPickerOpen(false);
      return;
    }
    setQuery(q);
    if (dismissedRef.current) return;
    setPickerOpen(true);
  }, []);

  const closePicker = useCallback(() => {
    dismissedRef.current = true;
    setPickerOpen(false);
  }, []);

  return { pickerOpen, query, closePicker, openPicker, setMentionQuery };
}
