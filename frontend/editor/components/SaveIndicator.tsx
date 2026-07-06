/**
 * SaveIndicator — the always-on save status in the top bar (spec v3 §3.4 D4).
 *
 * Each scene owns its own useSceneSync, so the shell aggregates every block's
 * save state into one headline status with `aggregateSaveState` (worst-wins:
 * conflict > offline > retrying > saving > saved) and renders it here. The
 * region is `aria-live="polite"` so assistive tech announces transitions
 * (saved → saving → saved) without stealing focus.
 */
import { useTranslation } from 'react-i18next';
import type { SaveState } from '../useSceneSync';

// Worst-wins priority: a single unresolved conflict must dominate the headline.
const PRIORITY: SaveState[] = ['conflict', 'offline', 'retrying', 'saving', 'saved'];

export function aggregateSaveState(states: SaveState[]): SaveState {
  for (const s of PRIORITY) {
    if (states.includes(s)) return s;
  }
  return 'saved';
}

const LABEL_KEY: Record<SaveState, string> = {
  saved: 'editor.saved',
  saving: 'editor.saving',
  retrying: 'editor.retrying',
  offline: 'editor.offlineQueued',
  conflict: 'editor.conflictShort',
};

export interface SaveIndicatorProps {
  state: SaveState;
  /** Number of scenes with pending offline saves (shown only when offline). */
  queued?: number;
}

export function SaveIndicator({ state, queued = 0 }: SaveIndicatorProps) {
  const { t } = useTranslation();
  return (
    <div
      className={`mh-save-indicator ${state}`}
      data-testid="save-indicator"
      data-state={state}
      role="status"
      aria-live="polite"
    >
      {state === 'saved' ? (
        <span className="mh-save-check" aria-hidden="true">
          ✓
        </span>
      ) : (
        <span className="mh-save-dot" aria-hidden="true" />
      )}
      {t(LABEL_KEY[state])}
      {state === 'offline' && queued > 0 && <span className="mh-save-count">{queued}</span>}
    </div>
  );
}
