/**
 * notificationMerge pure tests — the read-layer merge of the two notification
 * feeds: newest-first ordering across both, unread = sum of both, broadcast
 * `content`→`body` mapping, and empty inputs.
 */

import { describe, expect, it } from 'vitest';

import {
  countUnread,
  mergeNotifications,
  toBroadcastUnified,
} from './notificationMerge';
import type { InboxNotification } from './notificationsService';
import type { NotificationWithRead } from './notificationService';

function inbox(over: Partial<InboxNotification> = {}): InboxNotification {
  return {
    id: '1',
    kind: 'generation_result',
    title: 'Inbox item',
    severity: 'info',
    read: false,
    created_at: '2026-07-20T10:00:00Z',
    ...over,
  };
}

function broadcast(over: Partial<NotificationWithRead> = {}): NotificationWithRead {
  return {
    id: '900',
    type: 'system',
    title: 'Release notes',
    content: 'What is new',
    team_id: null,
    created_by: null,
    created_at: '2026-07-20T11:00:00Z',
    read: false,
    ...over,
  };
}

describe('mergeNotifications', () => {
  it('interleaves both feeds newest-first by created_at', () => {
    const merged = mergeNotifications(
      [
        inbox({ id: 'i1', created_at: '2026-07-20T10:00:00Z' }),
        inbox({ id: 'i2', created_at: '2026-07-20T12:00:00Z' }),
      ],
      [broadcast({ id: 'b1', created_at: '2026-07-20T11:00:00Z' })],
    );
    expect(merged.map((n) => n.id)).toEqual(['i2', 'b1', 'i1']);
    expect(merged.map((n) => n.source)).toEqual(['inbox', 'broadcast', 'inbox']);
  });

  it('maps a broadcast row into the shared shape (content → body, stringified id)', () => {
    const u = toBroadcastUnified(broadcast({ id: 900, content: 'Notes text' } as never));
    expect(u).toMatchObject({
      source: 'broadcast',
      id: '900',
      body: 'Notes text',
      broadcast_type: 'system',
      read: false,
    });
    expect(typeof u.id).toBe('string');
  });

  it('keeps a broadcast row with null content as null body', () => {
    const u = toBroadcastUnified(broadcast({ content: null }));
    expect(u.body).toBeNull();
  });

  it('returns an empty list when both feeds are empty', () => {
    expect(mergeNotifications([], [])).toEqual([]);
  });

  it('tolerates missing timestamps without throwing (sorts them last)', () => {
    const merged = mergeNotifications(
      [inbox({ id: 'i1', created_at: null })],
      [broadcast({ id: 'b1', created_at: '2026-07-20T11:00:00Z' })],
    );
    expect(merged.map((n) => n.id)).toEqual(['b1', 'i1']);
  });
});

describe('countUnread', () => {
  it('sums unread across both feeds', () => {
    const merged = mergeNotifications(
      [inbox({ id: 'i1', read: false }), inbox({ id: 'i2', read: true })],
      [broadcast({ id: 'b1', read: false }), broadcast({ id: 'b2', read: false })],
    );
    expect(countUnread(merged)).toBe(3); // i1 + b1 + b2
  });

  it('is 0 when everything is read', () => {
    const merged = mergeNotifications(
      [inbox({ read: true })],
      [broadcast({ read: true })],
    );
    expect(countUnread(merged)).toBe(0);
  });
});
