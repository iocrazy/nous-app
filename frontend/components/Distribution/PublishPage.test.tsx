import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { vi, describe, it, expect } from 'vitest';

// vi.mock is hoisted above top-level consts, so the fn it references must be
// hoisted too (vi.hoisted) — otherwise "Cannot access before initialization".
const { createPublishTask } = vi.hoisted(() => ({
  createPublishTask: vi.fn().mockResolvedValue({ id: '700', accounts: [] }),
}));

vi.mock('../../services/distributionService', () => ({
  listAccounts: vi.fn().mockResolvedValue([
    { id: '10', scope_type: 'user', scope_id: 'u1', platform: 'douyin',
      platform_user_id: 'op1', username: 'HEYGO', avatar_url: null,
      token_expires_at: null, status: 'active', created_at: '2026-07-08T00:00:00Z' },
    { id: '11', scope_type: 'user', scope_id: 'u1', platform: 'douyin',
      platform_user_id: 'op2', username: 'Expired One', avatar_url: null,
      token_expires_at: null, status: 'expired', created_at: '2026-07-08T00:00:00Z' },
  ]),
  listLibraryVideos: vi.fn().mockResolvedValue([
    { id: '30', filename: 'clip-a.mp4', thumbnail_url: null },
  ]),
  createPublishTask,
}));

vi.mock('react-router-dom', async (orig) => ({
  ...(await orig<typeof import('react-router-dom')>()),
  useNavigate: () => vi.fn(),
}));

// PublishPage calls useToast — mock it so the test needn't wrap ToastProvider.
vi.mock('../Toast', () => ({ useToast: () => ({ addToast: vi.fn() }) }));

import PublishPage from './PublishPage';

describe('PublishPage', () => {
  it('publishes only after content + account + title are chosen', async () => {
    render(<MemoryRouter><PublishPage /></MemoryRouter>);
    await waitFor(() => expect(screen.getByLabelText('clip-a.mp4')).toBeInTheDocument());

    const publishBtn = screen.getByRole('button', { name: /Publish now/i });
    expect(publishBtn).toBeDisabled();

    fireEvent.click(screen.getByLabelText('clip-a.mp4'));           // pick content
    fireEvent.click(screen.getByText('HEYGO'));                    // pick account
    fireEvent.change(screen.getByPlaceholderText(/Add a title/i), {
      target: { value: 'Launch day' },
    });
    expect(publishBtn).not.toBeDisabled();

    fireEvent.click(publishBtn);
    await waitFor(() => expect(createPublishTask).toHaveBeenCalledTimes(1));
    const arg = createPublishTask.mock.calls[0][0];
    expect(arg.resource_ids).toEqual(['30']);
    expect(arg.account_ids).toEqual(['10']);
    expect(arg.title).toBe('Launch day');
  });

  it('marks expired accounts non-selectable', async () => {
    render(<MemoryRouter><PublishPage /></MemoryRouter>);
    await waitFor(() => expect(screen.getByText('Expired One')).toBeInTheDocument());
    expect(screen.getByText(/Expired/i)).toBeInTheDocument();
  });
});
