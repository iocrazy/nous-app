import { vi, describe, it, expect, beforeEach, afterEach } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import { useScriptPresence, type ScriptPresenceSelf } from '../collab/useScriptPresence';
import { getSupabaseClient } from '../../supabaseClient';

// ---------------------------------------------------------------------------
// Module mock — hoisted before imports resolve. Factory returns a stable
// vi.fn() so the return value can be swapped per-test.
// ---------------------------------------------------------------------------
vi.mock('../../supabaseClient', () => ({
  getSupabaseClient: vi.fn(),
}));

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------
const ME: ScriptPresenceSelf = {
  userId: 'me-123',
  name: 'Me',
  focusedSceneId: null,
  isDirty: false,
};

// The channel is opened only AFTER `supabase.auth.getSession()` resolves (auth
// applied to the realtime socket first), so flush the microtask queue before
// asserting on / firing the channel. Microtasks aren't faked by fake timers.
async function flush() {
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
  });
}

// ---------------------------------------------------------------------------
// Helpers — a fresh fake Supabase channel + client per test.
// ---------------------------------------------------------------------------
function buildFakes(config: { session?: { access_token: string } | null; defer?: boolean } = {}) {
  const session = 'session' in config ? config.session : { access_token: 'tok' };
  const capturedHandlers: Record<string, (...args: unknown[]) => void> = {};
  let subscribeCb: ((status: string) => void) | null = null;
  let presenceSnapshot: Record<string, unknown> = {};

  const fakeChannel: Record<string, unknown> = {
    on: vi.fn(
      (type: string, opts: { event: string }, handler: (...args: unknown[]) => void) => {
        capturedHandlers[`${type}:${opts.event}`] = handler;
        return fakeChannel; // chainable
      },
    ),
    subscribe: vi.fn((cb: (status: string) => void) => {
      // Capture but DON'T fire yet — some tests fire it manually to control
      // ordering; most fire it immediately via subscribeNow().
      subscribeCb = cb;
      return fakeChannel;
    }),
    track: vi.fn().mockResolvedValue('ok'),
    presenceState: vi.fn(() => presenceSnapshot),
  };

  let resolveSession: (() => void) | null = null;
  const sessionResult = { data: { session } };
  const getSession = vi.fn(() =>
    config.defer
      ? new Promise((res) => {
          resolveSession = () => res(sessionResult);
        })
      : Promise.resolve(sessionResult),
  );

  const fakeSupabase = {
    auth: { getSession },
    realtime: { setAuth: vi.fn() },
    channel: vi.fn(() => fakeChannel),
    removeChannel: vi.fn(),
  };

  const setPresenceSnapshot = (snap: Record<string, unknown>) => {
    presenceSnapshot = snap;
  };
  const subscribeNow = () => {
    subscribeCb?.('SUBSCRIBED');
  };

  return {
    capturedHandlers,
    fakeChannel,
    fakeSupabase,
    setPresenceSnapshot,
    subscribeNow,
    resolveSession: () => resolveSession?.(),
  };
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------
describe('useScriptPresence', () => {
  let capturedHandlers: ReturnType<typeof buildFakes>['capturedHandlers'];
  let fakeChannel: ReturnType<typeof buildFakes>['fakeChannel'];
  let fakeSupabase: ReturnType<typeof buildFakes>['fakeSupabase'];
  let setPresenceSnapshot: ReturnType<typeof buildFakes>['setPresenceSnapshot'];
  let subscribeNow: ReturnType<typeof buildFakes>['subscribeNow'];
  let resolveSession: ReturnType<typeof buildFakes>['resolveSession'];

  const wire = (fakes: ReturnType<typeof buildFakes>) => {
    ({ capturedHandlers, fakeChannel, fakeSupabase, setPresenceSnapshot, subscribeNow, resolveSession } =
      fakes);
    vi.mocked(getSupabaseClient).mockReturnValue(
      fakeSupabase as unknown as ReturnType<typeof getSupabaseClient>,
    );
  };

  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2026-01-01T00:00:00Z'));
    wire(buildFakes());
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  // -------------------------------------------------------------------------
  // 1. Null-guard — flag-off callers pass null, nothing subscribes.
  // -------------------------------------------------------------------------
  describe('null-guard', () => {
    it('scriptId=null → no channel created, empty state', async () => {
      const { result } = renderHook(() => useScriptPresence(null, ME));
      await flush();
      expect(fakeSupabase.channel).not.toHaveBeenCalled();
      expect(result.current.onlineUsers).toEqual([]);
    });

    it('me=null → no channel created, empty state', async () => {
      const { result } = renderHook(() => useScriptPresence('s1', null));
      await flush();
      expect(fakeSupabase.channel).not.toHaveBeenCalled();
      expect(result.current.onlineUsers).toEqual([]);
    });
  });

  // -------------------------------------------------------------------------
  // 2. Auth-before-join + channel identity + track on SUBSCRIBED
  // -------------------------------------------------------------------------
  it('applies realtime auth BEFORE opening the presence channel', async () => {
    renderHook(() => useScriptPresence('s1', ME));
    await flush();
    expect(fakeSupabase.realtime.setAuth).toHaveBeenCalledWith('tok');
    const setAuthOrder = fakeSupabase.realtime.setAuth.mock.invocationCallOrder[0];
    const channelOrder = fakeSupabase.channel.mock.invocationCallOrder[0];
    expect(setAuthOrder).toBeLessThan(channelOrder);
  });

  it('opens no channel when there is no session', async () => {
    wire(buildFakes({ session: null }));
    renderHook(() => useScriptPresence('s1', ME));
    await flush();
    expect(fakeSupabase.realtime.setAuth).not.toHaveBeenCalled();
    expect(fakeSupabase.channel).not.toHaveBeenCalled();
  });

  it('does not subscribe when unmounted before getSession resolves (cancel guard)', async () => {
    wire(buildFakes({ defer: true }));
    const { unmount } = renderHook(() => useScriptPresence('s1', ME));
    act(() => unmount());
    resolveSession();
    await flush();
    expect(fakeSupabase.channel).not.toHaveBeenCalled();
    expect(fakeSupabase.removeChannel).not.toHaveBeenCalled();
  });

  it('opens channel `script-presence-<id>` keyed by userId', async () => {
    renderHook(() => useScriptPresence('s1', ME));
    await flush();
    expect(fakeSupabase.channel).toHaveBeenCalledWith('script-presence-s1', {
      config: { presence: { key: 'me-123' } },
    });
  });

  it('attaches all presence handlers before subscribe', async () => {
    renderHook(() => useScriptPresence('s1', ME));
    await flush();
    // on() is chainable and must be fully wired before subscribe() runs.
    const onOrder = (fakeChannel.on as ReturnType<typeof vi.fn>).mock.invocationCallOrder;
    const subscribeOrder = (fakeChannel.subscribe as ReturnType<typeof vi.fn>).mock
      .invocationCallOrder[0];
    expect(onOrder.length).toBe(3);
    expect(Math.max(...onOrder)).toBeLessThan(subscribeOrder);
  });

  it('tracks self payload on SUBSCRIBED (viewing when clean)', async () => {
    renderHook(() => useScriptPresence('s1', ME));
    await flush();
    act(() => subscribeNow());
    expect(fakeChannel.track).toHaveBeenCalledWith({
      user_id: 'me-123',
      name: 'Me',
      focused_scene_id: null,
      mode: 'viewing',
    });
  });

  it('reports mode=editing when isDirty is true', async () => {
    renderHook(() => useScriptPresence('s1', { ...ME, isDirty: true, focusedSceneId: 's7' }));
    await flush();
    act(() => subscribeNow());
    expect(fakeChannel.track).toHaveBeenCalledWith({
      user_id: 'me-123',
      name: 'Me',
      focused_scene_id: 's7',
      mode: 'editing',
    });
  });

  // -------------------------------------------------------------------------
  // 3. Snapshot recompute + self filter
  // -------------------------------------------------------------------------
  it('recomputes onlineUsers from presenceState and excludes self', async () => {
    setPresenceSnapshot({
      'me-123': [{ user_id: 'me-123', name: 'Me', focused_scene_id: 's1', mode: 'viewing' }],
      'other-456': [
        { user_id: 'other-456', name: 'Other', focused_scene_id: 's2', mode: 'editing' },
      ],
    });
    const { result } = renderHook(() => useScriptPresence('s1', ME));
    await flush();
    act(() => subscribeNow());
    act(() => capturedHandlers['presence:sync']());

    expect(result.current.onlineUsers).toEqual([
      { user_id: 'other-456', name: 'Other', focused_scene_id: 's2', mode: 'editing' },
    ]);
  });

  it('join and leave both recompute the full snapshot', async () => {
    setPresenceSnapshot({ 'u-a': [{ user_id: 'u-a', name: 'A' }] });
    const { result } = renderHook(() => useScriptPresence('s1', ME));
    await flush();
    act(() => subscribeNow());

    act(() => capturedHandlers['presence:join']());
    expect(result.current.onlineUsers.map((u) => u.user_id)).toEqual(['u-a']);

    setPresenceSnapshot({});
    act(() => capturedHandlers['presence:leave']());
    expect(result.current.onlineUsers).toEqual([]);
  });

  it('defaults name to user_id and focused_scene_id to null when absent', async () => {
    setPresenceSnapshot({ 'u-x': [{ user_id: 'u-x' }] });
    const { result } = renderHook(() => useScriptPresence('s1', ME));
    await flush();
    act(() => subscribeNow());
    act(() => capturedHandlers['presence:sync']());
    expect(result.current.onlineUsers).toEqual([
      { user_id: 'u-x', name: 'u-x', focused_scene_id: null, mode: 'viewing' },
    ]);
  });

  // -------------------------------------------------------------------------
  // 4. Re-track on focus change + throttle
  // -------------------------------------------------------------------------
  it('re-tracks when focusedSceneId changes after the throttle window', async () => {
    const { rerender } = renderHook(
      ({ me }) => useScriptPresence('s1', me),
      { initialProps: { me: ME } },
    );
    await flush();
    act(() => subscribeNow());
    expect(fakeChannel.track).toHaveBeenCalledTimes(1);

    act(() => vi.advanceTimersByTime(2001));
    act(() => rerender({ me: { ...ME, focusedSceneId: 's5' } }));

    expect(fakeChannel.track).toHaveBeenCalledTimes(2);
    expect(fakeChannel.track).toHaveBeenLastCalledWith(
      expect.objectContaining({ focused_scene_id: 's5' }),
    );
  });

  it('throttles rapid focus changes to a single trailing track with the latest value', async () => {
    const { rerender } = renderHook(
      ({ me }) => useScriptPresence('s1', me),
      { initialProps: { me: ME } },
    );
    await flush();
    act(() => subscribeNow());
    expect(fakeChannel.track).toHaveBeenCalledTimes(1); // leading, on subscribe

    // Two changes inside the 2 s window → one trailing track scheduled.
    act(() => rerender({ me: { ...ME, focusedSceneId: 'a' } }));
    act(() => rerender({ me: { ...ME, focusedSceneId: 'b' } }));
    expect(fakeChannel.track).toHaveBeenCalledTimes(1); // still throttled

    act(() => vi.advanceTimersByTime(2000));
    expect(fakeChannel.track).toHaveBeenCalledTimes(2);
    expect(fakeChannel.track).toHaveBeenLastCalledWith(
      expect.objectContaining({ focused_scene_id: 'b' }),
    );
  });

  // -------------------------------------------------------------------------
  // 5. Cleanup on unmount
  // -------------------------------------------------------------------------
  it('removes the channel on unmount', async () => {
    const { unmount } = renderHook(() => useScriptPresence('s1', ME));
    await flush();
    act(() => subscribeNow());
    act(() => unmount());
    expect(fakeSupabase.removeChannel).toHaveBeenCalledTimes(1);
    expect(fakeSupabase.removeChannel).toHaveBeenCalledWith(fakeChannel);
  });

  it('clears a pending trailing track timer on unmount (no track after unmount)', async () => {
    const { rerender, unmount } = renderHook(
      ({ me }) => useScriptPresence('s1', me),
      { initialProps: { me: ME } },
    );
    await flush();
    act(() => subscribeNow());
    expect(fakeChannel.track).toHaveBeenCalledTimes(1);

    // Schedule a trailing track, then unmount before it fires.
    act(() => rerender({ me: { ...ME, focusedSceneId: 'a' } }));
    act(() => unmount());

    act(() => vi.advanceTimersByTime(5000));
    expect(fakeChannel.track).toHaveBeenCalledTimes(1); // trailing timer was cleared
  });

  // -------------------------------------------------------------------------
  // 8. Editing-mode 4 s expiry (C3)
  // -------------------------------------------------------------------------
  describe('editing-mode expiry', () => {
    const editing = (over: Record<string, unknown> = {}) => ({
      'other-456': [
        { user_id: 'other-456', name: 'Other', focused_scene_id: 's2', mode: 'editing', ...over },
      ],
    });
    const viewing = () => ({
      'other-456': [
        { user_id: 'other-456', name: 'Other', focused_scene_id: 's2', mode: 'viewing' },
      ],
    });

    it('shows editing while fresh, downgrades to viewing after 4000 ms', async () => {
      setPresenceSnapshot(editing());
      const { result } = renderHook(() => useScriptPresence('s1', ME));
      await flush();
      act(() => subscribeNow());
      act(() => capturedHandlers['presence:sync']());
      expect(result.current.onlineUsers[0].mode).toBe('editing');

      act(() => vi.advanceTimersByTime(4000));
      expect(result.current.onlineUsers[0].mode).toBe('viewing');
    });

    it('a fresh editing track within the window keeps it editing (sticky refresh)', async () => {
      setPresenceSnapshot(editing());
      const { result } = renderHook(() => useScriptPresence('s1', ME));
      await flush();
      act(() => subscribeNow());
      act(() => capturedHandlers['presence:sync']());

      act(() => vi.advanceTimersByTime(3000));
      act(() => capturedHandlers['presence:sync']()); // refresh — timer resets
      act(() => vi.advanceTimersByTime(3000)); // 6000 total, but only 3000 since refresh
      expect(result.current.onlineUsers[0].mode).toBe('editing');

      act(() => vi.advanceTimersByTime(1000)); // now 4000 past the refresh
      expect(result.current.onlineUsers[0].mode).toBe('viewing');
    });

    it('a transient viewing track does not clear a still-fresh editing badge', async () => {
      setPresenceSnapshot(editing());
      const { result } = renderHook(() => useScriptPresence('s1', ME));
      await flush();
      act(() => subscribeNow());
      act(() => capturedHandlers['presence:sync']());

      // Their save flaps to saved → a viewing track arrives 1 s later.
      act(() => vi.advanceTimersByTime(1000));
      setPresenceSnapshot(viewing());
      act(() => capturedHandlers['presence:sync']());
      // Still within the 4 s editing window → stays editing (no flicker).
      expect(result.current.onlineUsers[0].mode).toBe('editing');
    });

    it('drops the editing timer when the user leaves', async () => {
      setPresenceSnapshot(editing());
      const { result } = renderHook(() => useScriptPresence('s1', ME));
      await flush();
      act(() => subscribeNow());
      act(() => capturedHandlers['presence:sync']());
      expect(result.current.onlineUsers).toHaveLength(1);

      setPresenceSnapshot({});
      act(() => capturedHandlers['presence:leave']());
      expect(result.current.onlineUsers).toEqual([]);
      // No lingering timer fires after they left.
      act(() => vi.advanceTimersByTime(5000));
      expect(result.current.onlineUsers).toEqual([]);
    });
  });
});
