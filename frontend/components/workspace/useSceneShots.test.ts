/**
 * useSceneShots — the shared scene/shot data hook behind view one
 * (EpisodeSceneBoard) and view three (EpisodeShotListTable).
 *
 * This file covers only the `onStoryboardRefresh` subscription added in
 * Task 6 review 修复轮1 (2026-08-11): the OLD editor storyboard rail's
 * `StoryboardView` subscribed to this same channel so an Agent Run Undo
 * could silently re-pull scenes/shots while mounted; deleting it in Task 6
 * orphaned the channel with nobody listening. The rest of the hook's
 * behaviour (caching, stale-while-revalidate, refreshScene/setShotsForScene)
 * is already covered indirectly through EpisodeSceneBoard.test.tsx.
 */
import { act, renderHook, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const svc = vi.hoisted(() => ({
  listScenes: vi.fn(),
  listShots: vi.fn(),
}));
vi.mock('../../editor/sceneService', () => svc);

import { useSceneShots, __clearSceneShotsCache } from './useSceneShots';
import { requestStoryboardRefresh } from '../agentActivity/shotFocusBus';
import type { SceneDoc } from '../../editor/types';

const scene = (over: Partial<SceneDoc> = {}): SceneDoc => ({
  id: '200',
  script_id: 'sc1',
  chapter_id: null,
  scene_number: null,
  heading_int_ext: 'INT',
  location_text: 'Kitchen',
  time_of_day: 'DAY',
  content_version: 1,
  sort_order: 0,
  elements: [],
  ...over,
});

beforeEach(() => {
  svc.listScenes.mockReset().mockResolvedValue([scene()]);
  svc.listShots.mockReset().mockResolvedValue([]);
  __clearSceneShotsCache();
});

afterEach(() => {
  vi.clearAllMocks();
});

describe('useSceneShots — onStoryboardRefresh subscription (Task 6 review 修复轮1)', () => {
  it('requestStoryboardRefresh() re-fetches scenes/shots while the hook is mounted', async () => {
    renderHook(() => useSceneShots('sc1'));
    await waitFor(() => expect(svc.listScenes).toHaveBeenCalledTimes(1));

    // The undo changed the scene's heading — the refetch must pick that up.
    svc.listScenes.mockResolvedValue([scene({ heading_int_ext: 'EXT' })]);

    act(() => {
      requestStoryboardRefresh();
    });

    await waitFor(() => expect(svc.listScenes).toHaveBeenCalledTimes(2));
    expect(svc.listShots).toHaveBeenCalledTimes(2);
  });

  it('applies the refreshed data into the hook\'s returned state', async () => {
    const { result } = renderHook(() => useSceneShots('sc1'));
    await waitFor(() => expect(result.current.scenes).toHaveLength(1));
    expect(result.current.scenes[0].heading_int_ext).toBe('INT');

    svc.listScenes.mockResolvedValue([scene({ heading_int_ext: 'EXT' })]);
    act(() => {
      requestStoryboardRefresh();
    });

    await waitFor(() => expect(result.current.scenes[0].heading_int_ext).toBe('EXT'));
    // No loading flash for the refresh trigger (mirrors the old
    // StoryboardView's silent `void loadAll().then(setShotsByScene)`).
    expect(result.current.loading).toBe(false);
  });

  it('unsubscribes on unmount — a later requestStoryboardRefresh() no longer triggers a refetch', async () => {
    const { unmount } = renderHook(() => useSceneShots('sc1'));
    await waitFor(() => expect(svc.listScenes).toHaveBeenCalledTimes(1));

    unmount();
    svc.listScenes.mockClear();

    act(() => {
      requestStoryboardRefresh();
    });
    // Give any stray microtask a chance to run before asserting the negative.
    await act(async () => {
      await Promise.resolve();
    });
    expect(svc.listScenes).not.toHaveBeenCalled();
  });
});
