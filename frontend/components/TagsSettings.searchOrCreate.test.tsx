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
