/**
 * EpisodeStoryboardPage (IA redesign Task 2) — the standalone storyboard
 * module page. Extracted from ProjectWorkspace's old "surface panel" block
 * (see ProjectWorkspace.test.tsx's now-removed surface-panel tests, moved
 * here): the three-view segmented control (Storyboard | Canvas | Shot List),
 * the read-only script probe gate (never provisions on a passive render),
 * and the `?shot=` deep-link that jumps straight to Canvas and asks the
 * shotFocusBus to reveal the shot.
 */
import { render, screen, waitFor } from '@testing-library/react';
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

vi.mock('../Toast', () => ({
  useToast: () => ({ addToast: vi.fn() }),
  useOptionalToast: () => ({ addToast: vi.fn() }),
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
});
