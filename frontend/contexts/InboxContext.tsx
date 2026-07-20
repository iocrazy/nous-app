import React, {
  createContext,
  useContext,
  useState,
  useEffect,
  useCallback,
  useRef,
} from 'react';
import { getSupabaseClient } from '../supabaseClient';
import { useAuth } from './AuthContext';
import {
  listInbox,
  markInboxRead,
  markAllInboxRead,
  type InboxNotification,
} from '../services/notificationsService';
import {
  fetchNotifications as fetchBroadcast,
  markAsRead as markBroadcastRead,
  markAllAsRead as markAllBroadcastRead,
  type NotificationWithRead,
} from '../services/notificationService';
import {
  mergeNotifications,
  countUnread,
  type NotificationSource,
  type UnifiedNotification,
} from '../services/notificationMerge';

/**
 * Unified notification inbox (W3d + release-notes merge). One bell, one panel,
 * one unread count drawn from TWO backends, merged at the read layer:
 *   - per-user `inbox_notifications` (REST /api/v1/inbox) — the narrow three
 *     kinds (generation_result / publish_result / autopilot_output), kept live
 *     by a Supabase Realtime subscription (mirrors TaskManagerContext).
 *   - broadcast `notifications` (release-note / team announcements) via the
 *     legacy junction — read straight from Supabase, refreshed on load.
 *
 * Read state stays per source: inbox rows POST to /api/v1/inbox, broadcast rows
 * upsert `user_notifications`. Nothing is copied between tables and no 4th inbox
 * kind is introduced (the backend narrowness guard stays intact).
 */

interface InboxContextValue {
  notifications: UnifiedNotification[];
  unreadCount: number;
  loading: boolean;
  refresh: () => Promise<void>;
  markRead: (id: string, source: NotificationSource) => Promise<void>;
  markAllRead: () => Promise<void>;
}

const InboxContext = createContext<InboxContextValue | undefined>(undefined);

const MAX_ITEMS = 100;

export const InboxProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const { currentUserId } = useAuth();
  const [inboxItems, setInboxItems] = useState<InboxNotification[]>([]);
  const [broadcastItems, setBroadcastItems] = useState<NotificationWithRead[]>([]);
  const [loading, setLoading] = useState(false);
  const channelRef = useRef<ReturnType<ReturnType<typeof getSupabaseClient>['channel']> | null>(
    null,
  );

  const refresh = useCallback(async () => {
    if (!currentUserId) return;
    setLoading(true);
    // Load both feeds independently — one failing must not blank the other.
    const [inboxRes, broadcastRes] = await Promise.allSettled([
      listInbox({ limit: MAX_ITEMS }),
      fetchBroadcast(),
    ]);
    if (inboxRes.status === 'fulfilled') {
      // A malformed response (undefined body / missing field) must degrade to
      // an empty feed, not crash the whole app shell at the merge `.map`.
      setInboxItems(inboxRes.value?.notifications ?? []);
    } else {
      console.error('[Inbox] inbox refresh failed:', inboxRes.reason);
    }
    if (broadcastRes.status === 'fulfilled') {
      setBroadcastItems(broadcastRes.value ?? []);
    } else {
      console.error('[Inbox] broadcast refresh failed:', broadcastRes.reason);
    }
    setLoading(false);
  }, [currentUserId]);

  // Initial load on user change.
  useEffect(() => {
    if (!currentUserId) {
      setInboxItems([]);
      setBroadcastItems([]);
      return;
    }
    void refresh();
  }, [currentUserId, refresh]);

  // Realtime subscription — mirror TaskManagerContext's channel mechanics.
  useEffect(() => {
    if (!currentUserId) return;
    const supabase = getSupabaseClient();
    if (!supabase) return;

    const channel = supabase
      .channel(`user-inbox-${currentUserId}`)
      .on(
        'postgres_changes',
        {
          event: 'INSERT',
          schema: 'public',
          table: 'inbox_notifications',
          filter: `user_id=eq.${currentUserId}`,
        },
        (payload) => {
          const row = payload.new as InboxNotification & { read_at?: string | null };
          const next: InboxNotification = { ...row, read: !!row.read_at };
          setInboxItems((prev) => {
            if (prev.some((n) => n.id === next.id)) return prev;
            return [next, ...prev].slice(0, MAX_ITEMS);
          });
        },
      )
      .on(
        'postgres_changes',
        {
          event: 'UPDATE',
          schema: 'public',
          table: 'inbox_notifications',
          filter: `user_id=eq.${currentUserId}`,
        },
        (payload) => {
          const row = payload.new as InboxNotification & { read_at?: string | null };
          const next: InboxNotification = { ...row, read: !!row.read_at };
          setInboxItems((prev) => prev.map((n) => (n.id === next.id ? next : n)));
        },
      )
      .subscribe();

    channelRef.current = channel;
    return () => {
      if (channelRef.current) {
        supabase.removeChannel(channelRef.current);
        channelRef.current = null;
      }
    };
  }, [currentUserId]);

  const markRead = useCallback(async (id: string, source: NotificationSource) => {
    // Optimistic — flip the row in its own feed, then persist to that feed's
    // backend (inbox → REST, broadcast → user_notifications junction).
    if (source === 'inbox') {
      setInboxItems((prev) => prev.map((n) => (n.id === id ? { ...n, read: true } : n)));
      try {
        await markInboxRead(id);
      } catch (err) {
        console.error('[Inbox] markRead (inbox) failed:', err);
      }
    } else {
      setBroadcastItems((prev) =>
        prev.map((n) => (String(n.id) === id ? { ...n, read: true } : n)),
      );
      try {
        await markBroadcastRead(id);
      } catch (err) {
        console.error('[Inbox] markRead (broadcast) failed:', err);
      }
    }
  }, []);

  const markAllRead = useCallback(async () => {
    // Clear both feeds locally, then persist to both backends.
    setInboxItems((prev) => prev.map((n) => ({ ...n, read: true })));
    setBroadcastItems((prev) => prev.map((n) => ({ ...n, read: true })));
    const results = await Promise.allSettled([
      markAllInboxRead(),
      markAllBroadcastRead(),
    ]);
    results.forEach((r) => {
      if (r.status === 'rejected') {
        console.error('[Inbox] markAllRead failed:', r.reason);
      }
    });
  }, []);

  const notifications = mergeNotifications(inboxItems, broadcastItems);

  const value: InboxContextValue = {
    notifications,
    unreadCount: countUnread(notifications),
    loading,
    refresh,
    markRead,
    markAllRead,
  };

  return <InboxContext.Provider value={value}>{children}</InboxContext.Provider>;
};

export function useInbox(): InboxContextValue {
  const ctx = useContext(InboxContext);
  if (!ctx) {
    throw new Error('useInbox must be used within InboxProvider');
  }
  return ctx;
}
