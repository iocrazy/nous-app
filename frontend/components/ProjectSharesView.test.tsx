/**
 * ProjectSharesView reads the real `ProjectShareRow` wire shape: activity is
 * `status`, and the password is only ever surfaced as `has_password`. The
 * view used to read `is_active` / `password`, neither of which the endpoint
 * sends — every share rendered as Expired, the active count was always 0 and
 * the Protected badge never showed.
 */
import { render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import type { ProjectShare } from '../types/api';
import { ProjectSharesView } from './ProjectSharesView';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, fallback?: string) => (typeof fallback === 'string' ? fallback : key),
  }),
}));

const mockService = vi.hoisted(() => ({ fetchProjectShares: vi.fn() }));
vi.mock('../services/projectsService', () => mockService);

function share(over: Partial<ProjectShare>): ProjectShare {
  return {
    id: 7001,
    project_file_id: 9001,
    resource_id: null,
    folder_id: null,
    library_id: null,
    team_id: null,
    version_id: null,
    share_type: 'link',
    share_name: 'cut.mp4',
    share_code: 'abc123',
    shared_by: 'u1',
    has_password: false,
    allow_download: true,
    expires_at: null,
    max_views: null,
    view_count: 3,
    watermark: false,
    status: 'active',
    created_at: '2026-09-01T00:00:00Z',
    ...over,
  };
}

describe('ProjectSharesView', () => {
  it('derives Active / Protected / the active count from status and has_password', async () => {
    mockService.fetchProjectShares.mockResolvedValue([
      share({ id: 7001, share_code: 'live1', has_password: true }),
      share({ id: 7002, share_code: 'gone1', status: 'expired' }),
    ]);
    const onCountChange = vi.fn();

    render(<ProjectSharesView projectId="501" onCountChange={onCountChange} />);

    await waitFor(() => expect(onCountChange).toHaveBeenCalledWith(1));
    expect(screen.getAllByText('Active')).toHaveLength(1);
    expect(screen.getAllByText('Expired')).toHaveLength(1);
    expect(screen.getAllByText('Protected')).toHaveLength(1);
  });
});
