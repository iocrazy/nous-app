import { afterEach, describe, expect, it, vi } from 'vitest';
import { renderHook, waitFor } from '@testing-library/react';

const fetchModulesStatus = vi.fn();
vi.mock('../services/modulesService', () => ({
  fetchModulesStatus: (...a: unknown[]) => fetchModulesStatus(...a),
}));

import { useModuleStatus } from './useModuleStatus';

describe('useModuleStatus', () => {
  afterEach(() => vi.resetAllMocks());

  it('returns server state once loaded', async () => {
    fetchModulesStatus.mockResolvedValue({
      shares: { enabled: false, visible: false },
    });
    const { result } = renderHook(() => useModuleStatus('shares'));
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.enabled).toBe(false);
    expect(result.current.visible).toBe(false);
  });

  it('fails open for rollout modules on fetch error', async () => {
    fetchModulesStatus.mockResolvedValue(null);
    const { result } = renderHook(() => useModuleStatus('todolist'));
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.enabled).toBe(true);
    expect(result.current.visible).toBe(true);
  });

  it('fails closed for distribution on fetch error', async () => {
    fetchModulesStatus.mockResolvedValue(null);
    const { result } = renderHook(() => useModuleStatus('distribution'));
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.visible).toBe(false);
  });
});
