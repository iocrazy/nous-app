import { useEffect, useMemo, useRef, useState } from 'react';
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
/**
 * How long another user's `editing` mode stays shown after their last editing
 * track (ms). Mirrors useChannelPresence TYPING_EXPIRY. During active editing
 * the local user's re-tracks (≤ every 2 s) keep refreshing this, so it never
 * false-expires; a connected-but-idle "stuck editing" downgrades to viewing.
 */
const EDITING_EXPIRY_MS = 4_000;

const EMPTY: PresenceUser[] = [];
const EMPTY_SET: ReadonlySet<string> = new Set();

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
  // Raw snapshot straight from presenceState(); the exposed onlineUsers apply
  // the editing-expiry override on top (see the useMemo below).
  const [rawUsers, setRawUsers] = useState<PresenceUser[]>(EMPTY);
  // Users whose `editing` is still fresh (seen within EDITING_EXPIRY_MS).
  const [activeEditors, setActiveEditors] = useState<ReadonlySet<string>>(EMPTY_SET);
  const [subscribed, setSubscribed] = useState(false);

  const channelRef = useRef<RealtimeChannel | null>(null);
  // Last outbound track time + a pending trailing-edge timer for the throttle.
  const lastTrackRef = useRef(0);
  const trailingTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  // Per-user editing-expiry timers.
  const editingTimersRef = useRef<Map<string, ReturnType<typeof setTimeout>>>(new Map());

  // Latest payload, kept in a ref so the throttle's trailing fire reads current
  // values without re-subscribing the tracking effect.
  const mode: PresenceMode = me?.isDirty ? 'editing' : 'viewing';
  // focusedSceneId is a scene id (a JSON number at runtime, #1006) — String() it
  // so the wire payload and the per-scene grouping key are strings on both sides.
  const focusedSceneId = me?.focusedSceneId != null ? String(me.focusedSceneId) : null;
  const payloadRef = useRef<PresenceUser>({
    user_id: me?.userId ?? '',
    name: me?.name ?? '',
    focused_scene_id: focusedSceneId,
    mode,
  });
  payloadRef.current = {
    user_id: me?.userId ?? '',
    name: me?.name ?? '',
    focused_scene_id: focusedSceneId,
    mode,
  };

  // ----- Channel lifecycle (keyed on channel identity only) ----------------

  useEffect(() => {
    if (!scriptId || !me) return;
    const supabase = getSupabaseClient();
    if (!supabase) return;

    const selfId = me.userId;
    let ch: RealtimeChannel | null = null;
    let cancelled = false;

    void (async () => {
      // Apply the session token to the realtime socket BEFORE joining. Presence
      // itself has no RLS, but we take the same path as the op-stream channel
      // (see useScriptOpsRealtime for the anon-claims race) for consistency and
      // so broadcast auth keeps working if presence ever gains it.
      const { data } = await supabase.auth.getSession();
      const token = data.session?.access_token;
      if (!token || cancelled) return; // no session, or unmounted mid-await
      supabase.realtime.setAuth(token);

      const channel = supabase.channel('script-presence-' + scriptId, {
        config: { presence: { key: selfId } },
      });

      // (Re)start a user's 4 s editing-expiry timer and mark them an active editor.
      const refreshEditor = (uid: string) => {
        const existing = editingTimersRef.current.get(uid);
        if (existing !== undefined) clearTimeout(existing);
        editingTimersRef.current.set(
          uid,
          setTimeout(() => {
            editingTimersRef.current.delete(uid);
            setActiveEditors((prev) => {
              if (!prev.has(uid)) return prev;
              const next = new Set(prev);
              next.delete(uid);
              return next;
            });
          }, EDITING_EXPIRY_MS),
        );
        setActiveEditors((prev) => (prev.has(uid) ? prev : new Set(prev).add(uid)));
      };

      // Recompute the online set from the full presence snapshot (authoritative
      // after any event); the local user is always filtered out.
      const recompute = () => {
        const state = channel.presenceState() as Record<string, PresenceEntry[]>;
        const users: PresenceUser[] = [];
        const present = new Set<string>();
        for (const key of Object.keys(state)) {
          const entries = state[key];
          if (!entries || entries.length === 0) continue;
          const p = entries[0];
          const uid = typeof p.user_id === 'string' ? p.user_id : key;
          if (uid === selfId) continue;
          present.add(uid);
          const pmode: PresenceMode = p.mode === 'editing' ? 'editing' : 'viewing';
          users.push({
            user_id: uid,
            name: typeof p.name === 'string' ? p.name : uid,
            // Coerce, don't type-check: a focused_scene_id can arrive as a JSON
            // number (#1006), and `typeof === 'string'` would drop it to null,
            // silently breaking the per-scene grouping.
            focused_scene_id: p.focused_scene_id != null ? String(p.focused_scene_id) : null,
            mode: pmode,
          });
          if (pmode === 'editing') refreshEditor(uid);
        }
        // Drop editing timers/flags for users who have left the channel.
        editingTimersRef.current.forEach((timer, uid) => {
          if (present.has(uid)) return;
          clearTimeout(timer);
          editingTimersRef.current.delete(uid);
          setActiveEditors((prev) => {
            if (!prev.has(uid)) return prev;
            const next = new Set(prev);
            next.delete(uid);
            return next;
          });
        });
        setRawUsers(users);
      };

      // All .on() handlers are attached BEFORE .subscribe() (Realtime contract).
      channel
        .on('presence', { event: 'sync' }, () => { recompute(); })
        .on('presence', { event: 'join' }, () => { recompute(); })
        .on('presence', { event: 'leave' }, () => { recompute(); })
        .subscribe((status) => {
          if (status === 'SUBSCRIBED') setSubscribed(true);
        });

      ch = channel;
      channelRef.current = channel;
    })();

    return () => {
      cancelled = true;
      if (trailingTimerRef.current !== null) {
        clearTimeout(trailingTimerRef.current);
        trailingTimerRef.current = null;
      }
      editingTimersRef.current.forEach((timer) => clearTimeout(timer));
      editingTimersRef.current.clear();
      if (ch) supabase.removeChannel(ch);
      channelRef.current = null;
      lastTrackRef.current = 0;
      setSubscribed(false);
      setRawUsers(EMPTY);
      setActiveEditors(EMPTY_SET);
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

  // Apply the editing-expiry override: a user shows `editing` only while their
  // editing is fresh (in activeEditors); otherwise they read as `viewing`.
  const onlineUsers = useMemo<PresenceUser[]>(
    () =>
      rawUsers.map((u) =>
        activeEditors.has(u.user_id)
          ? u.mode === 'editing'
            ? u
            : { ...u, mode: 'editing' }
          : u.mode === 'editing'
            ? { ...u, mode: 'viewing' }
            : u,
      ),
    [rawUsers, activeEditors],
  );

  return { onlineUsers };
}
