import { useCallback, useEffect, useRef, useState } from 'react';
import type { RealtimeChannel } from '@supabase/supabase-js';
import { getSupabaseClient } from '../supabaseClient';

// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------

export interface ChannelPresence {
  /** Distinct user ids currently tracked in the presence channel. */
  onlineUserIds: string[];
  /** Users who are currently typing (self excluded), self-expiring after ~4 s. */
  typingUsers: { user_id: string; name: string }[];
  /** Throttled (≤1 per 2 s) broadcast that signals the current user is typing. */
  sendTyping: () => void;
}

type TypingUser = { user_id: string; name: string };

// Supabase broadcast payload is an open record; cast to the known shape.
type TypingPayload = Record<string, unknown>;

/** Auto-expiry for a received typing event (ms). */
const TYPING_EXPIRY_MS = 4_000;
/** Minimum gap between outbound typing broadcasts (ms). */
const SEND_THROTTLE_MS = 2_000;

// ---------------------------------------------------------------------------
// Hook
// ---------------------------------------------------------------------------

/**
 * Opens a Supabase Realtime presence + broadcast channel
 * `chat-presence-<channelId>` (separate from the postgres_changes channel).
 *
 * Provides:
 *  - `onlineUserIds`  — distinct user ids tracked via presence.
 *  - `typingUsers`    — users currently typing (others only, self-expiring ~4 s).
 *  - `sendTyping`     — throttled broadcast, call on every keystroke from Composer.
 *
 * Returns a stable no-op object when `channelId`, `me`, or the Supabase client
 * are unavailable — safe to call unconditionally.
 */
export function useChannelPresence(
  channelId: string | null,
  me: { user_id: string; name: string } | null,
): ChannelPresence {
  const [onlineUserIds, setOnlineUserIds] = useState<string[]>([]);
  const [typingUsers, setTypingUsers] = useState<TypingUser[]>([]);

  // Ref to the active realtime channel so sendTyping stays stable.
  const channelRef = useRef<RealtimeChannel | null>(null);
  // Per-user self-expiry timers for typing state.
  const typingTimersRef = useRef<Map<string, ReturnType<typeof setTimeout>>>(new Map());
  // Last time we sent a typing broadcast (for throttle).
  const lastSentRef = useRef<number>(0);
  // Keeps the current `me` accessible inside the stable sendTyping callback.
  const meRef = useRef(me);
  meRef.current = me;

  // ----- Realtime channel lifecycle ----------------------------------------

  useEffect(() => {
    if (!channelId || !me) return;
    const supabase = getSupabaseClient();
    if (!supabase) return;

    const ch = supabase.channel('chat-presence-' + channelId, {
      config: { presence: { key: me.user_id } },
    });

    // Recompute online set from full presence state (authoritative after any event).
    const syncOnline = () => {
      setOnlineUserIds(Object.keys(ch.presenceState()));
    };

    // Upsert a typing user and (re)start their 4 s expiry timer.
    const addOrRefreshTyping = (user: TypingUser) => {
      setTypingUsers((prev) => {
        const exists = prev.some((u) => u.user_id === user.user_id);
        if (exists) {
          return prev.map((u) =>
            u.user_id === user.user_id ? { ...u, name: user.name } : u,
          );
        }
        return [...prev, user];
      });

      const existing = typingTimersRef.current.get(user.user_id);
      if (existing !== undefined) clearTimeout(existing);

      const timer = setTimeout(() => {
        setTypingUsers((prev) => prev.filter((u) => u.user_id !== user.user_id));
        typingTimersRef.current.delete(user.user_id);
      }, TYPING_EXPIRY_MS);

      typingTimersRef.current.set(user.user_id, timer);
    };

    ch
      .on('presence', { event: 'sync' }, () => { syncOnline(); })
      .on('presence', { event: 'join' }, () => { syncOnline(); })
      .on('presence', { event: 'leave' }, () => { syncOnline(); })
      .on('broadcast', { event: 'typing' }, ({ payload }) => {
        // payload comes in as Supabase's open broadcast record type.
        const p = payload as TypingPayload;
        const uid = p?.user_id;
        if (typeof uid !== 'string' || uid === me.user_id) return;
        const name = typeof p.name === 'string' ? p.name : uid;
        addOrRefreshTyping({ user_id: uid, name });
      })
      .subscribe(async (status) => {
        if (status === 'SUBSCRIBED') {
          // Track our presence; return value is 'ok' | 'error' | 'timed out'.
          await ch.track({ user_id: me.user_id, name: me.name });
        }
      });

    channelRef.current = ch;

    return () => {
      // Clear all pending typing expiry timers.
      typingTimersRef.current.forEach((t) => clearTimeout(t));
      typingTimersRef.current.clear();
      // Unsubscribe and release the channel.
      supabase.removeChannel(ch);
      channelRef.current = null;
      // Reset ephemeral state for the next channel.
      setOnlineUserIds([]);
      setTypingUsers([]);
    };
  }, [channelId, me?.user_id, me?.name]); // re-run when channel or identity changes

  // ----- Outbound typing broadcast (stable, throttled) ---------------------

  const sendTyping = useCallback(() => {
    const m = meRef.current;
    if (!m) return;
    const now = Date.now();
    if (now - lastSentRef.current < SEND_THROTTLE_MS) return;
    lastSentRef.current = now;
    channelRef.current?.send({
      type: 'broadcast',
      event: 'typing',
      payload: { user_id: m.user_id, name: m.name },
    });
  }, []); // stable — reads everything through refs, no closure over me

  return { onlineUserIds, typingUsers, sendTyping };
}
