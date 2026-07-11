/**
 * ProjectWorkspace (PR-10b Wave 1 / PR-11) — the workspace shell smoke suite.
 *
 * Pins: sidebar groups + current-episode block render from the episodes
 * progress feed; the ⇄ switcher popover swaps the current episode (and
 * persists the choice to localStorage); non-overview sidebar clicks switch
 * to the shared placeholder; the Script/Storyboard children resolve the
 * current episode's script and mount it INLINE via EditorShell (PR-11 —
 * no more route jump), falling back to the Episodes module when none
 * exists. EditorShell itself is mocked to a thin stub (it's a heavy
 * component with its own extensive test suite) that records the props it
 * was called with, so this suite only pins ProjectWorkspace's own wiring:
 * which scriptId/projectId/initialRailView it resolves and passes down.
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

vi.mock('../../contexts/AuthContext', () => ({
  useAuth: () => ({ currentUserId: 'u1', userProfile: { name: 'Test Writer' } }),
}));

const mockEditorShell = vi.hoisted(() => vi.fn());
vi.mock('../../editor/components/EditorShell', () => ({
  EditorShell: (props: {
    scriptId: string;
    projectId?: string;
    initialRailView?: string;
    currentUserId?: string | null;
    currentUserName?: string;
    embedded?: boolean;
    episodesForSwitcher?: Array<{ episode_id: string; title: string }>;
    onSwitchEpisode?: (episodeId: string) => void;
  }) => {
    mockEditorShell(props);
    return (
      <div
        data-testid="mock-editor-shell"
        data-script-id={props.scriptId}
        data-project-id={props.projectId ?? ''}
        data-initial-rail-view={props.initialRailView ?? ''}
      >
        {(props.episodesForSwitcher ?? []).map((ep) => (
          <button
            key={ep.episode_id}
            data-testid={`mock-editor-switch-${ep.episode_id}`}
            onClick={() => props.onSwitchEpisode?.(ep.episode_id)}
          >
            {ep.title}
          </button>
        ))}
      </div>
    );
  },
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
  createScriptProject: vi.fn(),
  updateScriptProject: vi.fn(),
}));
vi.mock('../../services/scriptService', () => mockScriptService);

// WorkspaceCanvas (real Canvas module) fetches the project's canvases on
// mount — stub the service so the shell test stays network-free.
vi.mock('../../features/canvas-core/services/canvasService', () => ({
  listCanvases: vi.fn().mockResolvedValue([]),
  createCanvas: vi.fn(),
}));

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
  mockEditorShell.mockClear();
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
  mockScriptService.createScriptProject
    .mockReset()
    .mockResolvedValue({ id: 'created-1', name: 'Episode 1' });
  mockScriptService.updateScriptProject.mockReset().mockResolvedValue({ id: 'created-1' });
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

  it('switches content to the real Canvas module (the last placeholder is gone)', async () => {
    render(<ProjectWorkspace project={PROJECT} teamId="t1" onBack={noop} />);

    fireEvent.click(await screen.findByTestId('ws-module-canvas'));
    // WorkspaceCanvas fetches then renders the project's canvases; the
    // empty state proves the real module (not the placeholder) mounted.
    expect(await screen.findByTestId('workspace-canvas-empty')).toBeTruthy();
    expect(screen.queryByTestId('workspace-placeholder-canvas')).toBeNull();
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

  it('mounts EditorShell inline with the resolved scriptId when the Script child is clicked (PR-11)', async () => {
    mockScriptService.fetchScriptProjects.mockResolvedValue({
      data: [
        { id: 's1', name: 'Draft', status: 'active', created_at: '', updated_at: '2026-07-01T00:00:00Z', episode_id: '1' },
      ],
      total: 1,
    });
    render(<ProjectWorkspace project={PROJECT} teamId="t1" onBack={noop} />);

    fireEvent.click(await screen.findByTestId('ws-ep-script'));
    const shell = await screen.findByTestId('mock-editor-shell');
    expect(shell).toHaveAttribute('data-script-id', 's1');
    expect(shell).toHaveAttribute('data-project-id', 'p1');
    // Script (not Storyboard) — no view preset, EditorShell falls back to its
    // own stored/'script' default.
    expect(shell).toHaveAttribute('data-initial-rail-view', '');
    expect(navigate).not.toHaveBeenCalled();
    // Studio mode is single-rail (2026-07-11): the workspace sidebar hides
    // while the editor is mounted — its rail is the only side navigation.
    // Still no deep-link route jump.
    expect(screen.queryByTestId('workspace-sidebar')).toBeNull();
    expect(screen.queryByTestId('ws-overview')).toBeNull();
  });

  it('mounts EditorShell preset to the storyboard view when the Storyboard child is clicked (PR-11)', async () => {
    mockScriptService.fetchScriptProjects.mockResolvedValue({
      data: [
        { id: 's1', name: 'Draft', status: 'active', created_at: '', updated_at: '2026-07-01T00:00:00Z', episode_id: '1' },
      ],
      total: 1,
    });
    render(<ProjectWorkspace project={PROJECT} teamId="t1" onBack={noop} />);

    fireEvent.click(await screen.findByTestId('ws-ep-storyboard'));
    const shell = await screen.findByTestId('mock-editor-shell');
    expect(shell).toHaveAttribute('data-script-id', 's1');
    expect(shell).toHaveAttribute('data-project-id', 'p1');
    expect(shell).toHaveAttribute('data-initial-rail-view', 'storyboard');
    expect(navigate).not.toHaveBeenCalled();
  });

  it('re-resolves the mounted script for the newly selected episode when switching via the ⇄ popover (PR-11)', async () => {
    mockScriptService.fetchScriptProjects.mockResolvedValue({
      data: [
        { id: 's1', name: 'Draft 1', status: 'active', created_at: '', updated_at: '2026-07-01T00:00:00Z', episode_id: '1' },
        { id: 's2', name: 'Draft 2', status: 'active', created_at: '', updated_at: '2026-07-02T00:00:00Z', episode_id: '2' },
      ],
      total: 2,
    });
    render(<ProjectWorkspace project={PROJECT} teamId="t1" onBack={noop} />);

    // Open Script for the default episode (Ep 1, lowest sort_order) — resolves s1.
    fireEvent.click(await screen.findByTestId('ws-ep-script'));
    expect(await screen.findByTestId('mock-editor-shell')).toHaveAttribute('data-script-id', 's1');
    expect(mockScriptService.fetchScriptProjects).toHaveBeenCalledTimes(1);

    // Studio mode hides the workspace sidebar (single-rail, 2026-07-11) — the
    // switch now goes through the editor rail's embedded switcher, which
    // delegates back via onSwitchEpisode. Same re-resolve requirement: the
    // new episode's script must replace Ep1's stale mount.
    expect(screen.queryByTestId('ws-ep-card')).not.toBeInTheDocument();
    fireEvent.click(screen.getByTestId('mock-editor-switch-2'));

    await waitFor(() =>
      expect(screen.getByTestId('mock-editor-shell')).toHaveAttribute('data-script-id', 's2'),
    );
    expect(screen.getByTestId('mock-editor-shell')).toHaveAttribute('data-project-id', 'p1');
    // Still no route jump.
    expect(navigate).not.toHaveBeenCalled();
    // Exactly one extra fetch for the re-resolve on top of the initial
    // resolve — pins a bounded re-resolve, not an effect loop.
    expect(mockScriptService.fetchScriptProjects).toHaveBeenCalledTimes(2);
  });

  it('auto-provisions an empty script when the episode has none, then mounts it (2026-07-11)', async () => {
    // Pre-epic projects: episode exists, script does not — the old silent
    // fall-back to Episodes read as a dead click on prod.
    mockScriptService.fetchScriptProjects.mockResolvedValue({ data: [], total: 0 });
    mockScriptService.createScriptProject.mockResolvedValue({ id: 'fresh-1', name: 'Episode 1' });
    render(<ProjectWorkspace project={PROJECT} teamId="t1" onBack={noop} />);

    fireEvent.click(await screen.findByTestId('ws-ep-script'));
    const shell = await screen.findByTestId('mock-editor-shell');
    expect(shell).toHaveAttribute('data-script-id', 'fresh-1');
    expect(mockScriptService.createScriptProject).toHaveBeenCalledWith({
      project_id: 'p1',
      name: 'Ep 1 — Pilot',
    });
    // Attached to the clicked episode so the next open resolves it directly.
    expect(mockScriptService.updateScriptProject).toHaveBeenCalledWith('fresh-1', {
      episode_id: '1',
    });
    expect(navigate).not.toHaveBeenCalled();
  });

  it('falls back to the Episodes module (with an error toast) when provisioning fails', async () => {
    mockScriptService.fetchScriptProjects.mockResolvedValue({ data: [], total: 0 });
    mockScriptService.createScriptProject.mockRejectedValue(new Error('boom'));
    render(<ProjectWorkspace project={PROJECT} teamId="t1" onBack={noop} />);

    fireEvent.click(await screen.findByTestId('ws-ep-script'));
    expect(await screen.findByTestId('ws-episodes')).toBeTruthy();
    expect(navigate).not.toHaveBeenCalled();
    expect(mockEditorShell).not.toHaveBeenCalled();
  });

  it('shows the disabled Publish placeholder in the current-episode block (PR-11, G12)', async () => {
    render(<ProjectWorkspace project={PROJECT} teamId="t1" onBack={noop} />);

    const publishBtn = await screen.findByTestId('ws-ep-publish');
    expect(publishBtn).toBeDisabled();
  });
});
