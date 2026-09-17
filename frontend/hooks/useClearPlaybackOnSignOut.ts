import { useEffect, useRef } from 'react';
import { clearUser } from '../utils/playbackResume';

/**
 * Drop a signing-out account's remembered playback positions.
 *
 * Why this is a hook on the sign-out TRANSITION rather than a line inside
 * `handleLogout`: clearing there runs too early and the entry comes back.
 * `VideoPlayer` flushes its position in its own effect cleanup — that flush is
 * what makes a deploy-triggered reload resumable — and the cleanup runs when
 * the logout re-render unmounts it, i.e. AFTER anything `handleLogout` did
 * synchronously. Verified on production before this moved: the row survived
 * logout carrying a timestamp NEWER than the clear.
 *
 * React runs every destroy function in a commit before the create functions,
 * so this effect body lands after those flushes. It clears the PREVIOUS id,
 * which is exactly the namespace those late writes used (the player's cleanup
 * closes over the id it rendered with).
 *
 * `clear` is injectable only so the ordering can be asserted without mounting
 * the whole auth provider.
 */
export function useClearPlaybackOnSignOut(
  currentUserId: string | null,
  clear: (userId: string | null) => void = clearUser,
): void {
  const previousUserId = useRef<string | null>(null);
  useEffect(() => {
    const before = previousUserId.current;
    previousUserId.current = currentUserId;
    // Only the non-null → null edge. A first render as signed-out, or a
    // straight A → B account switch, must not wipe anything: the arriving
    // account's own namespace is untouched either way, and B's positions are
    // not A's to delete.
    if (before && !currentUserId) clear(before);
  }, [currentUserId, clear]);
}
