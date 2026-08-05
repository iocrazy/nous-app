/**
 * A tiny pub/sub so "click a shot card the agent just wrote" can reach the
 * storyboard canvas.
 *
 * The publisher (AIChatPanel, inside FloatingChatWidget) and the subscriber
 * (EditorShell) are SIBLINGS — ScriptEditor mounts both, and the widget is
 * also mounted app-wide by AppLayout — so there is no shared React state to
 * thread a callback through. A module-level bus is the smallest thing that
 * connects them without hoisting editor state into a global store.
 *
 * Deliberately fire-and-forget: if no editor is mounted (the panel is open on
 * another route, or the script isn't loaded), the request is dropped. A chip
 * that silently does nothing is better than one that navigates the user away
 * from what they were reading.
 */

export type ShotFocusListener = (shotId: string) => void;

const listeners = new Set<ShotFocusListener>();

/** Subscribe; returns the unsubscribe function (useEffect-shaped). */
export function onShotFocus(listener: ShotFocusListener): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

/** Ask whoever is listening to reveal this shot. No-op when nothing listens. */
export function requestShotFocus(shotId: string): void {
  for (const listener of [...listeners]) {
    try {
      listener(shotId);
    } catch (err) {
      console.error('[shotFocusBus] listener failed:', err);
    }
  }
}

/** True when an editor is mounted and able to honour a focus request. Lets a
 *  caller render the card as plain text rather than a dead-looking button. */
export function hasShotFocusListener(): boolean {
  return listeners.size > 0;
}
