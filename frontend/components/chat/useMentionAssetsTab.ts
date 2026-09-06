import { useCallback, useMemo, useRef, useState } from 'react';

import {
  type AssetGridPickerHandle,
  type AssetGridQuery,
  type AssetGridRow,
} from '../assets/AssetGridPicker';
import { searchAssetsAccessible } from '../../services/assetsService';
import type { AssetsTabProps } from './ResourcePickerSuggestion';

/**
 * The `@` picker's Assets tab, for every host that renders one.
 *
 * Extracted because `AIChatPanel` and `Todolist/IssueReplyBox` had grown
 * byte-identical copies of the tab state, the transport and the key routing.
 * Identical was the intent — the two entry points must not disagree about what
 * mentioning an asset does — but nothing kept them that way, and the copy that
 * drifted first would have been the quiet one: a tab that searches a slightly
 * different population, or arrows that stop working in one composer only.
 *
 * What stays with the HOST: whether the picker is open at all (each owns that
 * flag, its own lifecycle and its own name for it) and what to do with a picked
 * row. Everything else lives here.
 */

export interface UseMentionAssetsTabOptions {
  /** The host's own "the mention picker is showing" flag. */
  pickerOpen: boolean;
  /** What the host does with a picked row (stage it, in both hosts today). */
  onSelect: (row: AssetGridRow) => void;
}

export interface MentionAssetsTab {
  /** Ready to spread onto `ResourcePickerSuggestion`'s `assets` prop. */
  assets: AssetsTabProps;
  /** Leave the Assets tab without closing the picker (the host's
   *  `onKindChange`). */
  deactivate: () => void;
  /**
   * ↑ / ↓ / Enter, routed from the composer to the open grid. Answers false
   * when it did not claim the key, and the host must let that fall through.
   *
   * Only the ASSETS tab is claimed. The five resource tabs have never moved
   * their highlight with the arrows (`activeIndex` has been pinned at 0 since
   * the picker shipped), so claiming a key for a row the user cannot see
   * selected would silently swallow a keystroke that means something else —
   * send in the chat panel, a newline in the issue reply box.
   */
  handleKey: (key: 'ArrowUp' | 'ArrowDown' | 'Enter') => boolean;
  /**
   * Forget the tab and the count. Belongs on the host's CLOSE, not its open:
   * the live query updates on every keystroke, so resetting on open would
   * bounce the user off the Assets tab the moment they typed one more
   * character. Closing ends the mention session, which is the only moment the
   * choice stops meaning anything.
   */
  reset: () => void;
}

export function useMentionAssetsTab({
  pickerOpen,
  onSelect,
}: UseMentionAssetsTabOptions): MentionAssetsTab {
  // Its own flag rather than a seventh `activeKind` value: the two axes answer
  // to different searches, and a shared enum would make every read re-derive
  // which one it is holding.
  const [active, setActive] = useState(false);
  // `null` until the grid answers. An unvisited tab badging "0" would state
  // that the user's library is empty — a claim no request has been made to
  // support.
  const [count, setCount] = useState<number | null>(null);
  const pickerRef = useRef<AssetGridPickerHandle | null>(null);

  /**
   * Every team the user belongs to, plus the system presets. No `scope_id`:
   * a chat window outlives any one workspace route, and an asset reference is
   * authorized server-side by team membership (the same predicate
   * `asset_ref_resolver` reads). Narrowing it here — even in the issue reply
   * box, which does have a `teamId` for its RESOURCE search — would be a
   * second, quieter answer to the same authorization question.
   */
  const fetch = useCallback(
    (params: AssetGridQuery, signal: AbortSignal): Promise<AssetGridRow[]> =>
      searchAssetsAccessible(params.q ?? '', {
        type: params.type ?? undefined,
        library: params.library,
        limit: params.limit,
        signal,
      }),
    [],
  );

  const handleKey = useCallback(
    (key: 'ArrowUp' | 'ArrowDown' | 'Enter'): boolean => {
      if (!pickerOpen || !active) return false;
      const handle = pickerRef.current;
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
      // that false is what lets the keystroke fall through to the host.
      return handle.commitActive();
    },
    [pickerOpen, active],
  );

  const reset = useCallback(() => {
    setActive(false);
    // A count carried over would badge a number for a search this session
    // never ran.
    setCount(null);
  }, []);

  const deactivate = useCallback(() => setActive(false), []);

  const assets = useMemo<AssetsTabProps>(
    () => ({
      active,
      onActivate: () => setActive(true),
      count,
      onCountChange: setCount,
      onSelect,
      fetch,
      pickerRef,
    }),
    [active, count, onSelect, fetch],
  );

  return { assets, deactivate, handleKey, reset };
}
