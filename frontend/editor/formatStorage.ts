/**
 * Per-script format persistence (spec v3 §3.3, Task 7).
 *
 * The layout engine (Hollywood vs Asian) is a per-script writer preference, not
 * authoritative script data. The backend PUT accepts only a full-replace
 * `settings_json` jsonb that already holds `format_preset`, so persisting format
 * there would mean a read-modify-write that risks clobbering that blob (the
 * known shared-jsonb trap). Instead we keep the choice client-side under
 * `editor.format.<scriptId>`, mirroring the `editor.theme` localStorage pattern
 * in useEditorState — no network, no clobber, and it survives reload/remount.
 */
import type { EditorFormat } from './useEditorState';

const storageKey = (scriptId: string): string => `editor.format.${scriptId}`;

export function readStoredFormat(scriptId: string): EditorFormat | null {
  try {
    const value = localStorage.getItem(storageKey(scriptId));
    return value === 'asian' || value === 'hollywood' ? value : null;
  } catch {
    return null;
  }
}

export function persistFormat(scriptId: string, format: EditorFormat): void {
  try {
    localStorage.setItem(storageKey(scriptId), format);
  } catch (err) {
    // Non-fatal: a writer in private mode just loses the per-script memory.
    console.error('[editor] failed to persist format', err);
  }
}
