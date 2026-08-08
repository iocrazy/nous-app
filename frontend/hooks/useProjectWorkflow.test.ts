/**
 * useProjectWorkflow — episodeId gate (B6 PR-2 task 10).
 *
 * Pins the fix for a first-paint 422: `ProjectWorkspace` resets
 * `currentEpisodeId` to `null` on every project change and only resolves it
 * once its own episodes-progress fetch settles, so this hook sees a null
 * episodeId on first render. The backend's 4 episode-aware endpoints made
 * `episode_id` REQUIRED, so a null/undefined episodeId must skip the fetch
 * entirely (not fire a request the server would reject) and only fetch once
 * a real episodeId shows up.
 */
import { describe, expect, it, vi, beforeEach } from 'vitest';
import { renderHook, waitFor } from '@testing-library/react';

const mockWorkflowService = vi.hoisted(() => ({
  fetchProjectWorkflow: vi.fn(),
}));
vi.mock('../services/workflowService', () => mockWorkflowService);

import { useProjectWorkflow } from './useProjectWorkflow';

const WORKFLOW = { has_workflow: true, current_node_id: 'n1', agents_active: 0, nodes: [] };

beforeEach(() => {
  mockWorkflowService.fetchProjectWorkflow.mockReset();
});

describe('useProjectWorkflow episodeId gate', () => {
  it('does not fetch and stays in the loading/empty state when episodeId is null', async () => {
    const { result } = renderHook(() => useProjectWorkflow('p1', null));

    // Give any stray microtask a chance to run before asserting the negative.
    await new Promise((r) => setTimeout(r, 0));

    expect(mockWorkflowService.fetchProjectWorkflow).not.toHaveBeenCalled();
    expect(result.current.workflow).toBeNull();
    expect(result.current.loading).toBe(true);
  });

  it('does not fetch and stays in the loading/empty state when episodeId is undefined', async () => {
    const { result } = renderHook(() => useProjectWorkflow('p1', undefined));

    await new Promise((r) => setTimeout(r, 0));

    expect(mockWorkflowService.fetchProjectWorkflow).not.toHaveBeenCalled();
    expect(result.current.workflow).toBeNull();
    expect(result.current.loading).toBe(true);
  });

  it('fetches once episodeId resolves to a real value', async () => {
    mockWorkflowService.fetchProjectWorkflow.mockResolvedValue(WORKFLOW);
    const { result, rerender } = renderHook(
      ({ episodeId }: { episodeId: string | null }) => useProjectWorkflow('p1', episodeId),
      { initialProps: { episodeId: null } },
    );

    expect(mockWorkflowService.fetchProjectWorkflow).not.toHaveBeenCalled();

    rerender({ episodeId: 'ep-1' });

    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(mockWorkflowService.fetchProjectWorkflow).toHaveBeenCalledWith('p1', 'ep-1');
    expect(result.current.workflow).toEqual(WORKFLOW);
  });

  it('reload() is a no-op when episodeId is null', async () => {
    const { result } = renderHook(() => useProjectWorkflow('p1', null));

    await result.current.reload();

    expect(mockWorkflowService.fetchProjectWorkflow).not.toHaveBeenCalled();
  });
});
