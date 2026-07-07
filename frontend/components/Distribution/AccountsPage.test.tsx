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
    expect(screen.getByText(/Authorization expired/i)).toBeInTheDocument();
    expect(screen.getByText(/Reauthorize/i)).toBeInTheDocument();
    expect(screen.getByText(/Connect Account/i)).toBeInTheDocument();
  });
});
