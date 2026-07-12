import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { vi, describe, it, expect } from 'vitest';

const retryPublishTask = vi.fn().mockResolvedValue({});
const getShareSchema = vi.fn().mockResolvedValue({ schema_url: 'snssdk1128://x', share_id: 's1' });

vi.mock('../../services/distributionService', () => ({
  listPublishTasks: vi.fn().mockResolvedValue([
    { id: '700', content_type: 'video', title: 'Awaiting', description: null, topics: [],
      visibility: 'public', distribution_mode: 'broadcast', status: 'pending_share',
      created_at: '2026-07-08T00:00:00Z',
      accounts: [{ id: '1', account_id: '10', username: 'HEYGO', avatar_url: null,
        channel: 'h5', status: 'pending_share', error_message: null, published_url: null,
        platform_item_id: null, published_at: null }] },
    { id: '701', content_type: 'video', title: 'Mixed', description: null, topics: [],
      visibility: 'public', distribution_mode: 'broadcast', status: 'partial',
      created_at: '2026-07-08T00:00:00Z',
      accounts: [
        { id: '2', account_id: '11', username: 'Ok One', avatar_url: null, channel: 'official',
          status: 'success', error_message: null, published_url: 'https://douyin/v/9',
          platform_item_id: '9', published_at: '2026-07-08T01:00:00Z' },
        { id: '3', account_id: '12', username: 'Bad One', avatar_url: null, channel: 'official',
          status: 'failed', error_message: 'upload rejected', published_url: null,
          platform_item_id: null, published_at: null }] },
    { id: '702', content_type: 'video', title: 'Done One', description: null, topics: [],
      visibility: 'public', distribution_mode: 'broadcast', status: 'success',
      created_at: '2026-07-08T00:00:00Z',
      accounts: [{ id: '4', account_id: '13', username: 'Winner', avatar_url: null,
        channel: 'official', status: 'success', error_message: null,
        published_url: 'https://douyin/v/12', platform_item_id: '12',
        published_at: '2026-07-08T02:00:00Z' }] },
  ]),
  cancelPublishTask: vi.fn(),
  retryPublishTask,
  getShareSchema,
}));

import RecordsPage from './RecordsPage';

describe('RecordsPage', () => {
  it('shows pending_share prompt and failed retry', async () => {
    render(<MemoryRouter><RecordsPage /></MemoryRouter>);
    await waitFor(() => expect(screen.getByText('Awaiting')).toBeInTheDocument());
    expect(screen.getByText(/Open Douyin to finish/i)).toBeInTheDocument();

    // expand the mixed task to reveal per-account rows
    fireEvent.click(screen.getByText('Mixed'));
    await waitFor(() => expect(screen.getByText('upload rejected')).toBeInTheDocument());
    fireEvent.click(screen.getByRole('button', { name: /Retry/i }));
    await waitFor(() => expect(retryPublishTask).toHaveBeenCalledWith('701'));
  });

  it('filters by status chips', async () => {
    render(<MemoryRouter><RecordsPage /></MemoryRouter>);
    await waitFor(() => expect(screen.getByText('Awaiting')).toBeInTheDocument());
    fireEvent.click(screen.getByRole('button', { name: /Needs action/i }));
    // needs_action includes pending_share AND partial (both have user actions);
    // success is excluded.
    expect(screen.getByText('Awaiting')).toBeInTheDocument();   // pending_share
    expect(screen.getByText('Mixed')).toBeInTheDocument();      // partial
    expect(screen.queryByText('Done One')).not.toBeInTheDocument(); // success excluded
  });
});
