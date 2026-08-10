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
import type { EpisodeProgress, Project, ProjectStageNode, ProjectWorkflow } from '../../types';

const navigate = vi.fn();
// Mutable so a single test can seed the initial URL (e.g. `?module=stage`
// with no `node`) without needing a real router — read once per render via
// the initializer in ProjectWorkspace, so tests set it BEFORE calling render().
const mockSearchParams = vi.hoisted(() => ({ current: new URLSearchParams() }));
vi.mock('react-router-dom', async (importOriginal) => {
  const actual = await importOriginal<Record<string, unknown>>();
  return {
    ...actual,
    useNavigate: () => navigate,
    useSearchParams: () => [mockSearchParams.current, vi.fn()],
  };
});

const addToast = vi.fn();
vi.mock('../Toast', () => ({
  useToast: () => ({ addToast }),
  // WorkspaceTopBar's Autopilot chip (M4 Autopilot task O3) uses the
  // optional variant so it stays safe in provider-less hosts — this mock
  // module has no <ToastProvider>, so it must still export something.
  useOptionalToast: () => ({ addToast }),
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
    initialFocusSceneId?: string;
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
        data-initial-focus-scene-id={props.initialFocusSceneId ?? ''}
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
  generateMissingFrames: vi.fn(),
  fetchEpisodesProgress: vi.fn(),
  // WorkspaceTopBar's Autopilot chip (M4 Autopilot task O3) — unused by these
  // tests (no click on the chip), just needs to exist so the import resolves.
  updateProject: vi.fn(),
  // WorkflowSection's mount effect (only reached once a test seeds a
  // non-empty `workflow` via mockWorkflowService.fetchProjectWorkflow —
  // the surface-panel tests below) — the section swallows a rejection
  // itself, but resolving cleanly keeps those tests quiet/deterministic.
  fetchProjectMembers: vi.fn(),
}));
vi.mock('../../services/projectsService', () => mockProjectsService);

const mockScriptService = vi.hoisted(() => ({
  fetchScriptProjects: vi.fn(),
  createScriptProject: vi.fn(),
  updateScriptProject: vi.fn(),
}));
vi.mock('../../services/scriptService', () => mockScriptService);

// Task 3 (主工作面接线): the storyboard surface panel renders WorkflowSection
// (workflow-strip node click routing) and, once a node's surface is
// 'storyboard', EpisodeSceneBoard/EpisodeShotListTable — both self-fetch via
// sceneService. Mocked here so those tests stay network-free; most existing
// tests below never populate a workflow, so `fetchProjectWorkflow` defaults
// to resolving `null` (identical to the previously-unmocked, always-pending
// real fetch: `workflow` state simply never becomes truthy).
const mockWorkflowService = vi.hoisted(() => ({
  fetchProjectWorkflow: vi.fn(),
  fetchAdvancePreview: vi.fn(),
  executeAdvance: vi.fn(),
  addProjectNode: vi.fn(),
  deleteProjectNode: vi.fn(),
  fetchStageLibrary: vi.fn().mockResolvedValue([]),
  startEarlyNode: vi.fn(),
  updateProjectNode: vi.fn(),
  // Task 7 (strip 解耦): a non-current node click now lands on its own Stage
  // Board (WorkspaceStageBoard, unmocked in this suite) instead of the
  // current episode's surface panel — that real component calls
  // fetchStageBoard on mount.
  fetchStageBoard: vi.fn(),
}));
vi.mock('../../services/workflowService', () => mockWorkflowService);

const mockAiLibraryService = vi.hoisted(() => ({
  aiLibraryService: { listAgents: vi.fn().mockResolvedValue([]) },
}));
vi.mock('../../services/aiLibraryService', () => mockAiLibraryService);

const mockSceneService = vi.hoisted(() => ({
  listScenes: vi.fn(),
  listShots: vi.fn(),
  autoStoryboard: vi.fn(),
}));
vi.mock('../../editor/sceneService', () => mockSceneService);

// WorkspaceCanvas (real Canvas module) fetches the project's canvases on
// mount — stub the service so the shell test stays network-free.
vi.mock('../../features/canvas-core/services/canvasService', () => ({
  listCanvases: vi.fn().mockResolvedValue([]),
  createCanvas: vi.fn(),
}));

/** Minimal-but-complete ProjectStageNode builder (mirrors WorkflowStrip.test.tsx's factory). */
function stageNode(over: Partial<ProjectStageNode>): ProjectStageNode {
  return {
    id: '1',
    project_id: 'p1',
    source_template_node_id: null,
    legacy_stage_id: null,
    name: 'Storyboard',
    sort_order: 0,
    parallel_group: null,
    status: 'in_progress',
    owner_user_id: null,
    owner_agent_id: null,
    planned_start: null,
    planned_due: null,
    review_required: false,
    deliverable_required: false,
    deliverable_label: null,
    skipped: false,
    surface: 'storyboard',
    members: [],
    completion_policy: 'owner',
    events: { notify_on_arrival: true, notify_on_complete: false, suggest_agent_run: false },
    ...over,
  };
}

/** A one-node workflow instance whose current node is the storyboard-surface node above. */
function storyboardWorkflow(nodeOver: Partial<ProjectStageNode> = {}): ProjectWorkflow {
  const node = stageNode(nodeOver);
  return { has_workflow: true, current_node_id: node.id, agents_active: 0, nodes: [node] };
}

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
  mockProjectsService.generateMissingFrames.mockReset();
  mockProjectsService.fetchEpisodesProgress.mockReset().mockResolvedValue(EPISODES);
  mockScriptService.fetchScriptProjects.mockReset().mockResolvedValue({ data: [], total: 0 });
  mockScriptService.createScriptProject
    .mockReset()
    .mockResolvedValue({ id: 'created-1', name: 'Episode 1' });
  mockScriptService.updateScriptProject.mockReset().mockResolvedValue({ id: 'created-1' });
  mockProjectsService.fetchProjectMembers.mockReset().mockResolvedValue([]);
  mockWorkflowService.fetchProjectWorkflow.mockReset().mockResolvedValue(null);
  mockWorkflowService.fetchAdvancePreview.mockReset();
  mockWorkflowService.executeAdvance.mockReset();
  mockWorkflowService.fetchStageBoard.mockReset();
  mockSceneService.listScenes.mockReset().mockResolvedValue([]);
  mockSceneService.listShots.mockReset().mockResolvedValue([]);
  mockSceneService.autoStoryboard.mockReset();
  mockSearchParams.current = new URLSearchParams();
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

  it('keeps the 剧集 row icon left-aligned with the other rows (expand chevron does not indent it)', async () => {
    render(<ProjectWorkspace project={PROJECT} teamId="t1" onBack={noop} />);

    const epRow = await screen.findByTestId('ws-module-episodes');
    const icons = Array.from(epRow.querySelectorAll('svg.lucide'));
    // The row's LEADING icon must be the module glyph (ListVideo), NOT the
    // expand chevron — the chevron used to sit before it and pushed the icon
    // out of the shared left-icon column.
    expect(icons.length).toBeGreaterThan(0);
    expect(icons[0]!.classList.contains('lucide-list-video')).toBe(true);
    // The expand affordance still exists (relocated to the row's right group).
    expect(
      epRow.querySelector('.lucide-chevron-right, .lucide-chevron-down'),
    ).toBeTruthy();
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

  it('URL `ep` wins over localStorage on load (Task 1, IA redesign)', async () => {
    // localStorage says Ep 1, but the URL says Ep 2 — a deep link (e.g.
    // shared, or back/forward navigated) must win over what this browser
    // last remembered.
    localStorage.setItem('mediahub.project.p1.ep', '1');
    mockSearchParams.current = new URLSearchParams('ep=2');
    render(<ProjectWorkspace project={PROJECT} teamId="t1" onBack={noop} />);

    const epCard = await expandEpisodesTree();
    expect(epCard).toHaveTextContent('Ep 2 — Cutdown');
  });

  it('falls back to localStorage, then the first episode, when URL `ep` is missing or invalid', async () => {
    localStorage.setItem('mediahub.project.p1.ep', '2');
    mockSearchParams.current = new URLSearchParams('ep=does-not-exist');
    render(<ProjectWorkspace project={PROJECT} teamId="t1" onBack={noop} />);

    const epCard = await expandEpisodesTree();
    expect(epCard).toHaveTextContent('Ep 2 — Cutdown');
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

  // Storyboard is now the episode node's primary face (三视图主工作面, PR-A) —
  // a bare click (sidebar 分镜 row, or a workflow-strip node — see the next
  // test) lands on Overview with the storyboard surface panel expanded
  // instead of diving straight into the embedded editor.
  it('lands on the Overview storyboard surface panel — not the embedded editor — when 分镜 is clicked', async () => {
    mockScriptService.fetchScriptProjects.mockResolvedValue({
      data: [
        { id: 's1', name: 'Draft', status: 'active', created_at: '', updated_at: '2026-07-01T00:00:00Z', episode_id: '1' },
      ],
      total: 1,
    });
    mockWorkflowService.fetchProjectWorkflow.mockResolvedValue(storyboardWorkflow());
    mockSceneService.listScenes.mockResolvedValue([
      {
        id: '200',
        script_id: 's1',
        chapter_id: null,
        scene_number: null,
        heading_int_ext: 'INT',
        location_text: 'Kitchen',
        time_of_day: 'DAY',
        content_version: 1,
        sort_order: 0,
        elements: [],
      },
    ]);
    render(<ProjectWorkspace project={PROJECT} teamId="t1" onBack={noop} />);

    await expandEpisodesTree();
    fireEvent.click(await screen.findByTestId('ws-ep-storyboard'));

    expect(await screen.findByTestId('ep-scene-card-200')).toBeInTheDocument();
    expect(screen.getByTestId('episode-view-tabs')).toBeInTheDocument();
    expect(mockSceneService.listScenes).toHaveBeenCalledWith('s1');
    expect(screen.queryByTestId('mock-editor-shell')).toBeNull();
    expect(navigate).not.toHaveBeenCalled();
  });

  it('routes a storyboard-surface workflow-strip node click (handleSelectNode) to the same surface panel', async () => {
    mockScriptService.fetchScriptProjects.mockResolvedValue({
      data: [
        { id: 's1', name: 'Draft', status: 'active', created_at: '', updated_at: '2026-07-01T00:00:00Z', episode_id: '1' },
      ],
      total: 1,
    });
    mockWorkflowService.fetchProjectWorkflow.mockResolvedValue(storyboardWorkflow());
    mockSceneService.listScenes.mockResolvedValue([
      {
        id: '200',
        script_id: 's1',
        chapter_id: null,
        scene_number: null,
        heading_int_ext: 'INT',
        location_text: 'Kitchen',
        time_of_day: 'DAY',
        content_version: 1,
        sort_order: 0,
        elements: [],
      },
    ]);
    render(<ProjectWorkspace project={PROJECT} teamId="t1" onBack={noop} />);

    fireEvent.click(await screen.findByTestId('workflow-strip-node'));

    expect(await screen.findByTestId('ep-scene-card-200')).toBeInTheDocument();
    expect(screen.queryByTestId('mock-editor-shell')).toBeNull();
  });

  // Task 7 (小尾巴 A, 2026-08-09 拍板): the strip used to route ANY node click
  // by its creative surface, so clicking a non-current storyboard/script node
  // flashed open the CURRENT episode's surface panel — content unrelated to
  // the node the writer actually clicked. Decoupled: only the CURRENT node's
  // capsule routes to the surface panel; every other node's click lands on
  // that node's own Stage Board (handleOpenStage), same path deliverable-only
  // nodes already take.
  it('routes a non-current workflow-strip node click to that node\'s own Stage Board, not the current episode\'s surface panel', async () => {
    const currentNode = stageNode({ id: '1', name: 'Storyboard', status: 'in_progress', surface: 'storyboard' });
    const otherNode = stageNode({ id: '2', name: 'Script Pass 2', status: 'pending', surface: 'script' });
    mockWorkflowService.fetchProjectWorkflow.mockResolvedValue({
      has_workflow: true,
      current_node_id: currentNode.id,
      agents_active: 0,
      nodes: [currentNode, otherNode],
    });
    mockWorkflowService.fetchStageBoard.mockResolvedValue({ node: otherNode, issue: null, files: [] });
    render(<ProjectWorkspace project={PROJECT} teamId="t1" onBack={noop} />);

    const nodeButtons = await screen.findAllByTestId('workflow-strip-node');
    const otherButton = nodeButtons.find((el) => el.getAttribute('data-node-id') === '2');
    expect(otherButton).toBeTruthy();
    fireEvent.click(otherButton!);

    expect(await screen.findByTestId('workspace-stage-board')).toBeInTheDocument();
    expect(mockWorkflowService.fetchStageBoard).toHaveBeenCalledWith('p1', '2');
    // Never flashed the CURRENT episode's storyboard/script surface — nor
    // provisioned/mounted a script — for a click that was about a different
    // (non-current) node entirely.
    expect(screen.queryByTestId('episode-view-tabs')).toBeNull();
    expect(screen.queryByTestId('mock-editor-shell')).toBeNull();
    expect(mockScriptService.createScriptProject).not.toHaveBeenCalled();
  });

  it('a scene card\'s Open button deep-links into the embedded editor at the storyboard rail view', async () => {
    mockScriptService.fetchScriptProjects.mockResolvedValue({
      data: [
        { id: 's1', name: 'Draft', status: 'active', created_at: '', updated_at: '2026-07-01T00:00:00Z', episode_id: '1' },
      ],
      total: 1,
    });
    mockWorkflowService.fetchProjectWorkflow.mockResolvedValue(storyboardWorkflow());
    mockSceneService.listScenes.mockResolvedValue([
      {
        id: '200',
        script_id: 's1',
        chapter_id: null,
        scene_number: null,
        heading_int_ext: 'INT',
        location_text: 'Kitchen',
        time_of_day: 'DAY',
        content_version: 1,
        sort_order: 0,
        elements: [],
      },
    ]);
    render(<ProjectWorkspace project={PROJECT} teamId="t1" onBack={noop} />);

    await screen.findByTestId('ep-scene-card-200');
    fireEvent.click(screen.getByTestId('ep-scene-open-200'));

    const shell = await screen.findByTestId('mock-editor-shell');
    expect(shell).toHaveAttribute('data-script-id', 's1');
    expect(shell).toHaveAttribute('data-initial-rail-view', 'storyboard');
    expect(shell).toHaveAttribute('data-initial-focus-scene-id', '200');
  });

  // Fix round 1 regression (2026-08-09 review): a scene-card deep link used to
  // leave studioFocusSceneId set in ProjectWorkspace state even after
  // EditorShell unmounted (leaving the 'script' module drops it — studioMode
  // gates the mount). The NEXT unrelated mount — "Continue Writing" or an
  // episode row's Open button (handleOpenEpisode), neither of which passes a
  // sceneId — then re-mounted EditorShell with the STALE initialFocusSceneId
  // still attached, silently re-scrolling to a scene the writer never asked
  // for this time. Fixed by threading focusSceneId through openEpisodeScript
  // (the sole call site that flips activeModule to 'script') so every
  // mount-causing entry point declares its intent explicitly — undeclared
  // defaults to clearing it.
  it('a stale scene-card deep link does not survive into an unrelated later mount (handleOpenEpisode)', async () => {
    mockScriptService.fetchScriptProjects.mockResolvedValue({
      data: [
        { id: 's1', name: 'Draft', status: 'active', created_at: '', updated_at: '2026-07-01T00:00:00Z', episode_id: '1' },
      ],
      total: 1,
    });
    mockWorkflowService.fetchProjectWorkflow.mockResolvedValue(storyboardWorkflow());
    mockSceneService.listScenes.mockResolvedValue([
      {
        id: '200',
        script_id: 's1',
        chapter_id: null,
        scene_number: null,
        heading_int_ext: 'INT',
        location_text: 'Kitchen',
        time_of_day: 'DAY',
        content_version: 1,
        sort_order: 0,
        elements: [],
      },
    ]);
    render(<ProjectWorkspace project={PROJECT} teamId="t1" onBack={noop} />);

    // Step 1: scene-card Open deep-links into EditorShell with the target scene.
    await screen.findByTestId('ep-scene-card-200');
    fireEvent.click(screen.getByTestId('ep-scene-open-200'));
    const firstShell = await screen.findByTestId('mock-editor-shell');
    expect(firstShell).toHaveAttribute('data-initial-focus-scene-id', '200');

    // Step 2: navigate away — EditorShell unmounts (studioMode gates on
    // activeModule === 'script'), but studioFocusSceneId is bare React state
    // that outlives the unmount unless something explicitly clears it.
    fireEvent.click(await screen.findByTestId('ws-module-episodes'));
    expect(await screen.findByTestId('ws-episodes')).toBeTruthy();
    expect(screen.queryByTestId('mock-editor-shell')).toBeNull();

    // Step 3: an UNRELATED remount via the episode row's Open button
    // (handleOpenEpisode) — no sceneId involved at all.
    fireEvent.click(await screen.findByTestId('ws-episode-open-1'));
    const secondShell = await screen.findByTestId('mock-editor-shell');
    expect(secondShell).toHaveAttribute('data-initial-focus-scene-id', '');
  });

  it('switches to the Shot List tab and exports CSV via the tabs\' actions-slot button', async () => {
    mockScriptService.fetchScriptProjects.mockResolvedValue({
      data: [
        { id: 's1', name: 'Draft', status: 'active', created_at: '', updated_at: '2026-07-01T00:00:00Z', episode_id: '1' },
      ],
      total: 1,
    });
    mockWorkflowService.fetchProjectWorkflow.mockResolvedValue(storyboardWorkflow());
    mockSceneService.listScenes.mockResolvedValue([
      {
        id: '200',
        script_id: 's1',
        chapter_id: null,
        scene_number: '1',
        heading_int_ext: 'INT',
        location_text: 'Kitchen',
        time_of_day: 'DAY',
        content_version: 1,
        sort_order: 0,
        elements: [],
      },
    ]);
    mockSceneService.listShots.mockResolvedValue([
      {
        id: '900',
        scene_id: '200',
        shot_number: 1,
        shot_type: 'WIDE',
        camera_angle: 'EYE',
        camera_movement: 'STATIC',
        focal_length: '35mm',
        lighting: null,
        description: 'Establishing shot.',
        image_url: null,
        thumbnail_url: null,
        video_url: null,
        status: 'empty',
        sort_order: 0,
      },
    ]);
    const createSpy = vi.spyOn(URL, 'createObjectURL').mockReturnValue('blob:mock');
    vi.spyOn(URL, 'revokeObjectURL').mockImplementation(() => {});
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});
    render(<ProjectWorkspace project={PROJECT} teamId="t1" onBack={noop} />);

    await screen.findByTestId('ep-scene-card-200');
    const tabs = screen.getByTestId('episode-view-tabs');
    fireEvent.click(tabs.querySelector('[data-view="shotlist"]')!);

    expect(await screen.findByTestId('ep-shotlist-row-900')).toBeInTheDocument();
    // The table's own built-in Export button is suppressed (hideExport) — the
    // one visible trigger lives in the tabs' actions slot, driven via ref.
    expect(screen.getAllByTestId('ep-shotlist-export')).toHaveLength(1);
    fireEvent.click(screen.getByTestId('ep-shotlist-export'));
    expect(createSpy).toHaveBeenCalledTimes(1);
  });

  // Review fix (Task 3 round 1, Critical): the surface panel shows by DEFAULT
  // on Overview landing whenever the current node is storyboard-surfaced —
  // no click required. Its scriptId resolution MUST be read-only; this test
  // is the anti-regression pin — zero create calls from a passive render.
  it('never provisions a script just from landing on the storyboard surface panel — renders the "no script" empty state instead', async () => {
    mockScriptService.fetchScriptProjects.mockResolvedValue({ data: [], total: 0 });
    mockWorkflowService.fetchProjectWorkflow.mockResolvedValue(storyboardWorkflow());
    render(<ProjectWorkspace project={PROJECT} teamId="t1" onBack={noop} />);

    expect(await screen.findByTestId('episode-surface-no-script')).toBeInTheDocument();
    expect(screen.getByTestId('episode-surface-start-storyboard')).toBeInTheDocument();
    expect(screen.queryByTestId('ep-scene-card-200')).toBeNull();
    expect(mockScriptService.createScriptProject).not.toHaveBeenCalled();
  });

  it('clicking "Start Storyboard" provisions a script and the panel refreshes to the scene board', async () => {
    mockScriptService.fetchScriptProjects.mockResolvedValue({ data: [], total: 0 });
    mockScriptService.createScriptProject.mockResolvedValue({ id: 'created-sb', name: 'Ep 1 — Pilot' });
    mockWorkflowService.fetchProjectWorkflow.mockResolvedValue(storyboardWorkflow());
    mockSceneService.listScenes.mockResolvedValue([
      {
        id: '200',
        script_id: 'created-sb',
        chapter_id: null,
        scene_number: null,
        heading_int_ext: 'INT',
        location_text: 'Kitchen',
        time_of_day: 'DAY',
        content_version: 1,
        sort_order: 0,
        elements: [],
      },
    ]);
    render(<ProjectWorkspace project={PROJECT} teamId="t1" onBack={noop} />);

    fireEvent.click(await screen.findByTestId('episode-surface-start-storyboard'));

    expect(await screen.findByTestId('ep-scene-card-200')).toBeInTheDocument();
    expect(mockScriptService.createScriptProject).toHaveBeenCalledTimes(1);
    expect(mockScriptService.createScriptProject).toHaveBeenCalledWith({
      project_id: 'p1',
      name: 'Ep 1 — Pilot',
      episode_id: '1',
    });
    expect(mockSceneService.listScenes).toHaveBeenCalledWith('created-sb');
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

  it('does NOT render a SCENES list in the sidebar — the editor owns its own scene rail now', async () => {
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

    // The scene list moved OUT of the workspace nav into the editor's own slim
    // left rail beside the paper — the sidebar never shows per-scene rows.
    expect(screen.queryByTestId('ws-scene-sc1')).toBeNull();
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
    // Atomic create+bind: episode_id goes in the create body (single call), no
    // follow-up update. This collapses the old create-then-bind two-call dance
    // that raced into duplicate scripts (#1432).
    expect(mockScriptService.createScriptProject).toHaveBeenCalledWith({
      project_id: 'p1',
      name: 'Ep 1 — Pilot',
      episode_id: '1',
    });
    expect(mockScriptService.updateScriptProject).not.toHaveBeenCalled();
    expect(navigate).not.toHaveBeenCalled();
  });

  it('provisions exactly ONE script when the work view is double-clicked (no duplicate)', async () => {
    // The double-fire that produced two active scripts on prod (#1432): two
    // opens for the same episode before the first provision resolves must share
    // ONE createScriptProject call, not race into two.
    mockScriptService.fetchScriptProjects.mockResolvedValue({ data: [], total: 0 });
    let resolveCreate!: (v: { id: string; name: string }) => void;
    mockScriptService.createScriptProject.mockImplementation(
      () =>
        new Promise((res) => {
          resolveCreate = res;
        }),
    );
    render(<ProjectWorkspace project={PROJECT} teamId="t1" onBack={noop} />);

    await expandEpisodesTree();
    const scriptView = await screen.findByTestId('ws-ep-script');
    // Two clicks while the first provision is still in flight (create pending).
    fireEvent.click(scriptView);
    fireEvent.click(scriptView);

    // Both opens shared the single in-flight provision — createScriptProject
    // was issued exactly once.
    await waitFor(() =>
      expect(mockScriptService.createScriptProject).toHaveBeenCalledTimes(1),
    );

    resolveCreate({ id: 'fresh-1', name: 'Episode 1' });
    const shell = await screen.findByTestId('mock-editor-shell');
    expect(shell).toHaveAttribute('data-script-id', 'fresh-1');
    expect(mockScriptService.createScriptProject).toHaveBeenCalledTimes(1);
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

  it('falls back to Overview when ?module=stage is loaded without a node id (#final-review)', async () => {
    // A `stage` module URL with no `node` param is invalid (nothing to open a
    // board for) — stageNodeId's initializer falls back to null, and the
    // content switch used to match neither 'overview' nor a stage-with-node,
    // leaving the content area blank despite the module comment promising an
    // Overview fallback.
    mockSearchParams.current = new URLSearchParams('module=stage');
    render(<ProjectWorkspace project={PROJECT} teamId="t1" onBack={noop} />);

    expect(await screen.findByTestId('ws-overview')).toBeInTheDocument();
    expect(screen.queryByTestId('workspace-stage-board')).toBeNull();
    expect(screen.queryByTestId('stage-board-loading')).toBeNull();
  });
});
