import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { listIssueOutputs, OutputsError, type OutputObject } from '../../services/outputsService';
import { toMentionRows, type OutputMentionRow } from './outputMentionRows';
import type { OutputMentionListHandle } from './OutputMentionList';
import type { OutputsTabProps } from './ResourcePickerSuggestion';

/**
 * The `@` picker's Outputs tab (harness 3a Task 6).
 *
 * Written to the shape of `useMentionAssetsTab` on purpose — the host routes
 * keys the same way into both, and a second, subtly different contract would
 * make "which tab claimed this keystroke" a question with two answers. Three
 * things genuinely differ:
 *
 *  1. **One host, not two.** Citations are issue-scoped (the resolver's whole
 *     check is "was this version produced on this issue"), so only the issue
 *     reply box has this tab. The chat composer refuses `output_ref` outright.
 *  2. **Read once per mention session, filtered in memory.** The assets tab
 *     re-queries per keystroke because its population is the whole library;
 *     this one is a single issue's outputs — a few rows, unchanged for the
 *     seconds a mention lasts. A round trip per character would buy nothing.
 *  3. **A failed read is SAID.** "This issue produced nothing" and "I could
 *     not find out" are answers a reader acts on differently, so the error
 *     travels as its own field rather than collapsing into an empty list.
 */

export interface UseMentionOutputsTabOptions {
  /** The host's own "the mention picker is showing" flag. */
  pickerOpen: boolean;
  /** The issue whose outputs are citable, or null on a host with no issue
   *  behind it. Null never fetches — the host does not draw the tab at all in
   *  that case, and a request scoped to nothing would be a request for
   *  everything. */
  issueId: number | string | null;
  /** The live `@` query, already stripped of the `@`. */
  query: string;
  /** What the host does with a picked row (stage it as a citation). */
  onSelect: (row: OutputMentionRow) => void;
}

export interface MentionOutputsTab {
  /** Ready to spread onto `ResourcePickerSuggestion`'s `outputs` prop. */
  outputs: OutputsTabProps;
  /** Leave the tab without closing the picker (the host's `onKindChange`). */
  deactivate: () => void;
  /** ↑ / ↓ / Enter, routed from the composer into the open list. False when
   *  the tab did not claim the key, and the host must let it fall through. */
  handleKey: (key: 'ArrowUp' | 'ArrowDown' | 'Enter') => boolean;
  /** Forget the tab AND the rows. Belongs on the host's CLOSE: an issue
   *  produces outputs while its composer sits open, so a list cached from the
   *  previous mention session would hide the thing just made. */
  reset: () => void;
}

export function useMentionOutputsTab({
  pickerOpen,
  issueId,
  query,
  onSelect,
}: UseMentionOutputsTabOptions): MentionOutputsTab {
  const { t } = useTranslation();
  const [active, setActive] = useState(false);
  const [objects, setObjects] = useState<OutputObject[] | null>(null);
  const [loading, setLoading] = useState(false);
  /**
   * What went wrong, as a CODE rather than a sentence.
   *
   * `null` — nothing went wrong. `''` — it failed with nothing typed to say
   * about it. Anything else is the server's own `details.code`.
   *
   * Storing the code rather than the rendered string is what keeps `t` out of
   * the fetch effect's dependencies, and that is not a style preference: `t`
   * gets a fresh identity on every render under some i18n setups, so an effect
   * that depends on it re-runs each render and its own cleanup cancels the
   * request that was already in flight — a tab that fetches forever and shows
   * nothing, with the guard below making sure it never retries.
   */
  const [errorCode, setErrorCode] = useState<string | null>(null);
  const listRef = useRef<OutputMentionListHandle | null>(null);
  // Guards a second request while the first is still out: activating twice
  // (a click, then the strip re-rendering) must not double the round trip.
  const requested = useRef(false);

  useEffect(() => {
    if (!active || issueId == null || requested.current) return;
    requested.current = true;
    let live = true;
    setLoading(true);
    listIssueOutputs(issueId)
      .then((rows) => {
        if (!live) return;
        setObjects(rows);
        setErrorCode(null);
      })
      .catch((err) => {
        if (!live) return;
        // Logged with the server's own words, shown with ours: the wire
        // message names a kind and a snowflake, which is a sentence for a log.
        console.error('[useMentionOutputsTab] load failed', err);
        setObjects([]);
        setErrorCode(
          err instanceof OutputsError && err.code !== `http_${err.status}`
            ? err.code
            : '',
        );
      })
      .finally(() => {
        if (live) setLoading(false);
      });
    return () => {
      live = false;
    };
  }, [active, issueId]);

  const error = useMemo(() => {
    if (errorCode === null) return null;
    return errorCode
      ? t('outputs.errorCode', 'Could not read this output ({{code}})', { code: errorCode })
      : t('outputs.mentionError', 'Could not read this issue’s outputs');
  }, [errorCode, t]);

  const rows = useMemo(
    () => (objects ? toMentionRows(objects, query) : []),
    [objects, query],
  );

  const handleKey = useCallback(
    (key: 'ArrowUp' | 'ArrowDown' | 'Enter'): boolean => {
      if (!pickerOpen || !active) return false;
      const handle = listRef.current;
      if (!handle) return false;
      if (key === 'ArrowDown') {
        handle.move(1);
        return true;
      }
      if (key === 'ArrowUp') {
        handle.move(-1);
        return true;
      }
      // Enter. `commitActive` answers false when nothing is highlighted, and
      // that false is what lets the keystroke reach the text instead of being
      // swallowed into a pick that never happened.
      return handle.commitActive();
    },
    [pickerOpen, active],
  );

  const reset = useCallback(() => {
    setActive(false);
    setObjects(null);
    setErrorCode(null);
    requested.current = false;
  }, []);

  const deactivate = useCallback(() => setActive(false), []);

  const outputs = useMemo<OutputsTabProps>(
    () => ({
      active,
      onActivate: () => setActive(true),
      rows,
      loading,
      error,
      onSelect,
      listRef,
    }),
    [active, rows, loading, error, onSelect],
  );

  return { outputs, deactivate, handleKey, reset };
}
