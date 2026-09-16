import { renderHook } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';
import { useClearPlaybackOnSignOut } from './useClearPlaybackOnSignOut';

describe('useClearPlaybackOnSignOut', () => {
  it('clears the account that just signed out', () => {
    const clear = vi.fn();
    const { rerender } = renderHook(
      ({ id }: { id: string | null }) => useClearPlaybackOnSignOut(id, clear),
      { initialProps: { id: 'alice' as string | null } },
    );
    expect(clear).not.toHaveBeenCalled();

    rerender({ id: null });
    expect(clear).toHaveBeenCalledExactlyOnceWith('alice');
  });

  it('does nothing when the app starts signed out', () => {
    const clear = vi.fn();
    renderHook(() => useClearPlaybackOnSignOut(null, clear));
    expect(clear).not.toHaveBeenCalled();
  });

  it('does nothing on sign-IN', () => {
    const clear = vi.fn();
    const { rerender } = renderHook(
      ({ id }: { id: string | null }) => useClearPlaybackOnSignOut(id, clear),
      { initialProps: { id: null as string | null } },
    );
    rerender({ id: 'alice' });
    expect(clear).not.toHaveBeenCalled();
  });

  it('does not wipe anything on a straight account switch', () => {
    // B's positions are not A's to delete, and A's namespace is unreachable
    // from B anyway — so an A → B switch clears nothing.
    const clear = vi.fn();
    const { rerender } = renderHook(
      ({ id }: { id: string | null }) => useClearPlaybackOnSignOut(id, clear),
      { initialProps: { id: 'alice' as string | null } },
    );
    rerender({ id: 'bob' });
    expect(clear).not.toHaveBeenCalled();
  });

  it('clears again on a second sign-out in the same session', () => {
    const clear = vi.fn();
    const { rerender } = renderHook(
      ({ id }: { id: string | null }) => useClearPlaybackOnSignOut(id, clear),
      { initialProps: { id: 'alice' as string | null } },
    );
    rerender({ id: null });
    rerender({ id: 'bob' });
    rerender({ id: null });
    expect(clear).toHaveBeenCalledTimes(2);
    expect(clear).toHaveBeenNthCalledWith(1, 'alice');
    expect(clear).toHaveBeenNthCalledWith(2, 'bob');
  });
});
