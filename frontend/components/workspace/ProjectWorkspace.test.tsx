/**
 * ProjectWorkspace (PR-10b Wave 1) — the workspace shell smoke suite.
 *
 * Pins: sidebar groups + current-episode block render from the episodes
 * progress feed; the ⇄ switcher popover swaps the current episode (and
 * persists the choice to localStorage); non-overview sidebar clicks switch
 * to the shared placeholder; the Script/Storyboard children resolve the
 * current episode's script and navigate, falling back to the Episodes
 * module when none exists.
 */
import { render, screen, fireEvent, waitFor, cleanup } from '@testing-library/react';
import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest';

import { ProjectWorkspace } from './ProjectWorkspace';
import type { EpisodeProgress, Project } from '../../types';

const navigate = vi.fn();
vi.mock('react-router-dom', async (importOriginal) => {
  const actual = await importOriginal<Record<string, unknown>>();
  return {
    ...actual,
    useNavigate: () => navigate,
  };
});

const addToast = vi.fn();
vi.mock('../Toast', () => ({
  useToast: () => ({ addToast }),
}));

// relativeTime pulls in the real i18n instance via formatDate — stub it so
// this suite doesn't need initReactI18next (mirrors StageWorkbench.test.tsx).
// Pulled in transitively via the Overview module (default activeModule).
vi.mock('../../utils/relativeTime', () => ({
  formatRelativeTime: () => '2h ago',
}));

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, opts?: Record<string, unknown>) => {
      if (key === 'projects.workbench.advanceTo') return `Advance to ${opts?.stage}`;
      if (key.startsWith('projects.workspace.modules.')) return key.split('.').pop()!;
      if (key.startsWith('projects.workspace.episodeStatus.')) return key.split('.').pop()!;
      return key;
    },
  }),
}));

const mockProjectsService = vi.hoisted(() => ({
  fetchStageCatalog: vi.fn(),
  fetchCurrentStage: vi.fn(),
  setCurrentStage: vi.fn(),
  fetchStageSuggestion: vi.fn(),
  generateMissingFrames: vi.fn(),
  fetchEpisodesProgress: vi.fn(),
}));
vi.mock('../../services/projectsService', () => mockProjectsService);

const mockScriptService = vi.hoisted(() => ({
  fetchScriptProjects: vi.fn(),
}));
vi.mock('../../services/scriptService', () => mockScriptService);

const PROJECT: Project = {
  id: 'p1',
  name: 'Spring Campaign',
  description: null,
  owner_id: 'u1',
  team_id: 't1',
  project_type: 'internal',
  project_group: null,
  announcement: null,
  is_starred: false,
  color_label: null,
  archived_at: null,
  file_count: 128,
  created_at: '2026-06-01T00:00:00Z',
  updated_at: '2026-07-01T00:00:00Z',
};

// Deliberately unsorted (Ep 2 first) to pin the "default = lowest
// sort_order" selection logic rather than trivially picking array[0].
const EPISODES: EpisodeProgress[] = [
  {
    episode_id: '2',
    title: 'Ep 2 — Cutdown',
    sort_order: 20,
    script_count: 1,
    scene_count: 2,
    shots_total: 4,
    shots_done: 0,
    renders_count: 0,
    status: 'drafting',
  },
  {
    episode_id: '1',
    title: 'Ep 1 — Pilot',
    sort_order: 10,
    script_count: 1,
    scene_count: 4,
    shots_total: 12,
    shots_done: 9,
    renders_count: 1,
    status: 'boarding',
  },
];

beforeEach(() => {
  navigate.mockClear();
  addToast.mockClear();
  mockProjectsService.fetchStageCatalog.mockReset().mockResolvedValue([]);
  mockProjectsService.fetchCurrentStage.mockReset().mockResolvedValue(null);
  mockProjectsService.setCurrentStage.mockReset();
  mockProjectsService.fetchStageSuggestion.mockReset().mockResolvedValue({
    stage_slug: null,
    kind: '',
    progress: null,
    action: null,
  });
  mockProjectsService.generateMissingFrames.mockReset();
  mockProjectsService.fetchEpisodesProgress.mockReset().mockResolvedValue(EPISODES);
  mockScriptService.fetchScriptProjects.mockReset().mockResolvedValue({ data: [], total: 0 });
  localStorage.clear();
});

afterEach(() => cleanup());

const noop = () => {};

describe('ProjectWorkspace', () => {
  it('renders sidebar groups and the current-episode block from the episodes progress feed', async () => {
    render(<ProjectWorkspace project={PROJECT} teamId="t1" onBack={noop} />);

    expect(await screen.findByTestId('ws-module-overview')).toBeTruthy();
    expect(screen.getByTestId('ws-module-canvas')).toBeTruthy();
    expect(screen.getByTestId('ws-module-episodes')).toHaveTextContent('2');

    // Lowest sort_order (Ep 1, sort_order 10) wins as the default, not
    // array[0] (Ep 2 is first in the mocked response).
    const epCard = await screen.findByTestId('ws-ep-card');
    expect(epCard).toHaveTextContent('Ep 1 — Pilot');
    expect(screen.getByTestId('ws-ep-storyboard')).toHaveTextContent('9/12');
    expect(screen.getByTestId('ws-ep-renders')).toHaveTextContent('1');

    expect(screen.getByTestId('ws-module-characters')).toBeTruthy();
    expect(screen.getByTestId('ws-module-locations')).toBeTruthy();
    expect(screen.getByTestId('ws-module-files')).toBeTruthy();
    expect(screen.getByTestId('ws-module-trash')).toBeTruthy();
    expect(screen.getByTestId('ws-module-settings')).toBeTruthy();
  });

  it('switches the current episode via the popover and persists the choice', async () => {
    render(<ProjectWorkspace project={PROJECT} teamId="t1" onBack={noop} />);

    const epCard = await screen.findByTestId('ws-ep-card');
    fireEvent.click(epCard);
    const option = await screen.findByTestId('ws-ep-option-2');
    fireEvent.click(option);

    await waitFor(() =>
      expect(screen.getByTestId('ws-ep-card')).toHaveTextContent('Ep 2 — Cutdown'),
    );
    expect(screen.getByTestId('ws-ep-storyboard')).toHaveTextContent('0/4');
    expect(localStorage.getItem('mediahub.project.p1.ep')).toBe('2');
  });

  it('switches content to the shared placeholder for a module without real content yet (Canvas)', async () => {
    render(<ProjectWorkspace project={PROJECT} teamId="t1" onBack={noop} />);

    fireEvent.click(await screen.findByTestId('ws-module-canvas'));
    expect(await screen.findByTestId('workspace-placeholder-canvas')).toBeTruthy();
    expect(screen.queryByTestId('ws-overview')).toBeNull();
  });

  // PR-10b Wave 2 — Episodes/Characters/Locations/Files/Trash/Settings now
  // render real module content instead of the shared placeholder; see
  // WorkspaceEpisodes.test.tsx / WorkspaceEntities.test.tsx /
  // WorkspaceFiles.test.tsx for their own coverage. This suite only pins
  // the shell's module-switch wiring.
  it('switches to the real Episodes module content', async () => {
    render(<ProjectWorkspace project={PROJECT} teamId="t1" onBack={noop} />);

    fireEvent.click(await screen.findByTestId('ws-module-episodes'));
    expect(await screen.findByTestId('ws-episodes')).toBeTruthy();
    expect(screen.queryByTestId('ws-overview')).toBeNull();
  });

  it('opens the current episode script when one exists', async () => {
    mockScriptService.fetchScriptProjects.mockResolvedValue({
      data: [
        { id: 's1', name: 'Draft', status: 'active', created_at: '', updated_at: '2026-07-01T00:00:00Z', episode_id: '1' },
      ],
      total: 1,
    });
    render(<ProjectWorkspace project={PROJECT} teamId="t1" onBack={noop} />);

    fireEvent.click(await screen.findByTestId('ws-ep-script'));
    await waitFor(() =>
      expect(navigate).toHaveBeenCalledWith('/team/t1/projects/p1/scripts/s1'),
    );
  });

  it('falls back to the Episodes module when the current episode has no script', async () => {
    mockScriptService.fetchScriptProjects.mockResolvedValue({ data: [], total: 0 });
    render(<ProjectWorkspace project={PROJECT} teamId="t1" onBack={noop} />);

    fireEvent.click(await screen.findByTestId('ws-ep-script'));
    expect(await screen.findByTestId('ws-episodes')).toBeTruthy();
    expect(navigate).not.toHaveBeenCalled();
  });
});
