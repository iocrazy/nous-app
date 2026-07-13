import { render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { vi, describe, it, expect } from 'vitest';
import { ToastProvider } from '../Toast';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (_k: string, d?: string) => d ?? _k }),
}));

vi.mock('../../services/distributionService', () => ({
  listAccounts: vi.fn().mockResolvedValue([
    {
      id: '727145299382534145', scope_type: 'user', scope_id: 'u1', platform: 'douyin',
      platform_user_id: 'op1', username: 'HEYGO', avatar_url: null,
      token_expires_at: null, status: 'active', created_at: '2026-07-07T00:00:00Z',
    },
    {
      id: '727145299382534146', scope_type: 'team', scope_id: 't1', platform: 'douyin',
      platform_user_id: 'op2', username: 'Studio Official', avatar_url: null,
      token_expires_at: null, status: 'expired', created_at: '2026-07-06T00:00:00Z',
    },
  ]),
  listPublishTasks: vi.fn().mockResolvedValue([
    {
      id: '900', content_type: 'video', title: 'Launch', description: null,
      topics: [], visibility: 'public', distribution_mode: 'broadcast',
      status: 'success', created_at: new Date().toISOString(),
      accounts: [
        { id: '1', account_id: '727145299382534145', username: 'HEYGO',
          avatar_url: null, channel: 'h5', status: 'success',
          error_message: null, published_url: null, platform_item_id: null,
          published_at: null },
      ],
    },
  ]),
  connectAccount: vi.fn(), refreshAccount: vi.fn(), deleteAccount: vi.fn(),
}));

import AccountsPage from './AccountsPage';

describe('AccountsPage', () => {
  it('renders accounts with status and scope badges', async () => {
    render(
      <ToastProvider>
        <MemoryRouter>
          <AccountsPage />
        </MemoryRouter>
      </ToastProvider>
    );
    await waitFor(() => expect(screen.getByText('HEYGO')).toBeInTheDocument());
    expect(screen.getByText('Studio Official')).toBeInTheDocument();
    // Expired appears as the status chip and the stats-card note.
    expect(screen.getAllByText(/Authorization expired/i).length).toBeGreaterThan(0);
    expect(screen.getByText(/Reauthorize/i)).toBeInTheDocument();
    expect(screen.getByText(/Connect Account/i)).toBeInTheDocument();
    // v4 additions: stats strip + platform panel + per-account usage meta.
    expect(screen.getByText(/Posts this week/i)).toBeInTheDocument();
    expect(screen.getByText(/Needs attention/i)).toBeInTheDocument();
    expect(screen.getByText(/Connect a platform/i)).toBeInTheDocument();
    // The i18next mock returns raw defaults without interpolation, so the
    // per-account usage meta renders its literal template.
    expect(screen.getByText('{{n}} posts')).toBeInTheDocument();
  });
});
