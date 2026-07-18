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

  it('promotes a shadow tag: updateTag called with curated origin, row enters the curated grid', async () => {
    render(<TagsSettings />);
    await waitFor(() => expect(screen.getByText('ai')).toBeTruthy());
    fireEvent.click(screen.getByText('From notes'));
    expect(screen.getByText('#scratch')).toBeTruthy();

    fireEvent.click(screen.getByText('Promote'));
    await waitFor(() =>
      expect(updateTag).toHaveBeenCalledWith('2', { origin: 'curated' }),
    );
    // After promotion the shadow entry is gone (moved out of "From notes")...
    await waitFor(() => expect(screen.queryByText('#scratch')).toBeNull());
    // ...and now renders in the main curated grid, same as any other
    // curated tag (display name without the "#" prefix — see the 'ai'
    // assertion above for how curated-grid tags are found).
    expect(screen.getByText('scratch')).toBeTruthy();
  });

  it('promote is optimistic: row enters the curated grid before the network call resolves', async () => {
    let resolveUpdate: (value: unknown) => void = () => {};
    updateTag.mockReset().mockReturnValue(
      new Promise((resolve) => {
        resolveUpdate = resolve;
      }),
    );
    render(<TagsSettings />);
    await waitFor(() => expect(screen.getByText('ai')).toBeTruthy());
    fireEvent.click(screen.getByText('From notes'));
    expect(screen.getByText('#scratch')).toBeTruthy();

    fireEvent.click(screen.getByText('Promote'));
    // Before the promise resolves, the row has already moved into the
    // curated grid — this is what "truly optimistic" means (patch state
    // BEFORE the await, like handleToggleEnabled).
    await waitFor(() => expect(screen.queryByText('#scratch')).toBeNull());
    expect(screen.getByText('scratch')).toBeTruthy();

    resolveUpdate({ id: '2', name: 'scratch', origin: 'curated' });
    await waitFor(() => expect(screen.getByText('scratch')).toBeTruthy());
  });

  it('reverts to the shadow section and surfaces an error when promote fails', async () => {
    updateTag.mockReset().mockRejectedValue(new Error('Promote failed'));
    render(<TagsSettings />);
    await waitFor(() => expect(screen.getByText('ai')).toBeTruthy());
    fireEvent.click(screen.getByText('From notes'));
    expect(screen.getByText('#scratch')).toBeTruthy();

    fireEvent.click(screen.getByText('Promote'));
    await waitFor(() =>
      expect(updateTag).toHaveBeenCalledWith('2', { origin: 'curated' }),
    );
    // On failure, the row reverts back to the shadow section.
    await waitFor(() => expect(screen.getByText('#scratch')).toBeTruthy());
    expect(screen.getByText('Promote failed')).toBeTruthy();
  });
});

describe('TagsSettings — shadow row opens the edit dialog (rename hint reachability)', () => {
  beforeEach(() => {
    fetchAllTags.mockReset().mockResolvedValue([curated, shadow]);
    fetchTagGroups.mockReset().mockResolvedValue([]);
    updateTag.mockReset().mockResolvedValue({
      id: '2',
      name: 'scratch',
      origin: 'curated',
    });
  });

  it('clicking a shadow row opens the edit dialog and shows the rename hint', async () => {
    render(<TagsSettings />);
    await waitFor(() => expect(screen.getByText('ai')).toBeTruthy());
    fireEvent.click(screen.getByText('From notes'));
    expect(screen.getByText('#scratch')).toBeTruthy();

    // Click the "#scratch" text itself — the entry point for shadow rows.
    fireEvent.click(screen.getByText('#scratch'));

    // The mocked t() has no fallback for 'settings.tags.editTag', so it
    // renders the raw key — that's the modal's title text under this mock.
    expect(screen.getByText('settings.tags.editTag')).toBeTruthy();
    expect(
      screen.getByText(
        'Notes keep their original #text; editing a note re-creates the old tag.',
      ),
    ).toBeTruthy();
  });

  it('clicking Promote does not also open the edit dialog', async () => {
    render(<TagsSettings />);
    await waitFor(() => expect(screen.getByText('ai')).toBeTruthy());
    fireEvent.click(screen.getByText('From notes'));

    fireEvent.click(screen.getByText('Promote'));

    expect(screen.queryByText('settings.tags.editTag')).toBeNull();
  });

  it('a curated tag\'s edit dialog does not show the rename hint', async () => {
    render(<TagsSettings />);
    await waitFor(() => expect(screen.getByText('ai')).toBeTruthy());

    fireEvent.click(screen.getByText('ai'));

    expect(screen.getByText('settings.tags.editTag')).toBeTruthy();
    expect(
      screen.queryByText(
        'Notes keep their original #text; editing a note re-creates the old tag.',
      ),
    ).toBeNull();
  });
});
