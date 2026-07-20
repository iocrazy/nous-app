/**
 * InboxContext — the read-layer merge wiring. Both backends are mocked so the
 * test drives: initial merge + summed unread, two-route mark-read (inbox → REST,
 * broadcast → junction), mark-all hitting both, and the empty state. Realtime is
 * disabled by returning a null supabase client.
 */

import { render, screen, waitFor, act } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { InboxProvider, useInbox } from './InboxContext';
import * as inboxSvc from '../services/notificationsService';
import * as broadcastSvc from '../services/notificationService';

vi.mock('./AuthContext', () => ({
  useAuth: () => ({ currentUserId: 'u1' }),
}));
vi.mock('../supabaseClient', () => ({
  getSupabaseClient: () => null, // no realtime channel in the test
}));
vi.mock('../services/notificationsService', () => ({
  listInbox: vi.fn(),
  markInboxRead: vi.fn().mockResolvedValue(undefined),
  markAllInboxRead: vi.fn().mockResolvedValue(undefined),
}));
vi.mock('../services/notificationService', () => ({
  fetchNotifications: vi.fn(),
  markAsRead: vi.fn().mockResolvedValue(undefined),
  markAllAsRead: vi.fn().mockResolvedValue(undefined),
}));

const listInbox = vi.mocked(inboxSvc.listInbox);
const markInboxRead = vi.mocked(inboxSvc.markInboxRead);
const markAllInboxRead = vi.mocked(inboxSvc.markAllInboxRead);
const fetchBroadcast = vi.mocked(broadcastSvc.fetchNotifications);
const markBroadcastRead = vi.mocked(broadcastSvc.markAsRead);
const markAllBroadcastRead = vi.mocked(broadcastSvc.markAllAsRead);

function Harness() {
  const { notifications, unreadCount, markRead, markAllRead } = useInbox();
  return (
    <div>
      <span data-testid="count">{unreadCount}</span>
      <span data-testid="len">{notifications.length}</span>
      <button data-testid="read-inbox" onClick={() => void markRead('i1', 'inbox')} />
      <button data-testid="read-bcast" onClick={() => void markRead('900', 'broadcast')} />
      <button data-testid="read-all" onClick={() => void markAllRead()} />
    </div>
  );
}

function renderInbox() {
  return render(
    <InboxProvider>
      <Harness />
    </InboxProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  listInbox.mockResolvedValue({
    notifications: [
      {
        id: 'i1',
        kind: 'generation_result',
        title: 'Download done',
        severity: 'success',
        read: false,
        created_at: '2026-07-20T10:00:00Z',
      },
    ],
    total: 1,
    unread_count: 1,
  });
  fetchBroadcast.mockResolvedValue([
    {
      id: '900',
      type: 'system',
      title: 'Release notes',
      content: 'v1.2 shipped',
      team_id: null,
      created_by: null,
      created_at: '2026-07-20T11:00:00Z',
      read: false,
    },
  ]);
});

describe('InboxContext merge', () => {
  it('merges both feeds and sums the unread count', async () => {
    renderInbox();
    await waitFor(() => expect(screen.getByTestId('len')).toHaveTextContent('2'));
    expect(screen.getByTestId('count')).toHaveTextContent('2');
    expect(listInbox).toHaveBeenCalledTimes(1);
    expect(fetchBroadcast).toHaveBeenCalledTimes(1);
  });

  it('routes an inbox mark-read to the REST inbox endpoint only', async () => {
    renderInbox();
    await waitFor(() => expect(screen.getByTestId('len')).toHaveTextContent('2'));
    await act(async () => {
      screen.getByTestId('read-inbox').click();
    });
    expect(markInboxRead).toHaveBeenCalledWith('i1');
    expect(markBroadcastRead).not.toHaveBeenCalled();
    // One row cleared → unread drops to 1.
    await waitFor(() => expect(screen.getByTestId('count')).toHaveTextContent('1'));
  });

  it('routes a broadcast mark-read to the junction endpoint only', async () => {
    renderInbox();
    await waitFor(() => expect(screen.getByTestId('len')).toHaveTextContent('2'));
    await act(async () => {
      screen.getByTestId('read-bcast').click();
    });
    expect(markBroadcastRead).toHaveBeenCalledWith('900');
    expect(markInboxRead).not.toHaveBeenCalled();
    await waitFor(() => expect(screen.getByTestId('count')).toHaveTextContent('1'));
  });

  it('mark-all clears both feeds and hits both backends', async () => {
    renderInbox();
    await waitFor(() => expect(screen.getByTestId('len')).toHaveTextContent('2'));
    await act(async () => {
      screen.getByTestId('read-all').click();
    });
    expect(markAllInboxRead).toHaveBeenCalledTimes(1);
    expect(markAllBroadcastRead).toHaveBeenCalledTimes(1);
    await waitFor(() => expect(screen.getByTestId('count')).toHaveTextContent('0'));
  });

  it('shows an empty, zero-unread state when both feeds are empty', async () => {
    listInbox.mockResolvedValue({ notifications: [], total: 0, unread_count: 0 });
    fetchBroadcast.mockResolvedValue([]);
    renderInbox();
    await waitFor(() => expect(screen.getByTestId('count')).toHaveTextContent('0'));
    expect(screen.getByTestId('len')).toHaveTextContent('0');
  });
});
