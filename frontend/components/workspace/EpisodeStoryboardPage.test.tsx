/**
 * EpisodeStoryboardPage (IA redesign Task 2) — the standalone storyboard
 * module page. Extracted from ProjectWorkspace's old "surface panel" block
 * (see ProjectWorkspace.test.tsx's now-removed surface-panel tests, moved
 * here): the three-view segmented control (Storyboard | Canvas | Shot List)
 * and the read-only script probe gate (never provisions on a passive
 * render). The shot-card-click → editor deep-link orchestration (Task 3
 * 修复轮2, 2026-08-10 用户拍板) lives in ProjectWorkspace, not here — this
 * page only forwards the click via `onOpenShotInEditor`; see
 * ProjectWorkspace.test.tsx for the bus/timer-level assertions.
 */
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
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

vi.mock('../../features/canvas-core/services/canvasService', () => ({
  listCanvases: vi.fn().mockResolvedValue([]),
  createCanvas: vi.fn(),
  deleteCanvas: vi.fn(),
}));

const navigate = vi.fn();
vi.mock('react-router-dom', () => ({
  useNavigate: () => navigate,
  // CanvasCardMenu (rendered by the real WorkspaceCanvas module on the
  // 'canvas' view) reads team/project scope for its create-issue payload.
  useParams: () => ({}),
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

const ep = { episode_id: '324362669885098', title: 'EP1', scene_count: 7,
  shots_done: 0, shots_total: 6, renders_count: 0, status: 'in_progress' } as any;
const base = {
  projectId: 'p1', teamId: 't1', episode: ep, initialView: null,
  onViewChange: vi.fn(), onOpenScene: vi.fn(), onOpenShotInEditor: vi.fn(),
  findExistingScript: vi.fn().mockResolvedValue('sc1'),
  provisionScript: vi.fn().mockResolvedValue('sc1'),
};

beforeEach(() => {
  mockSceneService.listScenes.mockReset().mockResolvedValue([]);
  mockSceneService.listShots.mockReset().mockResolvedValue([]);
  base.onViewChange.mockClear();
  base.onOpenScene.mockClear();
  base.onOpenShotInEditor.mockClear();
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

  // Task 3 修复轮2 (2026-08-10 用户拍板): a scene board shot-card click must
  // forward straight to `onOpenShotInEditor(shotId, sceneId)` — NOT
  // `onViewChange` (that would delete the URL's one-shot `shot` trigger) and
  // NOT a page-local view-state change (this page's own Canvas tab is a
  // materials canvas with no shot nodes; the editor deep-link + bus-retry
  // orchestration now live entirely in ProjectWorkspace, see its test file
  // for that half). This page's only job is to pass the (shotId, sceneId)
  // pair through unchanged.
  it('a shot-card click forwards (shotId, sceneId) to onOpenShotInEditor, not onViewChange', async () => {
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

    expect(base.onOpenShotInEditor).toHaveBeenCalledWith('9007199254740997', '200');
    expect(base.onViewChange).not.toHaveBeenCalled();
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
