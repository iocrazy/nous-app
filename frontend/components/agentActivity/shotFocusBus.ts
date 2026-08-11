/**
 * A tiny pub/sub so "click a shot card the agent just wrote" can reach the
 * storyboard canvas.
 *
 * The publisher (AIChatPanel, inside FloatingChatWidget) and the subscriber
 * (EditorShell, historically — now `EpisodeStoryboardPage`, see below) are
 * SIBLINGS — ScriptEditor mounts both, and the widget is also mounted
 * app-wide by AppLayout — so there is no shared React state to thread a
 * callback through. A module-level bus is the smallest thing that connects
 * them without hoisting editor state into a global store.
 *
 * Deliberately fire-and-forget: if no subscriber is mounted (the panel is
 * open on another route, or the script isn't loaded), the request is
 * dropped. A chip that silently does nothing is better than one that
 * navigates the user away from what they were reading.
 *
 * ⚠️ Keep-alive trap (Task 7, shot-nodes-on-canvas epic, review round 1):
 * "subscribed" (`listeners.size > 0`) stopped being the same fact as
 * "visible" once `EpisodeStoryboardPage` — the sole subscriber since Task 6
 * retired the editor's own — started staying mounted-and-subscribed across
 * module switches instead of unmounting (see that page's `active` prop).
 * `hasShotFocusListener()` accounts for this via `setShotFocusConsumerActive`
 * — see both functions' own doc comments — but any NEW direct consumer of
 * `listeners`/`onShotFocus` in this file must remember the same distinction:
 * a subscription existing is no longer proof that a focus request will
 * actually be acted on.
 */

export type ShotFocusListener = (shotId: string) => void;

const listeners = new Set<ShotFocusListener>();

/**
 * Whether the current subscriber can actually ACT on a focus request right
 * now, as opposed to merely being subscribed (Task 7, shot-nodes-on-canvas
 * epic — keep-alive 显隐切换 review round 1). Before Task 7, "subscribed"
 * and "visible" were the same fact: the storyboard page only ever mounted
 * (and therefore only ever called `onShotFocus`) while it was the active
 * workspace module — an exclusive ternary in `ProjectWorkspace`. Task 7 kept
 * that page (and its subscription) resident across module switches instead
 * of unmounting it, so a page that's merely hidden now stays subscribed
 * while its OWN `active` guard silently drops every event it receives (see
 * `EpisodeStoryboardPage`'s `onShotFocus` effect). Left unaddressed,
 * `hasShotFocusListener` would go permanently `true` the first time the
 * storyboard module is ever opened, even while hidden — exactly the
 * "looks clickable, silently does nothing" trap its OWN doc comment exists
 * to prevent.
 *
 * A single module-level flag, not per-listener: there is exactly one real
 * subscriber today (`EpisodeStoryboardPage`), so "the current subscriber's
 * reported state" is unambiguous. A second concurrent active-aware
 * subscriber would need this to become a per-listener map instead — not
 * done here since there's no such caller to design against yet (see
 * `hasShotFocusListener`'s own doc comment on why this whole export is
 * still dead code in production).
 */
let consumerActive = true;

/** Subscribe; returns the unsubscribe function (useEffect-shaped). */
export function onShotFocus(listener: ShotFocusListener): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

/**
 * Reports whether the shotFocusBus subscriber can honour a request right
 * now — call this from the SAME component that calls `onShotFocus`, kept in
 * sync with whatever visibility signal that component owns
 * (`EpisodeStoryboardPage` does this in a `useEffect` keyed on its own
 * `active` prop, resetting back to the neutral default on unmount so a
 * later, unrelated subscriber never inherits a stale `false`). See
 * `consumerActive`'s own doc comment for the keep-alive trap this exists to
 * close.
 */
export function setShotFocusConsumerActive(active: boolean): void {
  consumerActive = active;
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

/**
 * True when a subscriber is mounted AND currently reports itself active/
 * visible enough to honour a focus request — NOT merely "has an
 * `onShotFocus` handler registered somewhere" (see `consumerActive`'s doc
 * comment for why that distinction became real once Task 7's keep-alive
 * change let a subscriber stay mounted while hidden). Lets a caller render
 * the card as plain text rather than a dead-looking button.
 *
 * Still DEAD CODE in production as of this comment (no caller besides this
 * file's own test) — kept, and kept CORRECT, because the contract it
 * documents is exactly what a future consumer (e.g. an agent-panel shot
 * chip deciding whether to render a clickable vs. plain-text summary) would
 * need; a stale "subscribed-only" version would silently mislead whoever
 * writes that consumer into shipping a chip that looks clickable but is a
 * no-op whenever the storyboard page happens to be open-but-hidden.
 */
export function hasShotFocusListener(): boolean {
  return listeners.size > 0 && consumerActive;
}

/**
 * Second channel on the same bus: "something off-canvas (an Undo) changed
 * shot/scene data, reload." StoryboardView subscribes and re-fetches every
 * scene's shots; useRunUndo publishes after a successful undo. Same
 * fire-and-forget shape as shot focus above — no subscriber (storyboard not
 * mounted) just means the next mount picks up fresh data on its own load.
 */
export type StoryboardRefreshListener = () => void;

const refreshListeners = new Set<StoryboardRefreshListener>();

/** Subscribe; returns the unsubscribe function (useEffect-shaped). */
export function onStoryboardRefresh(listener: StoryboardRefreshListener): () => void {
  refreshListeners.add(listener);
  return () => {
    refreshListeners.delete(listener);
  };
}

/** Ask whoever is listening to reload storyboard data. No-op when nothing listens. */
export function requestStoryboardRefresh(): void {
  for (const listener of [...refreshListeners]) {
    try {
      listener();
    } catch (err) {
      console.error('[shotFocusBus] storyboard refresh listener failed:', err);
    }
  }
}
