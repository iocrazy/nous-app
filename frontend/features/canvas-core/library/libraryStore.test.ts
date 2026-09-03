import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import { LIBRARY_STORAGE_KEY, useLibraryStore } from './libraryStore';

beforeEach(() => {
  localStorage.clear();
  useLibraryStore.setState(useLibraryStore.getInitialState(), true);
});
afterEach(() => localStorage.clear());

describe('libraryStore', () => {
  it('opens closed, on the Media page, with no target', () => {
    const s = useLibraryStore.getState();
    expect(s.open).toBe(false);
    expect(s.page).toBe('media');
    expect(s.target).toBeNull();
  });

  it('openPanel carries a page, a store and a target in one call', () => {
    useLibraryStore.getState().openPanel({
      page: 'media',
      mediaStore: 'generated',
      target: { nodeId: 'p1', kind: 'prompt', title: 'Harbour' },
    });
    const s = useLibraryStore.getState();
    expect(s.open).toBe(true);
    expect(s.mediaStore).toBe('generated');
    expect(s.target?.nodeId).toBe('p1');
  });

  it('toggle closes an open panel and clears the target with it', () => {
    const s = () => useLibraryStore.getState();
    s().openPanel({ target: { nodeId: 'p1', kind: 'prompt', title: 'Harbour' } });
    s().toggle();
    expect(s().open).toBe(false);
    // A target that outlives its panel would silently re-arm the next open.
    expect(s().target).toBeNull();
  });

  it('focusSearch bumps a nonce rather than holding a ref', () => {
    const before = useLibraryStore.getState().focusNonce;
    useLibraryStore.getState().openPanel({ focusSearch: true });
    expect(useLibraryStore.getState().focusNonce).toBe(before + 1);
  });

  it('persists page / store / width, and nothing else', () => {
    const s = useLibraryStore.getState();
    s.openPanel({ mediaStore: 'assets' });
    s.setWidth(420);
    s.setQuery('harbour');
    s.setSelection(['uploads:1']);
    const saved = JSON.parse(localStorage.getItem(LIBRARY_STORAGE_KEY) ?? '{}');
    expect(saved).toEqual({ page: 'media', mediaStore: 'assets', width: 420 });
  });

  it('a corrupt stored value is ignored, not thrown on', () => {
    localStorage.setItem(LIBRARY_STORAGE_KEY, '{not json');
    expect(() => useLibraryStore.getState().setWidth(360)).not.toThrow();
  });
});
