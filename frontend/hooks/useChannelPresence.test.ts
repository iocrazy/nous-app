import { vi, describe, it, expect, beforeEach, afterEach } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import { useChannelPresence } from './useChannelPresence';
import { getSupabaseClient } from '../supabaseClient';

// ---------------------------------------------------------------------------
// Module mock — must be hoisted before imports are resolved.
// Factory returns a stable vi.fn() so we can swap the return value per-test.
// ---------------------------------------------------------------------------
vi.mock('../supabaseClient', () => ({
  getSupabaseClient: vi.fn(),
}));

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------
const ME = { user_id: 'me-123', name: 'Me' };
const OTHER = { user_id: 'other-456', name: 'Other' };

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/**
 * Builds a fresh fake Supabase channel + client for each test.
 *
 * Shape:
 *   fakeChannel.on(type, opts, handler) — chainable; captures handlers by
 *     key `"${type}:${opts.event}"` into `capturedHandlers`.
 *   fakeChannel.subscribe(cb)           — calls cb('SUBSCRIBED') synchronously.
 *   fakeChannel.track(payload)          — spy, resolves immediately.
 *   fakeChannel.presenceState()         — returns the shared `presenceStateRef`.
 *   fakeChannel.send(msg)               — spy.
 *   fakeSupabase.channel(name, opts)    — returns fakeChannel.
 *   fakeSupabase.removeChannel(ch)      — spy.
 */
function buildFakes() {
  const capturedHandlers: Record<string, (...args: unknown[]) => void> = {};
  // Mutable reference so tests can update presenceState before invoking
  // the sync handler.  The closure inside presenceState reads this variable.
  let presenceSnapshot: Record<string, unknown> = {};

  const fakeChannel: Record<string, unknown> = {
    on: vi.fn(
      (type: string, opts: { event: string }, handler: (...args: unknown[]) => void) => {
        capturedHandlers[`${type}:${opts.event}`] = handler;
        return fakeChannel; // chainable
      },
    ),
    subscribe: vi.fn((cb: (status: string) => void) => {
      // Invoke synchronously so handlers are captured before renderHook returns.
      cb('SUBSCRIBED');
      return fakeChannel;
    }),
    track: vi.fn().mockResolvedValue('ok'),
    presenceState: vi.fn(() => presenceSnapshot),
    send: vi.fn(),
  };

  const fakeSupabase = {
    channel: vi.fn(() => fakeChannel),
    removeChannel: vi.fn(),
  };

  /** Update the presence snapshot that presenceState() will return. */
  function setPresenceSnapshot(snap: Record<string, unknown>) {
    presenceSnapshot = snap;
  }

  return { capturedHandlers, fakeChannel, fakeSupabase, setPresenceSnapshot };
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe('useChannelPresence', () => {
  let capturedHandlers: ReturnType<typeof buildFakes>['capturedHandlers'];
  let fakeChannel: ReturnType<typeof buildFakes>['fakeChannel'];
  let fakeSupabase: ReturnType<typeof buildFakes>['fakeSupabase'];
  let setPresenceSnapshot: ReturnType<typeof buildFakes>['setPresenceSnapshot'];

  beforeEach(() => {
    vi.useFakeTimers();
    // Pin a known starting time so Date.now()-based throttle is deterministic.
    vi.setSystemTime(new Date('2026-01-01T00:00:00Z'));

    ({ capturedHandlers, fakeChannel, fakeSupabase, setPresenceSnapshot } = buildFakes());

    // Wire the mock so getSupabaseClient() returns our fake.
    vi.mocked(getSupabaseClient).mockReturnValue(fakeSupabase as ReturnType<typeof getSupabaseClient>);
  });

  afterEach(() => {
    vi.useRealTimers();
    // vi.restoreAllMocks() is called globally in tests/setup.ts afterEach.
  });

  // -------------------------------------------------------------------------
  // 1. Null-guard
  // -------------------------------------------------------------------------
  describe('null-guard', () => {
    it('channelId=null → empty state, no channel created', () => {
      const { result } = renderHook(() => useChannelPresence(null, ME));

      expect(fakeSupabase.channel).not.toHaveBeenCalled();
      expect(result.current.onlineUserIds).toEqual([]);
      expect(result.current.typingUsers).toEqual([]);
      expect(typeof result.current.sendTyping).toBe('function');
    });

    it('me=null → empty state, no channel created', () => {
      const { result } = renderHook(() => useChannelPresence('c1', null));

      expect(fakeSupabase.channel).not.toHaveBeenCalled();
      expect(result.current.onlineUserIds).toEqual([]);
      expect(result.current.typingUsers).toEqual([]);
    });
  });

  // -------------------------------------------------------------------------
  // 2. Typing receive + 4 s self-expiry
  // -------------------------------------------------------------------------
  describe('typing receive + 4 s self-expiry', () => {
    it('adds other user to typingUsers on broadcast typing event', () => {
      const { result } = renderHook(() => useChannelPresence('c1', ME));

      act(() => {
        capturedHandlers['broadcast:typing']({
          payload: { user_id: OTHER.user_id, name: OTHER.name },
        });
      });

      expect(result.current.typingUsers).toContainEqual({
        user_id: OTHER.user_id,
        name: OTHER.name,
      });
    });

    it('removes user from typingUsers after 4000 ms', () => {
      const { result } = renderHook(() => useChannelPresence('c1', ME));

      act(() => {
        capturedHandlers['broadcast:typing']({
          payload: { user_id: OTHER.user_id, name: OTHER.name },
        });
      });

      expect(result.current.typingUsers).toHaveLength(1);

      act(() => {
        vi.advanceTimersByTime(4000);
      });

      expect(result.current.typingUsers).toEqual([]);
    });

    it('user is still present just before 4000 ms expiry', () => {
      const { result } = renderHook(() => useChannelPresence('c1', ME));

      act(() => {
        capturedHandlers['broadcast:typing']({
          payload: { user_id: OTHER.user_id, name: OTHER.name },
        });
      });

      act(() => {
        vi.advanceTimersByTime(3999);
      });

      expect(result.current.typingUsers).toContainEqual({
        user_id: OTHER.user_id,
        name: OTHER.name,
      });
    });
  });

  // -------------------------------------------------------------------------
  // 3. Own typing ignored
  // -------------------------------------------------------------------------
  it('ignores broadcast typing event from self (user_id === me.user_id)', () => {
    const { result } = renderHook(() => useChannelPresence('c1', ME));

    act(() => {
      capturedHandlers['broadcast:typing']({
        payload: { user_id: ME.user_id, name: ME.name },
      });
    });

    expect(result.current.typingUsers).toEqual([]);
  });

  // -------------------------------------------------------------------------
  // 4. Refresh resets timer
  // -------------------------------------------------------------------------
  it('second typing event resets the 4 s expiry timer', () => {
    const { result } = renderHook(() => useChannelPresence('c1', ME));

    // t = 0: first event → 4 s timer starts (expires at t = 4000)
    act(() => {
      capturedHandlers['broadcast:typing']({
        payload: { user_id: OTHER.user_id, name: OTHER.name },
      });
    });

    // t = 3000: still present
    act(() => {
      vi.advanceTimersByTime(3000);
    });
    expect(result.current.typingUsers).toContainEqual({
      user_id: OTHER.user_id,
      name: OTHER.name,
    });

    // t = 3000: second event → clears old timer, starts fresh 4 s (expires at t = 7000)
    act(() => {
      capturedHandlers['broadcast:typing']({
        payload: { user_id: OTHER.user_id, name: OTHER.name },
      });
    });

    // t = 6999: should still be present (4000 ms after t=3000 is t=7000)
    act(() => {
      vi.advanceTimersByTime(3999);
    });
    expect(result.current.typingUsers).toContainEqual({
      user_id: OTHER.user_id,
      name: OTHER.name,
    });

    // t = 7000: timer fires → removed
    act(() => {
      vi.advanceTimersByTime(1);
    });
    expect(result.current.typingUsers).toEqual([]);
  });

  // -------------------------------------------------------------------------
  // 5. sendTyping throttle
  // -------------------------------------------------------------------------
  describe('sendTyping throttle', () => {
    it('two calls within 2000 ms → channel.send called only once', () => {
      const { result } = renderHook(() => useChannelPresence('c1', ME));

      act(() => {
        result.current.sendTyping(); // first call — passes throttle
      });
      act(() => {
        result.current.sendTyping(); // immediate second call — throttled
      });

      expect(fakeChannel.send).toHaveBeenCalledTimes(1);
    });

    it('call after > 2000 ms passes through throttle again', () => {
      const { result } = renderHook(() => useChannelPresence('c1', ME));

      act(() => {
        result.current.sendTyping(); // first call passes
      });
      expect(fakeChannel.send).toHaveBeenCalledTimes(1);

      // Advance past the 2 s throttle window.
      act(() => {
        vi.advanceTimersByTime(2001);
      });

      act(() => {
        result.current.sendTyping(); // should pass through
      });
      expect(fakeChannel.send).toHaveBeenCalledTimes(2);
    });

    it('send payload shape is correct', () => {
      const { result } = renderHook(() => useChannelPresence('c1', ME));

      act(() => {
        result.current.sendTyping();
      });

      expect(fakeChannel.send).toHaveBeenCalledWith({
        type: 'broadcast',
        event: 'typing',
        payload: { user_id: ME.user_id, name: ME.name },
      });
    });
  });

  // -------------------------------------------------------------------------
  // 6. Presence sync
  // -------------------------------------------------------------------------
  it('presence:sync handler populates onlineUserIds from presenceState()', () => {
    setPresenceSnapshot({ 'user-a': [], 'user-b': [] });

    const { result } = renderHook(() => useChannelPresence('c1', ME));

    act(() => {
      capturedHandlers['presence:sync']();
    });

    expect(result.current.onlineUserIds).toContain('user-a');
    expect(result.current.onlineUserIds).toContain('user-b');
    expect(result.current.onlineUserIds).toHaveLength(2);
  });

  it('presence:join also refreshes onlineUserIds', () => {
    setPresenceSnapshot({ 'user-a': [] });
    const { result } = renderHook(() => useChannelPresence('c1', ME));

    act(() => {
      capturedHandlers['presence:join']();
    });

    expect(result.current.onlineUserIds).toContain('user-a');
  });

  it('presence:leave also refreshes onlineUserIds', () => {
    setPresenceSnapshot({});
    const { result } = renderHook(() => useChannelPresence('c1', ME));

    // Seed a user first via sync.
    setPresenceSnapshot({ 'user-a': [] });
    act(() => {
      capturedHandlers['presence:sync']();
    });
    expect(result.current.onlineUserIds).toContain('user-a');

    // Simulate leave — presence snapshot shrinks.
    setPresenceSnapshot({});
    act(() => {
      capturedHandlers['presence:leave']();
    });
    expect(result.current.onlineUserIds).toEqual([]);
  });

  // -------------------------------------------------------------------------
  // 7. Cleanup
  // -------------------------------------------------------------------------
  describe('cleanup on unmount', () => {
    it('calls supabase.removeChannel with the active channel', () => {
      const { unmount } = renderHook(() => useChannelPresence('c1', ME));

      act(() => {
        unmount();
      });

      expect(fakeSupabase.removeChannel).toHaveBeenCalledTimes(1);
      expect(fakeSupabase.removeChannel).toHaveBeenCalledWith(fakeChannel);
    });

    it('clears pending typing timers on unmount (no setState-after-unmount)', () => {
      const { result, unmount } = renderHook(() => useChannelPresence('c1', ME));

      // Create a pending typing expiry timer.
      act(() => {
        capturedHandlers['broadcast:typing']({
          payload: { user_id: OTHER.user_id, name: OTHER.name },
        });
      });
      expect(result.current.typingUsers).toHaveLength(1);

      // Unmount — cleanup should clear the timer.
      act(() => {
        unmount();
      });

      expect(fakeSupabase.removeChannel).toHaveBeenCalled();

      // Advancing timers after unmount must NOT trigger setState
      // (would cause an act warning / React error if timers weren't cleared).
      act(() => {
        vi.advanceTimersByTime(5000);
      });
      // If we reach here without errors or act-warnings, the timers were cleared.
    });

    it('resets channelRef on cleanup so sendTyping becomes a no-op after unmount', () => {
      const { result, unmount } = renderHook(() => useChannelPresence('c1', ME));

      // Verify sendTyping works before unmount.
      act(() => {
        result.current.sendTyping();
      });
      expect(fakeChannel.send).toHaveBeenCalledTimes(1);

      act(() => {
        unmount();
      });

      // After unmount channelRef.current is null, so send should not be called again.
      // Advance timers past throttle window so the ref-null guard is the only gate.
      act(() => {
        vi.advanceTimersByTime(3000);
      });
      act(() => {
        result.current.sendTyping();
      });
      // send count stays at 1 — channel is gone.
      expect(fakeChannel.send).toHaveBeenCalledTimes(1);
    });
  });

  // -------------------------------------------------------------------------
  // Channel wiring: subscribe tracks current user on SUBSCRIBED
  // -------------------------------------------------------------------------
  it('calls channel.track with me on SUBSCRIBED', () => {
    renderHook(() => useChannelPresence('c1', ME));

    // subscribe was called with an async cb; our fake invokes it synchronously.
    // track() is called inside the async cb after the first await, so we need
    // to flush the microtask queue.  Use a small trick: re-check after a flush.
    return Promise.resolve().then(() => {
      expect(fakeChannel.track).toHaveBeenCalledWith({
        user_id: ME.user_id,
        name: ME.name,
      });
    });
  });
});
