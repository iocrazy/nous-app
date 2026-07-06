import { useEffect, useRef, useState } from 'react';
import type { RealtimeChannel } from '@supabase/supabase-js';
import { getSupabaseClient } from '../../supabaseClient';

// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------

/** How a present user is annotated: idly viewing vs. holding local dirty edits. */
export type PresenceMode = 'viewing' | 'editing';

/** One other participant currently tracked in the script presence channel. */
export interface PresenceUser {
  user_id: string;
  name: string;
  /** The scene id this user is focused on, or null when not on any scene. */
  focused_scene_id: string | null;
  mode: PresenceMode;
}

/**
 * Identity + live focus of the local user. `isDirty` is the injectable dirty
 * signal that drives `mode` — Task 1 passes a static value; Task 3 wires it to
 * the real per-scene sync queue state.
 */
export interface ScriptPresenceSelf {
  userId: string;
  name: string;
  focusedSceneId: string | null;
  isDirty?: boolean;
}

export interface ScriptPresence {
  /** Other participants (self excluded), recomputed from the authoritative snapshot. */
  onlineUsers: PresenceUser[];
}

// Supabase presenceState() returns an open record keyed by presence key.
type PresenceEntry = Record<string, unknown>;

/** Minimum gap between outbound presence re-tracks (ms), mirrors useChannelPresence. */
const TRACK_THROTTLE_MS = 2_000;

const EMPTY: PresenceUser[] = [];

// ---------------------------------------------------------------------------
// Hook
// ---------------------------------------------------------------------------

/**
 * Opens a Supabase Realtime presence channel `script-presence-<scriptId>`
 * (separate from any postgres_changes channel) and tracks the local user's
 * identity, focused scene, and mode.
 *
 * `onlineUsers` is recomputed from `presenceState()` on every presence event
 * (sync/join/leave all recompute the full snapshot rather than mutating
 * incrementally) and always excludes the local user.
 *
 * Re-tracks (throttled to ≤1 per 2 s, trailing-edge preserved) whenever the
 * focused scene, name, or mode changes. Returns a stable empty result when
 * `scriptId`, `me`, or the Supabase client is unavailable — safe to call
 * unconditionally, so a flag-off caller passes `null` and nothing subscribes.
 */
export function useScriptPresence(
  scriptId: string | null,
  me: ScriptPresenceSelf | null,
): ScriptPresence {
  const [onlineUsers, setOnlineUsers] = useState<PresenceUser[]>(EMPTY);
  const [subscribed, setSubscribed] = useState(false);

  const channelRef = useRef<RealtimeChannel | null>(null);
  // Last outbound track time + a pending trailing-edge timer for the throttle.
  const lastTrackRef = useRef(0);
  const trailingTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Latest payload, kept in a ref so the throttle's trailing fire reads current
  // values without re-subscribing the tracking effect.
  const mode: PresenceMode = me?.isDirty ? 'editing' : 'viewing';
  const payloadRef = useRef<PresenceUser>({
    user_id: me?.userId ?? '',
    name: me?.name ?? '',
    focused_scene_id: me?.focusedSceneId ?? null,
    mode,
  });
  payloadRef.current = {
    user_id: me?.userId ?? '',
    name: me?.name ?? '',
    focused_scene_id: me?.focusedSceneId ?? null,
    mode,
  };

  // ----- Channel lifecycle (keyed on channel identity only) ----------------

  useEffect(() => {
    if (!scriptId || !me) return;
    const supabase = getSupabaseClient();
    if (!supabase) return;

    const selfId = me.userId;
    const ch = supabase.channel('script-presence-' + scriptId, {
      config: { presence: { key: selfId } },
    });

    // Recompute the online set from the full presence snapshot (authoritative
    // after any event); the local user is always filtered out.
    const recompute = () => {
      const state = ch.presenceState() as Record<string, PresenceEntry[]>;
      const users: PresenceUser[] = [];
      for (const key of Object.keys(state)) {
        const entries = state[key];
        if (!entries || entries.length === 0) continue;
        const p = entries[0];
        const uid = typeof p.user_id === 'string' ? p.user_id : key;
        if (uid === selfId) continue;
        users.push({
          user_id: uid,
          name: typeof p.name === 'string' ? p.name : uid,
          focused_scene_id: typeof p.focused_scene_id === 'string' ? p.focused_scene_id : null,
          mode: p.mode === 'editing' ? 'editing' : 'viewing',
        });
      }
      setOnlineUsers(users);
    };

    // All .on() handlers are attached BEFORE .subscribe() (Realtime contract).
    ch
      .on('presence', { event: 'sync' }, () => { recompute(); })
      .on('presence', { event: 'join' }, () => { recompute(); })
      .on('presence', { event: 'leave' }, () => { recompute(); })
      .subscribe((status) => {
        if (status === 'SUBSCRIBED') setSubscribed(true);
      });

    channelRef.current = ch;

    return () => {
      if (trailingTimerRef.current !== null) {
        clearTimeout(trailingTimerRef.current);
        trailingTimerRef.current = null;
      }
      supabase.removeChannel(ch);
      channelRef.current = null;
      lastTrackRef.current = 0;
      setSubscribed(false);
      setOnlineUsers(EMPTY);
    };
  }, [scriptId, me?.userId]); // re-open only when the channel identity changes

  // ----- Track / re-track (throttled, trailing-edge preserved) -------------

  useEffect(() => {
    if (!subscribed) return;
    const ch = channelRef.current;
    if (!ch) return;

    const trackNow = () => {
      lastTrackRef.current = Date.now();
      void ch.track(payloadRef.current);
    };

    const elapsed = Date.now() - lastTrackRef.current;
    if (elapsed >= TRACK_THROTTLE_MS) {
      trackNow();
    } else if (trailingTimerRef.current === null) {
      trailingTimerRef.current = setTimeout(() => {
        trailingTimerRef.current = null;
        trackNow();
      }, TRACK_THROTTLE_MS - elapsed);
    }
  }, [subscribed, me?.focusedSceneId, me?.name, mode]);

  return { onlineUsers };
}
