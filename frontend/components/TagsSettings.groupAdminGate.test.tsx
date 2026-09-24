import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';

// tag_groups is one platform-wide table and its write routes are admin-only
// (OpenAPI P6). Non-admins still see and browse groups, but get no create /
// rename / reorder / delete controls — before, they could click them and only
// ever saw a 403 toast.
const fetchAllTags = vi.fn();
const fetchTagGroups = vi.fn();

vi.mock('../services/unifiedTagService', () => ({
  fetchAllTags: (...a: unknown[]) => fetchAllTags(...a),
  fetchTagGroups: (...a: unknown[]) => fetchTagGroups(...a),
  updateTag: () => Promise.resolve(),
  deleteTag: () => Promise.resolve(),
  createTag: () => Promise.resolve(),
  createTagGroup: () => Promise.resolve(),
  renameTagGroup: () => Promise.resolve(),
  deleteTagGroup: () => Promise.resolve(),
  reorderTagGroups: () => Promise.resolve(),
}));

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (_k: string, fallback?: string) => fallback ?? _k,
    i18n: { language: 'en' },
  }),
}));

let role: string | undefined;
vi.mock('../contexts/AuthContext', () => ({
  useOptionalAuth: () => (role === undefined ? null : { userProfile: { role } }),
}));

import { TagsSettings } from './TagsSettings';

// Real wire shape of GET /tags/groups items (TagGroupItem): id is a string.
const group = { id: '7', name: 'Genre', sort_order: 1 };

describe('TagsSettings — group management is admin-only', () => {
  beforeEach(() => {
    fetchAllTags.mockReset().mockResolvedValue([]);
    fetchTagGroups.mockReset().mockResolvedValue([group]);
  });

  it.each([
    ['a regular user', 'user'],
    ['no auth provider', undefined],
  ])('hides the controls for %s', async (_label, r) => {
    role = r;
    render(<TagsSettings />);
    await waitFor(() => expect(screen.getByText('Genre')).toBeTruthy());
    expect(screen.queryByTitle('Add group')).toBeNull();
    expect(screen.queryByTitle('Double-click to rename')).toBeNull();
    expect(screen.getByText('Genre').closest('[draggable]')?.getAttribute('draggable')).toBe(
      'false',
    );
  });

  it('shows the controls for an admin', async () => {
    role = 'admin';
    render(<TagsSettings />);
    await waitFor(() => expect(screen.getByText('Genre')).toBeTruthy());
    expect(screen.getByTitle('Add group')).toBeTruthy();
    expect(screen.getByTitle('Double-click to rename')).toBeTruthy();
    expect(screen.getByText('Genre').closest('[draggable]')?.getAttribute('draggable')).toBe(
      'true',
    );
  });
});
