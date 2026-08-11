/**
 * Per-script rail-view persistence (Phase B canvas polish follow-up F2).
 *
 * The centre-pane rail view (script sheet / node canvas / beat sheet) is a
 * per-script viewing preference, not authoritative script data. We keep the
 * choice client-side under `editor.railView.<scriptId>`, mirroring the
 * `editor.format` pattern in formatStorage — no network, no clobber — so a
 * writer who left the node canvas open returns to it after a reload/remount
 * instead of being dropped back on the script sheet.
 */
import type { RailView } from './components/RailModules';

const VALID: readonly RailView[] = ['script', 'nodes', 'beats'];
const storageKey = (scriptId: string): string => `editor.railView.${scriptId}`;

export function readStoredRailView(scriptId: string): RailView | null {
  try {
    const value = localStorage.getItem(storageKey(scriptId));
    return VALID.includes(value as RailView) ? (value as RailView) : null;
  } catch {
    return null;
  }
}

export function persistRailView(scriptId: string, view: RailView): void {
  try {
    localStorage.setItem(storageKey(scriptId), view);
  } catch (err) {
    // Non-fatal: a writer in private mode just loses the per-script memory.
    console.error('[editor] failed to persist rail view', err);
  }
}
