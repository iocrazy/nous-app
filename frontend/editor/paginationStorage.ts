/**
 * Per-script pagination-mode persistence — mirrors formatStorage (client-side
 * localStorage, no backend jsonb clobber risk). 'continuous' = one long flow,
 * 'paged' = laper-style dashed page rules + page numbers.
 */

export type PaginationMode = 'continuous' | 'paged';

const storageKey = (scriptId: string): string => `editor.pagination.${scriptId}`;

export function readStoredPagination(scriptId: string): PaginationMode | null {
  try {
    const value = localStorage.getItem(storageKey(scriptId));
    return value === 'paged' || value === 'continuous' ? value : null;
  } catch {
    return null;
  }
}

export function persistPagination(scriptId: string, mode: PaginationMode): void {
  try {
    localStorage.setItem(storageKey(scriptId), mode);
  } catch (err) {
    // Non-fatal: a writer in private mode just loses the per-script memory.
    console.error('[editor] failed to persist pagination mode', err);
  }
}
