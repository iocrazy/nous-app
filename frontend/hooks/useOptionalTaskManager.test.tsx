/**
 * useOptionalTaskManager must degrade, not crash.
 *
 * The floating chat mounts in TWO hosts: AppLayout (wrapped in
 * TaskManagerProvider) and the fullscreen Script/Storyboard editor routes
 * (NOT wrapped). Anything in the chat that wants task progress therefore
 * has to read the context optionally — `useTaskManager()` throws there.
 */

import React from 'react';
import { renderHook } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

vi.mock('../supabaseClient', () => ({
  getSupabaseClient: () => null,
  getSupabaseAccessToken: vi.fn().mockResolvedValue(null),
}));
vi.mock('../contexts/AuthContext', () => ({
  useAuth: () => ({ currentUserId: null }),
}));

import {
  TaskManagerContext,
  useTaskManager,
  type TaskManagerContextType,
} from '../contexts/TaskManagerContext';
import { useOptionalTaskManager } from './useOptionalTaskManager';

describe('useOptionalTaskManager', () => {
  it('returns null outside a TaskManagerProvider instead of throwing', () => {
    const { result } = renderHook(() => useOptionalTaskManager());

    expect(result.current).toBeNull();
  });

  it('is the only safe variant — useTaskManager still throws there', () => {
    // Falsifiability guard: proves the test above is not passing merely
    // because a provider is silently present in the render tree.
    expect(() => renderHook(() => useTaskManager())).toThrow(
      /TaskManagerProvider/,
    );
  });

  it('returns the context value when a provider is present', () => {
    const value = { tasks: [], totalActive: 3 } as unknown as TaskManagerContextType;
    const wrapper = ({ children }: { children: React.ReactNode }) => (
      <TaskManagerContext.Provider value={value}>{children}</TaskManagerContext.Provider>
    );

    const { result } = renderHook(() => useOptionalTaskManager(), { wrapper });

    expect(result.current).toBe(value);
    expect(result.current?.totalActive).toBe(3);
  });
});
