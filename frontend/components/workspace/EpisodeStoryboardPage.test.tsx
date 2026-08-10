/**
 * EpisodeStoryboardPage (IA redesign Task 2) — the standalone storyboard
 * module page. Extracted from ProjectWorkspace's old "surface panel" block
 * (see ProjectWorkspace.test.tsx's now-removed surface-panel tests, moved
 * here): the three-view segmented control (Storyboard | Canvas | Shot List),
 * the read-only script probe gate (never provisions on a passive render),
 * and the `?shot=` deep-link that jumps straight to Canvas and asks the
 * shotFocusBus to reveal the shot.
 */
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { cleanup } from '@testing-library/react';
import * as bus from '../agentActivity/shotFocusBus';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (k: string, dflt?: string) => dflt ?? k }),
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

import { EpisodeStoryboardPage } from './EpisodeStoryboardPage';

const ep = { episode_id: '324362669885098', title: 'EP1', scene_count: 7,
  shots_done: 0, shots_total: 6, renders_count: 0, status: 'in_progress' } as any;
const base = {
  projectId: 'p1', teamId: 't1', episode: ep, initialView: null, focusShotId: null,
  onViewChange: vi.fn(), onOpenScene: vi.fn(),
  findExistingScript: vi.fn().mockResolvedValue('sc1'),
  provisionScript: vi.fn().mockResolvedValue('sc1'),
};

beforeEach(() => {
  mockSceneService.listScenes.mockReset().mockResolvedValue([]);
  mockSceneService.listShots.mockReset().mockResolvedValue([]);
  base.onViewChange.mockClear();
  base.onOpenScene.mockClear();
  base.findExistingScript.mockReset().mockResolvedValue('sc1');
  base.provisionScript.mockReset().mockResolvedValue('sc1');
  addToast.mockClear();
});

afterEach(() => cleanup());

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
  it('focusShotId switches to canvas and fires shotFocusBus', async () => {
    const spy = vi.spyOn(bus, 'requestShotFocus');
    render(<EpisodeStoryboardPage {...base} initialView="canvas" focusShotId="9007199254740997" />);
    await waitFor(() => expect(spy).toHaveBeenCalledWith('9007199254740997'));
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
});
