/**
 * EpisodeStoryboardPage (IA redesign Task 2, extended by shot-nodes-on-
 * canvas Task 5) — the standalone storyboard module page. Three views —
 * Storyboard | Canvas | Shot List — plus the read-only script probe gate
 * (never provisions on a passive render).
 *
 * Task 5 (2026-08-11): the Canvas tab is no longer the materials-canvas
 * library (`WorkspaceCanvas`) — it now embeds the episode's real storyboard
 * canvas via `StoryboardCanvasEmbed`, mocked out below (it's heavy: pulls in
 * the whole canvas-core React Flow tree; its own resolve/render behaviour is
 * covered by `StoryboardCanvasEmbed.test.tsx`). This file focuses on the
 * three focus entry points this page owns (shot card click / the
 * `focusShotId` prop / `shotFocusBus`) and the `openShotInListBus` consumer.
 */
import { render, screen, waitFor, fireEvent, act } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { cleanup } from '@testing-library/react';

vi.mock('react-i18next', () => ({
  // Second arg is either an i18next default-value string OR an interpolation
  // options object (e.g. EpisodeSceneBoard's `shotBadge` call) — only treat
  // it as a rendered default when it's actually a string, else fall back to
  // the raw key (mirrors EpisodeSceneBoard.test.tsx's own `t: (k) => k` mock).
  useTranslation: () => ({
    t: (k: string, dflt?: string | Record<string, unknown>) =>
      typeof dflt === 'string' ? dflt : k,
  }),
}));

const mockSceneService = vi.hoisted(() => ({
  listScenes: vi.fn().mockResolvedValue([]),
  listShots: vi.fn().mockResolvedValue([]),
  autoStoryboard: vi.fn(),
}));
vi.mock('../../editor/sceneService', () => mockSceneService);

// StoryboardCanvasEmbed (Task 5) pulls in the whole canvas-core React Flow
// tree — mocked to a thin marker exposing the props this page threads down,
// so this file can assert on ITS OWN wiring without paying for a real
// mount. See StoryboardCanvasEmbed.test.tsx for its own resolve/render
// behaviour.
vi.mock('../../features/canvas-core/ui/StoryboardCanvasEmbed', () => ({
  StoryboardCanvasEmbed: (props: {
    episodeId: string;
    teamId?: string;
    focusShotId?: string | null;
    onFocusHandled?: () => void;
  }) => (
    <div
      data-testid="storyboard-canvas-embed-mock"
      data-episode-id={props.episodeId}
      data-team-id={props.teamId ?? ''}
      data-focus-shot-id={props.focusShotId ?? ''}
    >
      {/* Simulates the real embed's "focus settled" callback so tests can
          exercise the reset→re-fire path without a real React Flow mount. */}
      <button type="button" data-testid="mock-focus-handled" onClick={() => props.onFocusHandled?.()} />
    </div>
  ),
}));

// Stable hoisted spy (not a fresh vi.fn() per render) so the failure-toast
// test below can assert on it across re-renders.
const addToast = vi.hoisted(() => vi.fn());
vi.mock('../Toast', () => ({
  useToast: () => ({ addToast }),
  useOptionalToast: () => ({ addToast }),
}));

// relativeTime pulls in the real i18n module chain (HTTP backend init) —
// stub it so the test env stays hermetic (mirrors WorkspaceCanvas.test.tsx).
vi.mock('../../utils/relativeTime', () => ({
  formatRelativeTime: () => '3d ago',
}));

import { EpisodeStoryboardPage, __clearScriptProbeCache } from './EpisodeStoryboardPage';
import { __clearSceneShotsCache } from './useSceneShots';
import { requestShotFocus } from '../agentActivity/shotFocusBus';
import { requestOpenShotInList } from '../../features/canvas-core/smart/openShotInListBus';

const ep = { episode_id: '324362669885098', title: 'EP1', scene_count: 7,
  shots_done: 0, shots_total: 6, renders_count: 0, status: 'in_progress' } as any;
const base = {
  projectId: 'p1', teamId: 't1', episode: ep, initialView: null,
  onViewChange: vi.fn(),
  findExistingScript: vi.fn().mockResolvedValue('sc1'),
  provisionScript: vi.fn().mockResolvedValue('sc1'),
};

beforeEach(() => {
  mockSceneService.listScenes.mockReset().mockResolvedValue([]);
  mockSceneService.listShots.mockReset().mockResolvedValue([]);
  base.onViewChange.mockClear();
  base.findExistingScript.mockReset().mockResolvedValue('sc1');
  base.provisionScript.mockReset().mockResolvedValue('sc1');
  addToast.mockClear();
});

afterEach(() => {
  cleanup();
  // Module-level caches (fix 2, storyboard-page-polish) otherwise leak
  // between test cases — every test in this file reuses the same
  // `ep.episode_id`, so a prior test's probe would shadow the next test's
  // fresh mock without this.
  __clearScriptProbeCache();
  __clearSceneShotsCache();
});

describe('EpisodeStoryboardPage', () => {
  it('renders left-aligned view tabs with board active by default', async () => {
    render(<EpisodeStoryboardPage {...base} />);
    const tabs = await screen.findByTestId('episode-view-tabs');
    expect(tabs.querySelector('[data-view="storyboard"][aria-selected="true"]')).toBeTruthy();
  });
  it('shows Start Storyboard empty state when no script, without provisioning', async () => {
    render(<EpisodeStoryboardPage {...base} findExistingScript={vi.fn().mockResolvedValue(null)} />);
    expect(await screen.findByTestId('episode-surface-no-script')).toBeTruthy();
    expect(base.provisionScript).not.toHaveBeenCalled();
  });
  // Review fix round 1 (Important #1): the "Start Storyboard" CTA's failure
  // path used to only console.error — the UI silently fell back to the empty
  // state with no user-visible signal (CLAUDE.md「触发路径必须类型化失败回显」).
  it('shows an error toast and falls back to the empty state when Start Storyboard provisioning fails', async () => {
    const provisionScript = vi.fn().mockRejectedValue(new Error('boom'));
    render(
      <EpisodeStoryboardPage
        {...base}
        findExistingScript={vi.fn().mockResolvedValue(null)}
        provisionScript={provisionScript}
      />,
    );

    fireEvent.click(await screen.findByTestId('episode-surface-start-storyboard'));

    await waitFor(() => expect(addToast).toHaveBeenCalledWith('common.error', 'error'));
    expect(provisionScript).toHaveBeenCalledTimes(1);
    // Falls back to the same "no script" empty state, not stuck on the
    // loading spinner — the writer can retry the CTA.
    expect(await screen.findByTestId('episode-surface-no-script')).toBeInTheDocument();
    expect(screen.getByTestId('episode-surface-start-storyboard')).toBeInTheDocument();
  });

  // Review fix round 1 (Important #2, component half): a manual tab click
  // must go through `onViewChange` — the URL-writing path ProjectWorkspace
  // uses to clear the one-shot `?shot=` trigger (see ProjectWorkspace.test.tsx
  // for the URL-clearing assertion itself, since that logic lives there).
  it('a manual tab click calls onViewChange with the newly selected view', async () => {
    render(<EpisodeStoryboardPage {...base} />);
    const tabs = await screen.findByTestId('episode-view-tabs');

    fireEvent.click(tabs.querySelector('[data-view="shotlist"]')!);

    expect(base.onViewChange).toHaveBeenCalledWith('shotlist');
  });

  // Shot-nodes-on-canvas Task 5 (2026-08-11, supersedes Task 3 修复轮2; the
  // editor deep-link this superseded was fully retired in Task 6): a scene
  // board shot-card click switches THIS page to its own Canvas tab and
  // focuses the shot there. `onViewChange` IS called — this is a genuine tab
  // switch the URL should reflect.
  it('a shot-card click switches to the Canvas tab and focuses the shot there', async () => {
    mockSceneService.listScenes.mockResolvedValue([
      { id: '200', script_id: 'sc1', chapter_id: null, scene_number: '1',
        heading_int_ext: 'INT', location_text: 'Kitchen', time_of_day: 'DAY',
        content_version: 1, sort_order: 0, elements: [] },
    ]);
    mockSceneService.listShots.mockResolvedValue([
      { id: '9007199254740997', scene_id: '200', shot_number: 1, shot_type: 'WIDE',
        camera_angle: 'EYE', camera_movement: 'STATIC', focal_length: '35mm',
        lighting: null, description: '', image_url: null, thumbnail_url: null,
        video_url: null, status: 'empty', sort_order: 1000 },
    ]);

    render(<EpisodeStoryboardPage {...base} />);
    const shotCard = await screen.findByTestId('shot-card-9007199254740997');
    fireEvent.click(shotCard);

    expect(base.onViewChange).toHaveBeenCalledWith('canvas');
    const embed = await screen.findByTestId('storyboard-canvas-embed-mock');
    expect(embed.getAttribute('data-focus-shot-id')).toBe('9007199254740997');
  });

  // Scene card's Open (view one) — Task 6 retired the old editor deep-link
  // this used to reach through `onOpenScene`/ProjectWorkspace; it's now
  // handled entirely locally by this page: scroll the same column into view.
  it("a scene card's Open button scrolls its own column into view (local to view one)", async () => {
    mockSceneService.listScenes.mockResolvedValue([
      { id: '200', script_id: 'sc1', chapter_id: null, scene_number: '1',
        heading_int_ext: 'INT', location_text: 'Kitchen', time_of_day: 'DAY',
        content_version: 1, sort_order: 0, elements: [] },
    ]);
    const scrollIntoView = vi.fn();
    Element.prototype.scrollIntoView = scrollIntoView;

    render(<EpisodeStoryboardPage {...base} />);
    await screen.findByTestId('scene-column-200');
    fireEvent.click(screen.getByTestId('ep-scene-open-200'));

    expect(scrollIntoView).toHaveBeenCalled();
  });

  describe('canvas tab (Task 5 — embeds the episode storyboard canvas)', () => {
    it('renders StoryboardCanvasEmbed for the current episode/team once the Canvas tab is active', async () => {
      render(<EpisodeStoryboardPage {...base} />);
      const tabs = await screen.findByTestId('episode-view-tabs');
      fireEvent.click(tabs.querySelector('[data-view="canvas"]')!);

      const embed = await screen.findByTestId('storyboard-canvas-embed-mock');
      expect(embed.getAttribute('data-episode-id')).toBe(ep.episode_id);
      expect(embed.getAttribute('data-team-id')).toBe('t1');
    });

    // Entry point ②: the `focusShotId` prop (ProjectWorkspace's URL
    // `?view=canvas&shot=` resolution) merges into local state, switches the
    // tab even if the writer wasn't already on Canvas, and is reported back
    // as consumed exactly once.
    it('a focusShotId prop switches to Canvas and reports consumption via onFocusShotIdConsumed', async () => {
      const onFocusShotIdConsumed = vi.fn();
      render(
        <EpisodeStoryboardPage
          {...base}
          focusShotId="url-shot-1"
          onFocusShotIdConsumed={onFocusShotIdConsumed}
        />,
      );

      const embed = await screen.findByTestId('storyboard-canvas-embed-mock');
      expect(embed.getAttribute('data-focus-shot-id')).toBe('url-shot-1');
      expect(onFocusShotIdConsumed).toHaveBeenCalledTimes(1);
      // Purely local — this page never writes the URL for the prop-driven
      // entry point (the URL already says view=canvas, see the component's
      // own doc comment).
      expect(base.onViewChange).not.toHaveBeenCalled();
    });

    // Entry point ③: shotFocusBus — a second subscriber alongside
    // EditorShell's existing one (T6 retires that one; both coexist here).
    it('a shotFocusBus request switches to Canvas and focuses the shot', async () => {
      render(<EpisodeStoryboardPage {...base} />);
      await screen.findByTestId('episode-view-tabs');

      act(() => requestShotFocus('bus-shot-1'));

      const embed = await screen.findByTestId('storyboard-canvas-embed-mock');
      expect(embed.getAttribute('data-focus-shot-id')).toBe('bus-shot-1');
      expect(base.onViewChange).toHaveBeenCalledWith('canvas');
    });

    it('the embed clearing its own focus (onFocusHandled) resets this page state so a repeat request re-fires', async () => {
      render(<EpisodeStoryboardPage {...base} />);
      act(() => requestShotFocus('bus-shot-1'));
      expect(screen.getByTestId('storyboard-canvas-embed-mock').getAttribute('data-focus-shot-id')).toBe(
        'bus-shot-1',
      );

      fireEvent.click(screen.getByTestId('mock-focus-handled'));
      expect(screen.getByTestId('storyboard-canvas-embed-mock').getAttribute('data-focus-shot-id')).toBe('');

      // Same shot id requested again — must re-fire (null → value is a real
      // change), not silently no-op because the prop "already had that value".
      act(() => requestShotFocus('bus-shot-1'));
      expect(screen.getByTestId('storyboard-canvas-embed-mock').getAttribute('data-focus-shot-id')).toBe(
        'bus-shot-1',
      );
    });
  });

  // Task 4's node-menu "Delete in shot list" (`openShotInListBus`) — jumps
  // back to view one and scrolls the matching shot card into view.
  describe('openShotInListBus consumer (Task 4 menu → this page)', () => {
    it('switches to the Storyboard view and scrolls the matching shot card into view', async () => {
      mockSceneService.listScenes.mockResolvedValue([
        { id: '200', script_id: 'sc1', chapter_id: null, scene_number: '1',
          heading_int_ext: 'INT', location_text: 'Kitchen', time_of_day: 'DAY',
          content_version: 1, sort_order: 0, elements: [] },
      ]);
      mockSceneService.listShots.mockResolvedValue([
        { id: '9007199254740997', scene_id: '200', shot_number: 1, shot_type: 'WIDE',
          camera_angle: 'EYE', camera_movement: 'STATIC', focal_length: '35mm',
          lighting: null, description: '', image_url: null, thumbnail_url: null,
          video_url: null, status: 'empty', sort_order: 1000 },
      ]);
      const scrollIntoView = vi.fn();
      // jsdom doesn't implement scrollIntoView at all.
      Element.prototype.scrollIntoView = scrollIntoView;

      render(<EpisodeStoryboardPage {...base} />);
      const tabs = await screen.findByTestId('episode-view-tabs');
      fireEvent.click(tabs.querySelector('[data-view="canvas"]')!);
      await screen.findByTestId('storyboard-canvas-embed-mock');

      requestOpenShotInList('9007199254740997');

      expect(await screen.findByTestId('episode-view-storyboard')).toBeInTheDocument();
      await waitFor(() => expect(scrollIntoView).toHaveBeenCalled());
    });
  });

  // Fix 2 (storyboard-page-polish, 2026-08-10 用户反馈): re-entering the same
  // episode's storyboard module used to repeat the script probe → Loading
  // gate → EpisodeSceneBoard's own scenes/shots fetch, spinning every single
  // time. A module-level cache (scriptProbeCache + useSceneShots'
  // sceneShotsCache) should render the last-known state immediately instead.
  describe('per-episode cache (fix 2)', () => {
    it('re-entering the same episode renders the board immediately from cache, without the Loading gate', async () => {
      mockSceneService.listScenes.mockResolvedValue([
        { id: '200', script_id: 'sc1', chapter_id: null, scene_number: '1',
          heading_int_ext: 'INT', location_text: 'Kitchen', time_of_day: 'DAY',
          content_version: 1, sort_order: 0, elements: [] },
      ]);
      const { unmount } = render(<EpisodeStoryboardPage {...base} />);
      await screen.findByTestId('scene-column-200');
      unmount();

      // Second entry: the probe never resolves within this test — a cache
      // miss would leave the page stuck on the Loading gate forever.
      const pendingProbe = new Promise<string | null>(() => {});
      render(
        <EpisodeStoryboardPage
          {...base}
          findExistingScript={vi.fn().mockReturnValue(pendingProbe)}
        />,
      );

      // No `await`/`waitFor` — this must already be in the DOM synchronously
      // from the cached scriptId + cached scenes, proving the Loading gate
      // never rendered on this mount.
      expect(screen.getByTestId('scene-column-200')).toBeInTheDocument();
      expect(screen.queryByTestId('episode-surface-no-script')).toBeNull();
    });

    it('the background probe silently corrects the rendered script when it changed since the cached visit', async () => {
      const { unmount } = render(
        <EpisodeStoryboardPage {...base} findExistingScript={vi.fn().mockResolvedValue('sc1')} />,
      );
      await screen.findByTestId('episode-view-storyboard');
      unmount();
      mockSceneService.listScenes.mockClear();

      // Re-entry: cache says 'sc1', but this mount's probe resolves a
      // DIFFERENT script id (e.g. created/replaced elsewhere) — the cached
      // render must self-correct once the background probe settles.
      render(
        <EpisodeStoryboardPage {...base} findExistingScript={vi.fn().mockResolvedValue('sc2')} />,
      );
      // Renders the cached scriptId's board synchronously first...
      expect(screen.getByTestId('episode-view-storyboard')).toBeInTheDocument();
      // ...then the background refresh swaps EpisodeSceneBoard onto the
      // corrected scriptId (visible as a fresh scenes fetch for 'sc2').
      await waitFor(() => expect(mockSceneService.listScenes).toHaveBeenCalledWith('sc2'));
    });

    it('a no-op background probe (unchanged script) does not remount the board', async () => {
      mockSceneService.listScenes.mockResolvedValue([
        { id: '200', script_id: 'sc1', chapter_id: null, scene_number: '1',
          heading_int_ext: 'INT', location_text: 'Kitchen', time_of_day: 'DAY',
          content_version: 1, sort_order: 0, elements: [] },
      ]);
      const { unmount } = render(<EpisodeStoryboardPage {...base} />);
      await screen.findByTestId('scene-column-200');
      unmount();
      mockSceneService.listScenes.mockClear();

      render(<EpisodeStoryboardPage {...base} />); // same 'sc1' result as before
      expect(screen.getByTestId('scene-column-200')).toBeInTheDocument();
      // The background probe still fires (re-validates)...
      await waitFor(() => expect(base.findExistingScript).toHaveBeenCalled());
      // ...but since the result didn't change, the board is still showing
      // (no flash to the Loading gate / no-script state along the way).
      expect(screen.getByTestId('scene-column-200')).toBeInTheDocument();
    });

    it('Start Storyboard success updates the cache so re-entry shows the fresh script immediately', async () => {
      const provisionScript = vi.fn().mockResolvedValue('newsc');
      const { unmount } = render(
        <EpisodeStoryboardPage
          {...base}
          findExistingScript={vi.fn().mockResolvedValue(null)}
          provisionScript={provisionScript}
        />,
      );
      fireEvent.click(await screen.findByTestId('episode-surface-start-storyboard'));
      await waitFor(() => expect(screen.getByTestId('episode-view-storyboard')).toBeInTheDocument());
      expect(provisionScript).toHaveBeenCalledTimes(1);
      unmount();

      // Re-entry: even a probe that never resolves must not shadow the
      // cache the successful provision just wrote (the 'missing' state from
      // before the click must not leak back in).
      render(
        <EpisodeStoryboardPage
          {...base}
          findExistingScript={vi.fn().mockReturnValue(new Promise<string | null>(() => {}))}
        />,
      );
      expect(screen.getByTestId('episode-view-storyboard')).toBeInTheDocument();
      expect(screen.queryByTestId('episode-surface-no-script')).toBeNull();
    });
  });
});
