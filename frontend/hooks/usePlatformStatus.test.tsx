// hooks/usePlatformStatus.test.tsx
//
// The ONE request point for platform runtime state (spec 2026-09-25 §3.3).
// Pins the cache policy it inherited from the canvas catalogs: one request
// shared by every mounted picker, a refetch on window focus at most every 30
// seconds, a refetch on mount once 10 minutes old, a failed refresh keeps the
// last good answer — and until any answer lands, the status the settings
// carried shows instead of nothing.

import { act, renderHook, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { PlatformStatusResponse } from '../types/api';
import {
  ENGINE_DOWN,
  ENGINE_OK,
  baseAISettings,
  platformStatusWire,
  withPlatform,
} from '../tests/fixtures/platform';

const getPlatformStatus = vi.fn<() => Promise<PlatformStatusResponse>>();
vi.mock('../services/aiService', () => ({
  getPlatformStatus: () => getPlatformStatus(),
}));
const auth = vi.hoisted(() => ({ settings: null as unknown }));
vi.mock('../contexts/AuthContext', () => ({
  useOptionalAISettings: () => auth.settings,
}));

import { _resetPlatformStatusCache, usePlatformStatus } from './usePlatformStatus';

const SETTINGS = withPlatform(
  baseAISettings(),
  [
    { name: 'nous-qwen3-8b', status: 'idle' },
    { name: 'codex-local-image', type: 'image', is_local: true, status: 'not_probed' },
  ],
  { engine: ENGINE_OK },
);
const IDLE = platformStatusWire({ 'nous-qwen3-8b': { status: 'idle' } });
const OK = platformStatusWire({
  'nous-qwen3-8b': { status: 'ok' },
  'codex-local-image': { status: 'not_probed', local_ready: false },
  'jimeng-image': { superseded: true },
});
const T0 = new Date('2026-09-25T10:00:00Z').getTime();

beforeEach(() => {
  vi.useFakeTimers({ toFake: ['Date'] });
  vi.setSystemTime(T0);
  _resetPlatformStatusCache();
  getPlatformStatus.mockReset();
  auth.settings = SETTINGS;
});
afterEach(() => {
  vi.useRealTimers();
  _resetPlatformStatusCache();
});

function focus() {
  act(() => {
    window.dispatchEvent(new Event('focus'));
  });
}

describe('usePlatformStatus', () => {
  it('shows the status the settings carried until the live answer lands', async () => {
    getPlatformStatus.mockReturnValue(new Promise(() => {}));
    const { result } = renderHook(() => usePlatformStatus());
    expect(result.current.statusOf('nous-qwen3-8b')).toBe('idle');
    expect(result.current.engine).toEqual(ENGINE_OK);
    // Not known yet is not a verdict.
    expect(result.current.localReady('codex-local-image')).toBeNull();
    expect(result.current.superseded('codex-local-image')).toBe(false);
  });

  it('then the live answer wins', async () => {
    getPlatformStatus.mockResolvedValue(OK);
    const { result } = renderHook(() => usePlatformStatus());
    await waitFor(() => expect(result.current.statusOf('nous-qwen3-8b')).toBe('ok'));
    expect(result.current.localReady('codex-local-image')).toBe(false);
    expect(result.current.superseded('jimeng-image')).toBe(true);
  });

  it('every mounted picker shares ONE request', async () => {
    getPlatformStatus.mockResolvedValue(OK);
    const hooks = [1, 2, 3, 4, 5].map(() => renderHook(() => usePlatformStatus()));
    await waitFor(() => expect(hooks[4].result.current.statusOf('nous-qwen3-8b')).toBe('ok'));
    for (const h of hooks) expect(h.result.current.statusOf('nous-qwen3-8b')).toBe('ok');
    expect(getPlatformStatus).toHaveBeenCalledTimes(1);
  });

  it('a fresh answer is reused by the next mount — no refetch', async () => {
    getPlatformStatus.mockResolvedValue(IDLE);
    const first = renderHook(() => usePlatformStatus());
    await waitFor(() => expect(getPlatformStatus).toHaveBeenCalledTimes(1));
    first.unmount();
    vi.setSystemTime(T0 + 9 * 60_000);
    renderHook(() => usePlatformStatus());
    expect(getPlatformStatus).toHaveBeenCalledTimes(1);
  });

  it('an answer older than 10 minutes is refetched on the next mount', async () => {
    getPlatformStatus.mockResolvedValueOnce(IDLE).mockResolvedValueOnce(OK);
    const first = renderHook(() => usePlatformStatus());
    await waitFor(() => expect(getPlatformStatus).toHaveBeenCalledTimes(1));
    first.unmount();
    vi.setSystemTime(T0 + 10 * 60_000 + 1);
    const again = renderHook(() => usePlatformStatus());
    await waitFor(() => expect(again.result.current.statusOf('nous-qwen3-8b')).toBe('ok'));
    expect(getPlatformStatus).toHaveBeenCalledTimes(2);
  });

  it('window focus refetches and updates mounted pickers', async () => {
    getPlatformStatus.mockResolvedValueOnce(IDLE).mockResolvedValueOnce(OK);
    const { result } = renderHook(() => usePlatformStatus());
    await waitFor(() => expect(getPlatformStatus).toHaveBeenCalledTimes(1));
    vi.setSystemTime(T0 + 30_001);
    focus();
    await waitFor(() => expect(result.current.statusOf('nous-qwen3-8b')).toBe('ok'));
    expect(getPlatformStatus).toHaveBeenCalledTimes(2);
  });

  it('focus within 30 seconds of the last answer does not refetch', async () => {
    getPlatformStatus.mockResolvedValue(IDLE);
    renderHook(() => usePlatformStatus());
    await waitFor(() => expect(getPlatformStatus).toHaveBeenCalledTimes(1));
    vi.setSystemTime(T0 + 29_000);
    focus();
    expect(getPlatformStatus).toHaveBeenCalledTimes(1);
  });

  it('a failed refresh keeps the last good answer (logged)', async () => {
    const spy = vi.spyOn(console, 'error').mockImplementation(() => {});
    getPlatformStatus.mockResolvedValueOnce(OK).mockRejectedValueOnce(new Error('boom'));
    const { result } = renderHook(() => usePlatformStatus());
    await waitFor(() => expect(result.current.statusOf('nous-qwen3-8b')).toBe('ok'));
    vi.setSystemTime(T0 + 60_000);
    focus();
    await waitFor(() => expect(getPlatformStatus).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(spy).toHaveBeenCalled());
    expect(result.current.statusOf('nous-qwen3-8b')).toBe('ok');
    spy.mockRestore();
  });

  it('a failed first request leaves the settings fallback in place', async () => {
    const spy = vi.spyOn(console, 'error').mockImplementation(() => {});
    getPlatformStatus.mockRejectedValue(new Error('boom'));
    const { result } = renderHook(() => usePlatformStatus());
    await waitFor(() => expect(spy).toHaveBeenCalled());
    expect(result.current.statusOf('nous-qwen3-8b')).toBe('idle');
    spy.mockRestore();
  });

  it('reads the engine state from the live answer once it lands', async () => {
    getPlatformStatus.mockResolvedValue(platformStatusWire({}, ENGINE_DOWN));
    const { result } = renderHook(() => usePlatformStatus());
    await waitFor(() => expect(result.current.engine).toEqual(ENGINE_DOWN));
  });

  it('uses settings passed in over the ones AuthContext loaded', () => {
    getPlatformStatus.mockReturnValue(new Promise(() => {}));
    auth.settings = null;
    const { result } = renderHook(() => usePlatformStatus(SETTINGS));
    expect(result.current.statusOf('nous-qwen3-8b')).toBe('idle');
  });
});
