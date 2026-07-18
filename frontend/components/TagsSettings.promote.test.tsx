import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';

// Data-fetch entry for TagsSettings is `fetchAllTags` (imported as
// `fetchTags`). Mock the whole service module — keep the surface the
// component imports so nothing throws on mount.
const fetchAllTags = vi.fn();
const fetchTagGroups = vi.fn();
const updateTag = vi.fn();
const deleteTag = vi.fn();
const createTag = vi.fn();
const createTagGroup = vi.fn();
const renameTagGroup = vi.fn();
const deleteTagGroup = vi.fn();
const reorderTagGroups = vi.fn();

vi.mock('../services/unifiedTagService', () => ({
  fetchAllTags: (...a: unknown[]) => fetchAllTags(...a),
  fetchTagGroups: (...a: unknown[]) => fetchTagGroups(...a),
  updateTag: (...a: unknown[]) => updateTag(...a),
  deleteTag: (...a: unknown[]) => deleteTag(...a),
  createTag: (...a: unknown[]) => createTag(...a),
  createTagGroup: (...a: unknown[]) => createTagGroup(...a),
  renameTagGroup: (...a: unknown[]) => renameTagGroup(...a),
  deleteTagGroup: (...a: unknown[]) => deleteTagGroup(...a),
  reorderTagGroups: (...a: unknown[]) => reorderTagGroups(...a),
}));

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (_k: string, fallback?: string) => fallback ?? _k,
    i18n: { language: 'en' },
  }),
}));

import { TagsSettings } from './TagsSettings';

const curated = {
  id: '1',
  name: 'ai',
  origin: 'curated',
  type: 'user',
  color: null,
  icon: null,
  created_at: '',
};
const shadow = {
  id: '2',
  name: 'scratch',
  origin: 'note',
  type: 'user',
  color: null,
  icon: null,
  created_at: '',
};

describe('TagsSettings — From notes shadow section', () => {
  beforeEach(() => {
    fetchAllTags.mockReset().mockResolvedValue([curated, shadow]);
    fetchTagGroups.mockReset().mockResolvedValue([]);
    updateTag.mockReset().mockResolvedValue({
      id: '2',
      name: 'scratch',
      origin: 'curated',
    });
  });

  it('hides shadow tags from the main grid; shows them under From notes when expanded', async () => {
    render(<TagsSettings />);
    // curated tag renders in the main grid
    await waitFor(() => expect(screen.getByText('ai')).toBeTruthy());
    // shadow tag is NOT in the grid initially
    expect(screen.queryByText('#scratch')).toBeNull();
    expect(screen.queryByText('scratch')).toBeNull();

    // Expand "From notes"
    fireEvent.click(screen.getByText('From notes'));
    expect(screen.getByText('#scratch')).toBeTruthy();
  });

  it('promotes a shadow tag: updateTag called with curated origin, row leaves shadow section', async () => {
    render(<TagsSettings />);
    await waitFor(() => expect(screen.getByText('ai')).toBeTruthy());
    fireEvent.click(screen.getByText('From notes'));
    expect(screen.getByText('#scratch')).toBeTruthy();

    fireEvent.click(screen.getByText('Promote'));
    await waitFor(() =>
      expect(updateTag).toHaveBeenCalledWith('2', { origin: 'curated' }),
    );
    // After promotion the shadow entry is gone (moved into curated list).
    await waitFor(() => expect(screen.queryByText('#scratch')).toBeNull());
  });
});
