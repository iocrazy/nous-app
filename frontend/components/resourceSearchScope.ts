// frontend/components/resourceSearchScope.ts
//
// Resource (My Uploads / Team Library) search scope — three checkbox
// options that mirror what the local filter in useResourcesDisplay
// actually scans (filename, notes, resource tag names). Persisted in
// localStorage under a key separate from the media scope so the two
// pickers stay independent.

import type { ScopeOption } from './ToolbarSearch';

export type ResourceSearchField = 'name' | 'notes' | 'tags';

export const ALL_RESOURCE_SCOPE: ResourceSearchField[] = ['name', 'notes', 'tags'];

export const DEFAULT_RESOURCE_SCOPE: ResourceSearchField[] = ['name', 'notes', 'tags'];

const RESOURCE_SCOPE_LABELS: Record<ResourceSearchField, string> = {
  name: 'Name',
  notes: 'Notes',
  tags: 'Tags',
};

export const RESOURCE_SCOPE_OPTIONS: ScopeOption[] = ALL_RESOURCE_SCOPE.map((id) => ({
  id,
  label: RESOURCE_SCOPE_LABELS[id],
  i18nKey: `search.scope.${id}`,
}));

const STORAGE_KEY = 'mediahub_resource_search_scope';

export function loadResourceSearchScope(): ResourceSearchField[] {
  if (typeof window === 'undefined') return DEFAULT_RESOURCE_SCOPE;
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) return DEFAULT_RESOURCE_SCOPE;
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed)) return DEFAULT_RESOURCE_SCOPE;
    const valid = parsed.filter((f): f is ResourceSearchField =>
      ALL_RESOURCE_SCOPE.includes(f as ResourceSearchField),
    );
    return valid.length > 0 ? valid : DEFAULT_RESOURCE_SCOPE;
  } catch {
    return DEFAULT_RESOURCE_SCOPE;
  }
}

export function saveResourceSearchScope(scope: ResourceSearchField[]): void {
  if (typeof window === 'undefined') return;
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(scope));
  } catch {
    /* ignore quota / private mode */
  }
}
