/**
 * useSceneShots — the shared scene/shot data hook behind view one
 * (EpisodeSceneBoard) and view three (EpisodeShotListTable).
 *
 * This file covers the `onStoryboardRefresh` subscription added in Task 6
 * review 修复轮1 (2026-08-11): the OLD editor storyboard rail's
 * `StoryboardView` subscribed to this same channel so an Agent Run Undo
 * could silently re-pull scenes/shots while mounted; deleting it in Task 6
 * orphaned the channel with nobody listening. The rest of the hook's
 * behaviour (caching, stale-while-revalidate, refreshScene/setShotsForScene)
 * is already covered indirectly through EpisodeSceneBoard.test.tsx.
 *
 * Also covers the 修复轮2 regression this fix's own mount/unmount guard
 * introduced (`mountedRef`) — see the `reactStrictMode: true` describe block
 * below.
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

// Task 6 review 修复轮2 (2026-08-11): `mountedRef`, added in 修复轮1 to guard
// `fetchAll` against writing state after unmount, was a `useRef(true)` + ONE
// cleanup-only effect (`useEffect(() => () => { mountedRef.current = false
// }, [])`). React.StrictMode's dev-only double-invoke (the whole app is
// wrapped in it, `index.tsx`) runs: mount → cleanup (flips false) → remount
// — an empty effect BODY never flips it back, so `mountedRef.current` stays
// false forever after the very first cycle. Every `fetchAll` after that
// silently early-returns before `setLoading(false)`, so view one/three spin
// forever in `npm run dev` (production doesn't double-invoke, so this was
// invisible outside manual dev-server testing). Fixed by resetting
// `mountedRef.current = true` in the effect BODY too. This block pins that
// fix with the actual repro shape (StrictMode double-invoked effects).
//
// ⚠️ Reproducing this specifically requires RTL's OWN `reactStrictMode: true`
// render/renderHook option — manually nesting `<React.StrictMode>` yourself
// via the `wrapper` option does NOT reliably trigger the double-invoke in
// this RTL version (empirically verified: render double-invokes but effects
// don't, so a test written that way would pass even against the buggy code
// and give false confidence). `reactStrictMode: true` makes StrictMode the
// literal root element `@testing-library/react` passes to `root.render()`,
// which is the path that actually gets the effects-double-invoke treatment.
describe('useSceneShots under React.StrictMode (Task 6 review 修复轮2 regression)', () => {
  it('scenes load and loading resolves to false (does not spin forever)', async () => {
    const { result } = renderHook(() => useSceneShots('sc1'), {
      reactStrictMode: true,
    } as Parameters<typeof renderHook>[1]);

    await waitFor(() => expect(result.current.scenes).toHaveLength(1));
    expect(result.current.loading).toBe(false);
  });

  it('a later requestStoryboardRefresh() still refetches (mountedRef did not get stuck false)', async () => {
    const { result } = renderHook(() => useSceneShots('sc1'), {
      reactStrictMode: true,
    } as Parameters<typeof renderHook>[1]);
    await waitFor(() => expect(result.current.scenes).toHaveLength(1));

    svc.listScenes.mockResolvedValue([scene({ heading_int_ext: 'EXT' })]);
    act(() => {
      requestStoryboardRefresh();
    });

    await waitFor(() => expect(result.current.scenes[0].heading_int_ext).toBe('EXT'));
    expect(result.current.loading).toBe(false);
  });
});
