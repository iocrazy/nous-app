/**
 * Per-script scene-TOC pin persistence — mirrors zoomStorage/railViewStorage
 * (client-side localStorage, no backend jsonb clobber risk).
 *
 * The Notion-style scene table-of-contents (SceneToc) floats over the paper as a
 * minimal tick rail; hovering reveals the full list, and the pin button keeps
 * that panel open. Pin is a per-script VIEWING preference, not authoritative
 * script data, so we keep it under `editor.sceneToc.<scriptId>` — a writer who
 * pinned the list returns to it pinned after a reload/remount.
 *
 * Stored as a BARE flag string ('1' pinned / '0' unpinned), not JSON; anything
 * else (missing / hand-edited) reads back as unpinned (the default).
 */

const storageKey = (scriptId: string): string => `editor.sceneToc.${scriptId}`;

export function readStoredTocPinned(scriptId: string): boolean {
  try {
    return localStorage.getItem(storageKey(scriptId)) === '1';
  } catch {
    return false;
  }
}

export function persistTocPinned(scriptId: string, pinned: boolean): void {
  try {
    localStorage.setItem(storageKey(scriptId), pinned ? '1' : '0');
  } catch (err) {
    // Non-fatal: a writer in private mode just loses the per-script memory.
    console.error('[editor] failed to persist scene-toc pin', err);
  }
}
