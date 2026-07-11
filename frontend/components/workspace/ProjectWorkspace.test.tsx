/**
 * ProjectWorkspace (合一终稿, 2026-07-11) — the workspace shell smoke suite.
 *
 * Pins: the single left-tree sidebar renders from the episodes progress feed;
 * the 剧集 node EXPANDS to reveal the episode's work views (剧本/节拍/分镜/场景/
 * 成片/发布); the ⇄ card swaps the current episode (persisted to localStorage);
 * clicking a work view resolves the current episode's script and mounts it
 * INLINE via EditorShell while the sidebar STAYS mounted (the editor drops its
 * own rail in embedded mode); the editor's scene list lifts up into the
 * sidebar's SCENES section and a scene click routes back into the editor.
 * EditorShell itself is a thin stub recording the props it was called with.
 */
import { useEffect } from 'react';
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
const mockSelectScene = vi.hoisted(() => vi.fn());
vi.mock('../../editor/components/EditorShell', () => ({
  EditorShell: (props: {
    scriptId: string;
    projectId?: string;
    initialRailView?: string;
    currentUserId?: string | null;
    currentUserName?: string;
    embedded?: boolean;
    onScenesChange?: (scenes: Array<{ id: string; heading_int_ext: string | null; location_text: string | null }>) => void;
    onActiveSceneChange?: (id: string | null) => void;
    selectSceneRef?: { current: ((id: string) => void) | null };
  }) => {
    mockEditorShell(props);
    // Lift a scene up to the workspace once, on mount (mirrors the real shell's
    // onScenesChange effect), and publish a scene-select fn back to the sidebar.
    useEffect(() => {
      props.onScenesChange?.([{ id: 'sc1', heading_int_ext: 'INT', location_text: 'Test Loc' }]);
      props.onActiveSceneChange?.('sc1');
      if (props.selectSceneRef) props.selectSceneRef.current = mockSelectScene;
      // eslint-disable-next-line react-hooks/exhaustive-deps
    }, []);
    return (
      <div
        data-testid="mock-editor-shell"
        data-script-id={props.scriptId}
        data-project-id={props.projectId ?? ''}
        data-initial-rail-view={props.initialRailView ?? ''}
        data-embedded={props.embedded ? 'true' : 'false'}
      />
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
  mockSelectScene.mockClear();
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

/** Expand the 剧集 tree node (its work views are hidden while collapsed). */
async function expandEpisodesTree() {
  fireEvent.click(await screen.findByTestId('ws-module-episodes'));
  return screen.findByTestId('ws-ep-card');
}

describe('ProjectWorkspace', () => {
  it('renders sidebar groups and, once expanded, the current-episode work views', async () => {
    render(<ProjectWorkspace project={PROJECT} teamId="t1" onBack={noop} />);

    expect(await screen.findByTestId('ws-module-overview')).toBeTruthy();
    expect(screen.getByTestId('ws-module-canvas')).toBeTruthy();
    expect(screen.getByTestId('ws-module-episodes')).toHaveTextContent('2');

    // Expand the tree to reveal the current episode's work views. Lowest
    // sort_order (Ep 1, sort_order 10) wins as the default, not array[0].
    const epCard = await expandEpisodesTree();
    expect(epCard).toHaveTextContent('Ep 1 — Pilot');
    expect(screen.getByTestId('ws-ep-script')).toBeTruthy();
    expect(screen.getByTestId('ws-ep-beats')).toBeTruthy();
    expect(screen.getByTestId('ws-ep-storyboard')).toHaveTextContent('9/12');
    expect(screen.getByTestId('ws-ep-scenes')).toHaveTextContent('4');
    expect(screen.getByTestId('ws-ep-renders')).toHaveTextContent('1');

    expect(screen.getByTestId('ws-module-characters')).toBeTruthy();
    expect(screen.getByTestId('ws-module-locations')).toBeTruthy();
    expect(screen.getByTestId('ws-module-files')).toBeTruthy();
    expect(screen.getByTestId('ws-module-trash')).toBeTruthy();
    expect(screen.getByTestId('ws-module-settings')).toBeTruthy();
  });

  it('switches the current episode via the ⇄ card popover and persists the choice', async () => {
    render(<ProjectWorkspace project={PROJECT} teamId="t1" onBack={noop} />);

    const epCard = await expandEpisodesTree();
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

  it('switches to the real Episodes module content', async () => {
    render(<ProjectWorkspace project={PROJECT} teamId="t1" onBack={noop} />);

    fireEvent.click(await screen.findByTestId('ws-module-episodes'));
    expect(await screen.findByTestId('ws-episodes')).toBeTruthy();
    expect(screen.queryByTestId('ws-overview')).toBeNull();
  });

  it('mounts EditorShell inline (embedded) with the resolved scriptId, sidebar STAYS', async () => {
    mockScriptService.fetchScriptProjects.mockResolvedValue({
      data: [
        { id: 's1', name: 'Draft', status: 'active', created_at: '', updated_at: '2026-07-01T00:00:00Z', episode_id: '1' },
      ],
      total: 1,
    });
    render(<ProjectWorkspace project={PROJECT} teamId="t1" onBack={noop} />);

    await expandEpisodesTree();
    fireEvent.click(await screen.findByTestId('ws-ep-script'));
    const shell = await screen.findByTestId('mock-editor-shell');
    expect(shell).toHaveAttribute('data-script-id', 's1');
    expect(shell).toHaveAttribute('data-project-id', 'p1');
    expect(shell).toHaveAttribute('data-embedded', 'true');
    // Script view — its own default (no storyboard preset here, but studioView
    // defaults to 'script', so the shell is told 'script').
    expect(shell).toHaveAttribute('data-initial-rail-view', 'script');
    expect(navigate).not.toHaveBeenCalled();
    // The unified sidebar stays mounted alongside the embedded editor — the
    // editor drops its OWN rail, this tree is the single side navigation.
    expect(screen.getByTestId('workspace-sidebar')).toBeInTheDocument();
    expect(screen.queryByTestId('ws-overview')).toBeNull();
  });

  it('mounts EditorShell preset to the storyboard view when 分镜 is clicked', async () => {
    mockScriptService.fetchScriptProjects.mockResolvedValue({
      data: [
        { id: 's1', name: 'Draft', status: 'active', created_at: '', updated_at: '2026-07-01T00:00:00Z', episode_id: '1' },
      ],
      total: 1,
    });
    render(<ProjectWorkspace project={PROJECT} teamId="t1" onBack={noop} />);

    await expandEpisodesTree();
    fireEvent.click(await screen.findByTestId('ws-ep-storyboard'));
    const shell = await screen.findByTestId('mock-editor-shell');
    expect(shell).toHaveAttribute('data-script-id', 's1');
    expect(shell).toHaveAttribute('data-initial-rail-view', 'storyboard');
    expect(navigate).not.toHaveBeenCalled();
  });

  it('re-resolves the mounted script for the newly selected episode via the ⇄ card', async () => {
    mockScriptService.fetchScriptProjects.mockResolvedValue({
      data: [
        { id: 's1', name: 'Draft 1', status: 'active', created_at: '', updated_at: '2026-07-01T00:00:00Z', episode_id: '1' },
        { id: 's2', name: 'Draft 2', status: 'active', created_at: '', updated_at: '2026-07-02T00:00:00Z', episode_id: '2' },
      ],
      total: 2,
    });
    render(<ProjectWorkspace project={PROJECT} teamId="t1" onBack={noop} />);

    // Open Script for the default episode (Ep 1, lowest sort_order) — resolves s1.
    await expandEpisodesTree();
    fireEvent.click(await screen.findByTestId('ws-ep-script'));
    expect(await screen.findByTestId('mock-editor-shell')).toHaveAttribute('data-script-id', 's1');
    expect(mockScriptService.fetchScriptProjects).toHaveBeenCalledTimes(1);

    // The sidebar ⇄ card is still there in studio mode (single-rail tree). The
    // switch re-resolves the new episode's script and remounts.
    fireEvent.click(screen.getByTestId('ws-ep-card'));
    fireEvent.click(await screen.findByTestId('ws-ep-option-2'));

    await waitFor(() =>
      expect(screen.getByTestId('mock-editor-shell')).toHaveAttribute('data-script-id', 's2'),
    );
    expect(screen.getByTestId('mock-editor-shell')).toHaveAttribute('data-project-id', 'p1');
    expect(navigate).not.toHaveBeenCalled();
    // Exactly one extra fetch for the re-resolve on top of the initial resolve.
    expect(mockScriptService.fetchScriptProjects).toHaveBeenCalledTimes(2);
  });

  it('lifts the editor scene list into the sidebar SCENES section and routes clicks back', async () => {
    mockScriptService.fetchScriptProjects.mockResolvedValue({
      data: [
        { id: 's1', name: 'Draft', status: 'active', created_at: '', updated_at: '2026-07-01T00:00:00Z', episode_id: '1' },
      ],
      total: 1,
    });
    render(<ProjectWorkspace project={PROJECT} teamId="t1" onBack={noop} />);

    await expandEpisodesTree();
    fireEvent.click(await screen.findByTestId('ws-ep-script'));
    await screen.findByTestId('mock-editor-shell');

    const sceneRow = await screen.findByTestId('ws-scene-sc1');
    expect(sceneRow).toHaveTextContent('S1');
    expect(sceneRow).toHaveTextContent('Test Loc');

    fireEvent.click(sceneRow);
    expect(mockSelectScene).toHaveBeenCalledWith('sc1');
  });

  it('auto-provisions an empty script when the episode has none, then mounts it', async () => {
    // Pre-epic projects: episode exists, script does not — the old silent
    // fall-back to Episodes read as a dead click on prod.
    mockScriptService.fetchScriptProjects.mockResolvedValue({ data: [], total: 0 });
    mockScriptService.createScriptProject.mockResolvedValue({ id: 'fresh-1', name: 'Episode 1' });
    render(<ProjectWorkspace project={PROJECT} teamId="t1" onBack={noop} />);

    await expandEpisodesTree();
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

    await expandEpisodesTree();
    fireEvent.click(await screen.findByTestId('ws-ep-script'));
    expect(await screen.findByTestId('ws-episodes')).toBeTruthy();
    expect(navigate).not.toHaveBeenCalled();
    expect(mockEditorShell).not.toHaveBeenCalled();
  });

  it('shows the disabled Publish placeholder in the current-episode block (G12)', async () => {
    render(<ProjectWorkspace project={PROJECT} teamId="t1" onBack={noop} />);

    await expandEpisodesTree();
    const publishBtn = await screen.findByTestId('ws-ep-publish');
    expect(publishBtn).toBeDisabled();
  });
});
