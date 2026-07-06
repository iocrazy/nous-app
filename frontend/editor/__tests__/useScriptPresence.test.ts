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

// ---------------------------------------------------------------------------
// Helpers — a fresh fake Supabase channel + client per test.
// ---------------------------------------------------------------------------
function buildFakes() {
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

  const fakeSupabase = {
    channel: vi.fn(() => fakeChannel),
    removeChannel: vi.fn(),
  };

  const setPresenceSnapshot = (snap: Record<string, unknown>) => {
    presenceSnapshot = snap;
  };
  const subscribeNow = () => {
    subscribeCb?.('SUBSCRIBED');
  };

  return { capturedHandlers, fakeChannel, fakeSupabase, setPresenceSnapshot, subscribeNow };
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

  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2026-01-01T00:00:00Z'));
    ({ capturedHandlers, fakeChannel, fakeSupabase, setPresenceSnapshot, subscribeNow } =
      buildFakes());
    vi.mocked(getSupabaseClient).mockReturnValue(
      fakeSupabase as ReturnType<typeof getSupabaseClient>,
    );
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  // -------------------------------------------------------------------------
  // 1. Null-guard — flag-off callers pass null, nothing subscribes.
  // -------------------------------------------------------------------------
  describe('null-guard', () => {
    it('scriptId=null → no channel created, empty state', () => {
      const { result } = renderHook(() => useScriptPresence(null, ME));
      expect(fakeSupabase.channel).not.toHaveBeenCalled();
      expect(result.current.onlineUsers).toEqual([]);
    });

    it('me=null → no channel created, empty state', () => {
      const { result } = renderHook(() => useScriptPresence('s1', null));
      expect(fakeSupabase.channel).not.toHaveBeenCalled();
      expect(result.current.onlineUsers).toEqual([]);
    });
  });

  // -------------------------------------------------------------------------
  // 2. Channel identity + track on SUBSCRIBED
  // -------------------------------------------------------------------------
  it('opens channel `script-presence-<id>` keyed by userId', () => {
    renderHook(() => useScriptPresence('s1', ME));
    expect(fakeSupabase.channel).toHaveBeenCalledWith('script-presence-s1', {
      config: { presence: { key: 'me-123' } },
    });
  });

  it('attaches all presence handlers before subscribe', () => {
    renderHook(() => useScriptPresence('s1', ME));
    // on() is chainable and must be fully wired before subscribe() runs.
    const onOrder = (fakeChannel.on as ReturnType<typeof vi.fn>).mock.invocationCallOrder;
    const subscribeOrder = (fakeChannel.subscribe as ReturnType<typeof vi.fn>).mock
      .invocationCallOrder[0];
    expect(onOrder.length).toBe(3);
    expect(Math.max(...onOrder)).toBeLessThan(subscribeOrder);
  });

  it('tracks self payload on SUBSCRIBED (viewing when clean)', () => {
    renderHook(() => useScriptPresence('s1', ME));
    act(() => subscribeNow());
    expect(fakeChannel.track).toHaveBeenCalledWith({
      user_id: 'me-123',
      name: 'Me',
      focused_scene_id: null,
      mode: 'viewing',
    });
  });

  it('reports mode=editing when isDirty is true', () => {
    renderHook(() => useScriptPresence('s1', { ...ME, isDirty: true, focusedSceneId: 's7' }));
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
  it('recomputes onlineUsers from presenceState and excludes self', () => {
    setPresenceSnapshot({
      'me-123': [{ user_id: 'me-123', name: 'Me', focused_scene_id: 's1', mode: 'viewing' }],
      'other-456': [
        { user_id: 'other-456', name: 'Other', focused_scene_id: 's2', mode: 'editing' },
      ],
    });
    const { result } = renderHook(() => useScriptPresence('s1', ME));
    act(() => subscribeNow());
    act(() => capturedHandlers['presence:sync']());

    expect(result.current.onlineUsers).toEqual([
      { user_id: 'other-456', name: 'Other', focused_scene_id: 's2', mode: 'editing' },
    ]);
  });

  it('join and leave both recompute the full snapshot', () => {
    setPresenceSnapshot({ 'u-a': [{ user_id: 'u-a', name: 'A' }] });
    const { result } = renderHook(() => useScriptPresence('s1', ME));
    act(() => subscribeNow());

    act(() => capturedHandlers['presence:join']());
    expect(result.current.onlineUsers.map((u) => u.user_id)).toEqual(['u-a']);

    setPresenceSnapshot({});
    act(() => capturedHandlers['presence:leave']());
    expect(result.current.onlineUsers).toEqual([]);
  });

  it('defaults name to user_id and focused_scene_id to null when absent', () => {
    setPresenceSnapshot({ 'u-x': [{ user_id: 'u-x' }] });
    const { result } = renderHook(() => useScriptPresence('s1', ME));
    act(() => subscribeNow());
    act(() => capturedHandlers['presence:sync']());
    expect(result.current.onlineUsers).toEqual([
      { user_id: 'u-x', name: 'u-x', focused_scene_id: null, mode: 'viewing' },
    ]);
  });

  // -------------------------------------------------------------------------
  // 4. Re-track on focus change + throttle
  // -------------------------------------------------------------------------
  it('re-tracks when focusedSceneId changes after the throttle window', () => {
    const { rerender } = renderHook(
      ({ me }) => useScriptPresence('s1', me),
      { initialProps: { me: ME } },
    );
    act(() => subscribeNow());
    expect(fakeChannel.track).toHaveBeenCalledTimes(1);

    act(() => vi.advanceTimersByTime(2001));
    act(() => rerender({ me: { ...ME, focusedSceneId: 's5' } }));

    expect(fakeChannel.track).toHaveBeenCalledTimes(2);
    expect(fakeChannel.track).toHaveBeenLastCalledWith(
      expect.objectContaining({ focused_scene_id: 's5' }),
    );
  });

  it('throttles rapid focus changes to a single trailing track with the latest value', () => {
    const { rerender } = renderHook(
      ({ me }) => useScriptPresence('s1', me),
      { initialProps: { me: ME } },
    );
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
  it('removes the channel on unmount', () => {
    const { unmount } = renderHook(() => useScriptPresence('s1', ME));
    act(() => subscribeNow());
    act(() => unmount());
    expect(fakeSupabase.removeChannel).toHaveBeenCalledTimes(1);
    expect(fakeSupabase.removeChannel).toHaveBeenCalledWith(fakeChannel);
  });

  it('clears a pending trailing track timer on unmount (no track after unmount)', () => {
    const { rerender, unmount } = renderHook(
      ({ me }) => useScriptPresence('s1', me),
      { initialProps: { me: ME } },
    );
    act(() => subscribeNow());
    expect(fakeChannel.track).toHaveBeenCalledTimes(1);

    // Schedule a trailing track, then unmount before it fires.
    act(() => rerender({ me: { ...ME, focusedSceneId: 'a' } }));
    act(() => unmount());

    act(() => vi.advanceTimersByTime(5000));
    expect(fakeChannel.track).toHaveBeenCalledTimes(1); // trailing timer was cleared
  });
});
