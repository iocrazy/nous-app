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

/**
 * Narrow notification inbox (W3d). Loads the current user's inbox once on
 * login, then keeps the badge + list live via a Supabase Realtime subscription
 * on `inbox_notifications` (filtered to the user) — mirroring the mechanism
 * TaskManagerContext uses for `task_tracking`. The inbox carries exactly three
 * kinds (generation_result / publish_result / autopilot_output); everything
 * else stays out (see backend app/services/notifications.py).
 */

interface InboxContextValue {
  notifications: InboxNotification[];
  unreadCount: number;
  loading: boolean;
  refresh: () => Promise<void>;
  markRead: (id: string) => Promise<void>;
  markAllRead: () => Promise<void>;
}

const InboxContext = createContext<InboxContextValue | undefined>(undefined);

const MAX_ITEMS = 100;

function computeUnread(items: InboxNotification[]): number {
  return items.filter((n) => !n.read).length;
}

export const InboxProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const { currentUserId } = useAuth();
  const [notifications, setNotifications] = useState<InboxNotification[]>([]);
  const [loading, setLoading] = useState(false);
  const channelRef = useRef<ReturnType<ReturnType<typeof getSupabaseClient>['channel']> | null>(
    null,
  );

  const refresh = useCallback(async () => {
    if (!currentUserId) return;
    setLoading(true);
    try {
      const result = await listInbox({ limit: MAX_ITEMS });
      setNotifications(result.notifications);
    } catch (err) {
      console.error('[Inbox] refresh failed:', err);
    } finally {
      setLoading(false);
    }
  }, [currentUserId]);

  // Initial load on user change.
  useEffect(() => {
    if (!currentUserId) {
      setNotifications([]);
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
          setNotifications((prev) => {
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
          setNotifications((prev) => prev.map((n) => (n.id === next.id ? next : n)));
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

  const markRead = useCallback(async (id: string) => {
    // Optimistic — flip local state, then persist. On failure, refresh to
    // reconcile.
    setNotifications((prev) =>
      prev.map((n) => (n.id === id ? { ...n, read: true } : n)),
    );
    try {
      await markInboxRead(id);
    } catch (err) {
      console.error('[Inbox] markRead failed:', err);
    }
  }, []);

  const markAllRead = useCallback(async () => {
    setNotifications((prev) => prev.map((n) => ({ ...n, read: true })));
    try {
      await markAllInboxRead();
    } catch (err) {
      console.error('[Inbox] markAllRead failed:', err);
    }
  }, []);

  const value: InboxContextValue = {
    notifications,
    unreadCount: computeUnread(notifications),
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
