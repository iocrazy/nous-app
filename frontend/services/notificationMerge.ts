/**
 * Unified notification feed (W3d #1477 tail).
 *
 * The bell shows ONE list drawn from two independent backends, merged at the
 * READ layer only — no row is ever copied between them:
 *   - inbox  : per-user `inbox_notifications` (REST /api/v1/inbox), the narrow
 *              three-kind feed (generation/publish/autopilot). Its Literal kind
 *              set is locked by a backend guard test — we add no 4th kind and
 *              never insert a broadcast row into it.
 *   - broadcast: the legacy `notifications` + `user_notifications` junction
 *              (release-note / team announcements), read straight from Supabase.
 *
 * Read state stays with each source: an inbox row carries its own `read`; a
 * broadcast row's read state lives in the `user_notifications` junction. This
 * module is pure (merge + sort + count) so the wiring in InboxContext is thin
 * and unit-testable without React or the network.
 */

import type { InboxNotification } from './notificationsService';
import type { NotificationWithRead } from './notificationService';

export type NotificationSource = 'inbox' | 'broadcast';

/** An inbox row, tagged with its source. */
export type UnifiedInboxNotification = { source: 'inbox' } & InboxNotification;

/** A broadcast (release-note / announcement) row, normalised to the shared shape. */
export interface UnifiedBroadcastNotification {
  source: 'broadcast';
  id: string;
  title: string;
  /** The announcement text (broadcast `content`). */
  body: string | null;
  read: boolean;
  created_at: string | null;
  /** 'system' (release notes) or 'team' (team announcement). */
  broadcast_type: 'system' | 'team';
}

export type UnifiedNotification =
  | UnifiedInboxNotification
  | UnifiedBroadcastNotification;

export function toInboxUnified(n: InboxNotification): UnifiedInboxNotification {
  return { source: 'inbox', ...n };
}

export function toBroadcastUnified(
  n: NotificationWithRead,
): UnifiedBroadcastNotification {
  return {
    source: 'broadcast',
    id: String(n.id),
    title: n.title,
    body: n.content ?? null,
    read: n.read,
    created_at: n.created_at ?? null,
    broadcast_type: n.type,
  };
}

function timestamp(iso: string | null | undefined): number {
  if (!iso) return 0;
  const ms = new Date(iso).getTime();
  return Number.isNaN(ms) ? 0 : ms;
}

/**
 * Merge the two feeds into one newest-first list. Neither input is mutated;
 * ties keep a stable order (inbox rows were pushed first).
 */
export function mergeNotifications(
  inbox: InboxNotification[],
  broadcast: NotificationWithRead[],
): UnifiedNotification[] {
  const merged: UnifiedNotification[] = [
    ...inbox.map(toInboxUnified),
    ...broadcast.map(toBroadcastUnified),
  ];
  return merged.sort((a, b) => timestamp(b.created_at) - timestamp(a.created_at));
}

/** Total unread across both feeds. */
export function countUnread(items: UnifiedNotification[]): number {
  return items.filter((n) => !n.read).length;
}
