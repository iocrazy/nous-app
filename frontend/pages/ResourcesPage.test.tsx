import { render } from '@testing-library/react';
import { MemoryRouter, Routes, Route } from 'react-router-dom';
import { describe, it, expect, vi } from 'vitest';
import { ResourcesPage } from './ResourcesPage';

vi.mock('../components/ResourcesView', () => ({
  ResourcesView: vi.fn(() => null),
}));

vi.mock('../contexts/TeamContext', () => ({
  useTeamContext: () => ({ personalTeamId: '310812366953241' }),
}));

import { ResourcesView } from '../components/ResourcesView';

describe('ResourcesPage scope wiring', () => {
  // After Spec 1 PR-C, `resource_items.scope_id` / `folders.scope_id` /
  // `tags.scope_id` / `smart_collections.scope_id` are remapped to the
  // personal-team snowflake. Passing `currentUserId` (UUID) here would
  // silently return zero rows even though the data is intact.
  it('passes personalTeamId as scopeId in personal mode', () => {
    render(
      <MemoryRouter initialEntries={['/resources']}>
        <Routes>
          <Route path="/resources" element={<ResourcesPage />} />
        </Routes>
      </MemoryRouter>
    );

    expect(ResourcesView).toHaveBeenCalledWith(
      expect.objectContaining({
        isPersonal: true,
        scopeId: '310812366953241',
      }),
      undefined
    );
  });

  it('passes URL teamId as scopeId in team mode', () => {
    render(
      <MemoryRouter initialEntries={['/team/999000111/resources']}>
        <Routes>
          <Route path="/team/:teamId/resources" element={<ResourcesPage />} />
        </Routes>
      </MemoryRouter>
    );

    expect(ResourcesView).toHaveBeenCalledWith(
      expect.objectContaining({
        isPersonal: false,
        scopeId: '999000111',
      }),
      undefined
    );
  });
});
