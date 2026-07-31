/**
 * listStateCache — module-level (in-memory) memory of the My Downloads list.
 *
 * Why a module singleton rather than sessionStorage: the semantics we want are
 * "coming back from a detail page inside this session restores what I was
 * looking at". A page reload should start clean, which a module variable gives
 * us for free (and avoids serializing hundreds of hydrated Video rows into
 * storage on every navigation).
 *
 * What is NOT stored here: the paginated library rows themselves. They live in
 * ``LibraryProvider``, which is mounted in AppLayout — a route ancestor of both
 * ``resources/downloads`` and ``resources/file/:resourceId`` — so React Router
 * keeps them alive while DownloadsView unmounts. Duplicating them here would
 * create a second source of truth that Realtime updates (#1642) would not reach.
 * The scroll restore instead waits for the list to be tall enough (see
 * ``applyScrollOffsets``), which covers the case where the rows are gone.
 *
 * Invalidation:
 *   - page reload / new tab           → module scope dies with the JS context
 *   - workspace (team) switch         → ``teamId`` guard in readDownloadsListState
 *   - manual Refresh / pull-to-refresh→ explicit clearDownloadsListState() call
 *   - stale after 30 min              → TTL guard
 */

import type { Video } from '../../types';
import type { SearchResult } from '../../services/searchService';

/** How long a snapshot stays eligible for restore. */
export const LIST_STATE_TTL_MS = 30 * 60 * 1000;

export interface ScrollOffsets {
  /** ``scrollTop`` of the desktop scroll container (``md:overflow-y-auto``). */
  scrollTop: number;
  /** ``window.scrollY`` — on mobile the document scrolls, not the container. */
  windowScrollY: number;
}

export interface DownloadsListState extends ScrollOffsets {
  /** Workspace the snapshot was taken in; a different team invalidates it. */
  teamId: string | null;
  searchQuery: string;
  isSearchActive: boolean;
  searchQueryText: string;
  searchResults: SearchResult[];
  searchVideoMap: Record<string, Video>;
  mobileSearchQuery: string;
  isMobileSearchOpen: boolean;
  /** Epoch ms, for the TTL check. */
  savedAt: number;
}

let cached: DownloadsListState | null = null;

export function saveDownloadsListState(state: DownloadsListState): void {
  cached = state;
}

/**
 * Returns the snapshot when it is still usable for ``teamId``, else null.
 * A mismatched team or an expired snapshot is dropped so a later read in the
 * original team can't resurrect it.
 */
export function readDownloadsListState(
  teamId: string | null,
  now: number = Date.now(),
): DownloadsListState | null {
  if (!cached) return null;
  if (cached.teamId !== teamId) {
    cached = null;
    return null;
  }
  if (now - cached.savedAt > LIST_STATE_TTL_MS) {
    cached = null;
    return null;
  }
  return cached;
}

export function clearDownloadsListState(): void {
  cached = null;
}

/** Current offsets of both possible scrollers (container + document). */
export function readScrollOffsets(el: HTMLElement | null): ScrollOffsets {
  return {
    scrollTop: el?.scrollTop ?? 0,
    windowScrollY: typeof window === 'undefined' ? 0 : window.scrollY || 0,
  };
}

/**
 * Applies saved offsets to whichever surface can actually take them.
 *
 * Both are attempted because the scroller differs by breakpoint: desktop
 * scrolls the container (``md:overflow-y-auto``), mobile scrolls the document.
 * Writing to the one that isn't scrollable is a harmless no-op.
 *
 * Returns false while a non-zero target still exceeds the current scroll
 * range — i.e. the list hasn't laid out far enough yet — so the caller can
 * retry on the next frame instead of landing the user halfway up.
 */
export function applyScrollOffsets(
  el: HTMLElement | null,
  offsets: ScrollOffsets,
): boolean {
  let settled = true;

  if (offsets.scrollTop > 0 && el) {
    const max = el.scrollHeight - el.clientHeight;
    if (max >= offsets.scrollTop) el.scrollTop = offsets.scrollTop;
    else settled = false;
  }

  if (offsets.windowScrollY > 0 && typeof window !== 'undefined') {
    const doc = document.documentElement;
    const max = doc.scrollHeight - window.innerHeight;
    if (max >= offsets.windowScrollY) window.scrollTo(0, offsets.windowScrollY);
    else settled = false;
  }

  return settled;
}
