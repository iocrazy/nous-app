import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';

// Same service-mock surface as TagsSettings.promote.test.tsx — keep every
// export the component imports so nothing throws on mount.
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
  name: 'copywriting',
  name_zh: '文案',
  origin: 'curated',
  type: 'user',
  color: null,
  icon: null,
  created_at: '',
};

// jsdom's synthetic KeyboardEvent doesn't always forward `isComposing`
// through fireEvent options, so dispatch a native event when the
// composition flag must reach `e.nativeEvent.isComposing`.
const keyDownComposing = (input: HTMLElement) => {
  const ev = new KeyboardEvent('keydown', { key: 'Enter', bubbles: true });
  Object.defineProperty(ev, 'isComposing', { value: true });
  input.dispatchEvent(ev);
};

describe('TagsSettings — search-or-create', () => {
  beforeEach(() => {
    fetchAllTags.mockReset().mockResolvedValue([curated]);
    fetchTagGroups.mockReset().mockResolvedValue([]);
    createTag.mockReset().mockResolvedValue({
      id: '99',
      name: 'newtag',
      origin: 'curated',
      type: 'user',
      color: null,
      icon: null,
      created_at: '',
    });
  });

  const typeSearch = async (value: string) => {
    render(<TagsSettings />);
    await waitFor(() => expect(screen.getByText('copywriting')).toBeTruthy());
    const input = screen.getByPlaceholderText('Search or create...');
    fireEvent.change(input, { target: { value } });
    return input;
  };

  it('shows an inline Create row when the query matches no existing tag', async () => {
    await typeSearch('newtag');
    expect(screen.getByText(/Create "newtag"/)).toBeInTheDocument();
  });

  it('Enter (non-composing) calls createTag with the query and the tag appears in the grid', async () => {
    const input = await typeSearch('newtag');
    fireEvent.keyDown(input, { key: 'Enter', isComposing: false });
    await waitFor(() =>
      expect(createTag).toHaveBeenCalledWith({ name: 'newtag' }),
    );
    // New tag lands in the (uncategorized) grid; the search clears.
    await waitFor(() => expect(screen.getByText('newtag')).toBeInTheDocument());
    expect((input as HTMLInputElement).value).toBe('');
  });

  it('clicking the Create row calls createTag and appends the tag', async () => {
    await typeSearch('brandnew');
    fireEvent.click(screen.getByText(/Create "brandnew"/));
    await waitFor(() =>
      expect(createTag).toHaveBeenCalledWith({ name: 'brandnew' }),
    );
  });

  it('an exact English-name match shows NO create affordance', async () => {
    await typeSearch('copywriting');
    expect(screen.queryByText(/Create "copywriting"/)).toBeNull();
  });

  it('an exact name_zh match shows NO create affordance', async () => {
    await typeSearch('文案');
    expect(screen.queryByText(/Create "文案"/)).toBeNull();
  });

  it('composing Enter does nothing (IME guard)', async () => {
    const input = await typeSearch('newtag');
    keyDownComposing(input);
    expect(createTag).not.toHaveBeenCalled();
  });

  it('surfaces an error via the banner when create fails', async () => {
    createTag.mockReset().mockRejectedValue(new Error('Create failed'));
    const input = await typeSearch('newtag');
    fireEvent.keyDown(input, { key: 'Enter', isComposing: false });
    await waitFor(() => expect(screen.getByText('Create failed')).toBeTruthy());
  });
});

// Regression coverage for the "shadow-search dead-end" finding: a query
// that matches ONLY a hidden shadow tag (origin='note') used to render
// silently — noExactMatch checked the full tag pool (so no Create
// affordance appeared) but filteredTags (curated-only) was empty, so the
// bare "No matching tags found" state rendered instead. The fix mirrors
// EagleTagBrowser's search-reveal: matching shadow tags now surface in the
// results area as a "From Notes" group with a Promote button.
describe('TagsSettings — search reveals matching shadow tags (dead-end fix)', () => {
  const shadowByName = {
    id: '10',
    name: 'brainwave',
    origin: 'note',
    type: 'user',
    color: null,
    icon: null,
    created_at: '',
  };
  const shadowByNameZh = {
    id: '11',
    name: 'daydream',
    name_zh: '白日梦',
    origin: 'note',
    type: 'user',
    color: null,
    icon: null,
    created_at: '',
  };

  beforeEach(() => {
    fetchAllTags
      .mockReset()
      .mockResolvedValue([curated, shadowByName, shadowByNameZh]);
    fetchTagGroups.mockReset().mockResolvedValue([]);
    updateTag.mockReset().mockResolvedValue({
      id: '10',
      name: 'brainwave',
      origin: 'curated',
    });
  });

  const search = async (value: string) => {
    render(<TagsSettings />);
    await waitFor(() => expect(screen.getByText('copywriting')).toBeTruthy());
    const input = screen.getByPlaceholderText('Search or create...');
    fireEvent.change(input, { target: { value } });
    return input;
  };

  it('empty query: shadow rows are not in the main results area', async () => {
    render(<TagsSettings />);
    await waitFor(() => expect(screen.getByText('copywriting')).toBeTruthy());
    expect(screen.queryByText('#brainwave')).toBeNull();
    expect(screen.queryByText('#daydream')).toBeNull();
  });

  it('exact name match on a shadow-only tag: no Create affordance, row visible with a Promote button', async () => {
    await search('brainwave');
    expect(screen.queryByText(/Create "brainwave"/)).toBeNull();
    expect(screen.getByText('#brainwave')).toBeTruthy();
    expect(screen.getByText('Promote')).toBeTruthy();
  });

  it('exact name_zh match on a shadow-only tag: no Create affordance, row visible with a Promote button', async () => {
    await search('白日梦');
    expect(screen.queryByText(/Create "白日梦"/)).toBeNull();
    expect(screen.getByText('#daydream')).toBeTruthy();
    expect(screen.getByText('Promote')).toBeTruthy();
  });

  it('partial shadow match: row is visible in the results area', async () => {
    await search('brain');
    expect(screen.getByText('#brainwave')).toBeTruthy();
  });

  it('clicking Promote on a matched shadow row calls updateTag with curated origin', async () => {
    await search('brainwave');
    fireEvent.click(screen.getByText('Promote'));
    await waitFor(() =>
      expect(updateTag).toHaveBeenCalledWith('10', { origin: 'curated' }),
    );
  });

  it('clicking the shadow row itself (not Promote) opens the edit dialog', async () => {
    await search('brainwave');
    fireEvent.click(screen.getByText('#brainwave'));
    expect(screen.getByText('settings.tags.editTag')).toBeTruthy();
  });
});
