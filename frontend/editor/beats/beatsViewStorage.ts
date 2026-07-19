/**
 * Per-script Beats sub-view persistence (M2) — mirrors railViewStorage's
 * bare-string localStorage pattern (no JSON, no backend clobber).
 *
 * The Beats pane has two sub-views: the timeline `arrangement` editor (default)
 * and the `list` editor. Which one a writer left open is a viewing preference,
 * not authoritative data, so it lives client-side under
 * `editor.beatsView.<scriptId>`.
 */

export type BeatsSubview = 'arrangement' | 'list';

export const DEFAULT_BEATS_SUBVIEW: BeatsSubview = 'arrangement';

const VALID: readonly BeatsSubview[] = ['arrangement', 'list'];
const storageKey = (scriptId: string): string => `editor.beatsView.${scriptId}`;

export function readStoredBeatsSubview(scriptId: string): BeatsSubview {
  try {
    const value = localStorage.getItem(storageKey(scriptId));
    return VALID.includes(value as BeatsSubview)
      ? (value as BeatsSubview)
      : DEFAULT_BEATS_SUBVIEW;
  } catch {
    return DEFAULT_BEATS_SUBVIEW;
  }
}

export function persistBeatsSubview(scriptId: string, view: BeatsSubview): void {
  try {
    localStorage.setItem(storageKey(scriptId), view);
  } catch (err) {
    // Non-fatal: a writer in private mode just loses the per-script memory.
    console.error('[editor] failed to persist beats sub-view', err);
  }
}
