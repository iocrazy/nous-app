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
import { render, screen, fireEvent, waitFor, cleanup, act } from '@testing-library/react';
import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest';

import { ProjectWorkspace } from './ProjectWorkspace';
import { ApiError } from '../../services/apiClient';
import { requestShotFocus } from '../agentActivity/shotFocusBus';
import type { EpisodeProgress, Project, ProjectStageNode, ProjectWorkflow } from '../../types';

const navigate = vi.fn();
// Mutable so a single test can seed the initial URL (e.g. `?module=stage`
// with no `node`) without needing a real router — read once per render via
// the initializer in ProjectWorkspace, so tests set it BEFORE calling render().
const mockSearchParams = vi.hoisted(() => ({ current: new URLSearchParams() }));
// A single stable identity (mirrors react-router's real setSearchParams,
// which doesn't change across renders) — needed so the Task 2 carry-over
// test below can inspect the raw updater `handleEpisodeChange` passes it,
// without depending on the mock actually re-deriving `mockSearchParams`.
const setSearchParamsSpy = vi.hoisted(() => vi.fn());
vi.mock('react-router-dom', async (importOriginal) => {
  const actual = await importOriginal<Record<string, unknown>>();
  return {
    ...actual,
    useNavigate: () => navigate,
    useSearchParams: () => [mockSearchParams.current, setSearchParamsSpy],
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
// Task 7 (keep-alive 显隐切换): fires on the mock's OWN unmount cleanup —
// the RED test for "EditorShell stays mounted across a module switch" needs
// a way to observe a REAL unmount (vs. just a prop-driven re-render), which
// `mockEditorShell`'s per-render call count alone can't distinguish.
const mockEditorShellUnmount = vi.hoisted(() => vi.fn());
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
      return () => mockEditorShellUnmount();
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

// StoryboardCanvasEmbed (shot-nodes-on-canvas Task 5) pulls in the entire
// canvas-core React Flow stack (CanvasComposer, canvasCoreStore's `create()`
// factory referencing saveCanvas/getCanvas at IMPORT time, realtime, etc.) —
// stubbed the same way EditorShell is above, recording the props it was
// mounted with. Its own resolve/render behaviour is covered by
// StoryboardCanvasEmbed.test.tsx / CanvasPage.focus.test.tsx.
const mockStoryboardCanvasEmbed = vi.hoisted(() => vi.fn());
vi.mock('../../features/canvas-core/ui/StoryboardCanvasEmbed', () => ({
  StoryboardCanvasEmbed: (props: {
    episodeId: string;
    teamId?: string;
    focusShotId?: string | null;
    onFocusHandled?: () => void;
  }) => {
    mockStoryboardCanvasEmbed(props);
    return (
      <div
        data-testid="mock-storyboard-canvas-embed"
        data-episode-id={props.episodeId}
        data-team-id={props.teamId ?? ''}
        data-focus-shot-id={props.focusShotId ?? ''}
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
  // WorkspaceNodeSettings' episode-owner PATCH (Task 10) — unused by most
  // tests below, just needs to exist so the import resolves.
  updateEpisode: vi.fn(),
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
  mockEditorShellUnmount.mockClear();
  mockSelectScene.mockClear();
  mockStoryboardCanvasEmbed.mockClear();
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
  // Task 9: node config PATCH, consumed by EpisodeNodeCard's editable owner
  // field via ProjectWorkspace's onPatchNode.
  mockWorkflowService.updateProjectNode.mockReset();
  mockAiLibraryService.aiLibraryService.listAgents.mockReset().mockResolvedValue([]);
  mockSceneService.listScenes.mockReset().mockResolvedValue([]);
  mockSceneService.listShots.mockReset().mockResolvedValue([]);
  mockSceneService.autoStoryboard.mockReset();
  mockSearchParams.current = new URLSearchParams();
  setSearchParamsSpy.mockClear();
  localStorage.clear();
});

afterEach(() => cleanup());

const noop = () => {};

/** Expand the 剧集 tree node (its work views are hidden while collapsed). */
async function expandEpisodesTree() {
  fireEvent.click(await screen.findByTestId('ws-module-episodes'));
  return screen.findByTestId('ws-ep-card');
}

/**
 * Navigate to the standalone Storyboard module (IA redesign Task 2) via the
 * sidebar's 分镜 row — the same entry point the old "surface panel" used to
 * show automatically on a passive Overview landing. Storyboard no longer
 * renders without this explicit click.
 */
async function openStoryboardModule() {
  await expandEpisodesTree();
  fireEvent.click(await screen.findByTestId('ws-ep-storyboard'));
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

  // Task 2 review carry-over: `view`/`scene`/`shot` are scoped to whichever
  // episode was current when they were set (e.g. a `?shot=` deep-link into
  // that episode's canvas/storyboard). An episode switch must drop them in
  // the SAME setSearchParams call that writes the new `ep` — otherwise a
  // stale shot/scene id from the episode just left behind would survive into
  // the newly-selected episode's Storyboard page.
  it('clears stale view/scene/shot params from the URL in the same call that writes the new ep (Task 2 carry-over)', async () => {
    render(<ProjectWorkspace project={PROJECT} teamId="t1" onBack={noop} />);

    const epCard = await expandEpisodesTree();
    fireEvent.click(epCard);
    fireEvent.click(await screen.findByTestId('ws-ep-option-2'));

    await waitFor(() => expect(setSearchParamsSpy).toHaveBeenCalled());
    // handleEpisodeChange's own call is the last one in this interaction —
    // apply its updater to a URL carrying a stale view/scene/shot from the
    // episode being left behind, and confirm all three are gone alongside
    // the new `ep`.
    const lastCall = setSearchParamsSpy.mock.calls[setSearchParamsSpy.mock.calls.length - 1];
    const updater = lastCall[0] as (prev: URLSearchParams) => URLSearchParams;
    const result = updater(new URLSearchParams('ep=1&view=canvas&scene=55&shot=900'));
    expect(result.get('ep')).toBe('2');
    expect(result.has('view')).toBe(false);
    expect(result.has('scene')).toBe(false);
    expect(result.has('shot')).toBe(false);
  });

  // 评审修复轮 (Important #3): `node=` is the OTHER per-episode deep link (the
  // Overview accordion's selected node id, Task 4/5) — left uncleared, a real
  // episode switch carried the OLD episode's node id into the NEW episode's
  // row. `renderNodeCard` computes `selectedNodeId ?? workflow.current_node_id`,
  // so a stale (but non-empty) `node=` always won over the new episode's own
  // cursor node — the accordion row expanded correctly but the node-card slot
  // rendered NOTHING (`workflow.nodes.find` misses an id belonging to a
  // different episode's node set).
  it('clears the stale accordion node= selection from the URL in the same call that writes the new ep (Important #3)', async () => {
    render(<ProjectWorkspace project={PROJECT} teamId="t1" onBack={noop} />);

    const epCard = await expandEpisodesTree();
    fireEvent.click(epCard);
    fireEvent.click(await screen.findByTestId('ws-ep-option-2'));

    await waitFor(() => expect(setSearchParamsSpy).toHaveBeenCalled());
    const lastCall = setSearchParamsSpy.mock.calls[setSearchParamsSpy.mock.calls.length - 1];
    const updater = lastCall[0] as (prev: URLSearchParams) => URLSearchParams;
    const result = updater(new URLSearchParams('ep=1&node=n1'));
    expect(result.get('ep')).toBe('2');
    expect(result.has('node')).toBe(false);
  });

  // 评审修复轮 (Important #2): the Overview accordion must always expand
  // whichever episode `workflow`/`renderNodeCard` are ACTUALLY scoped to
  // (`currentEpisodeId`), never a raw URL `ep=` that has diverged from it.
  // Task 10's Settings module mirrors ITS OWN episode browsing into the SAME
  // `ep=` key without moving `currentEpisodeId` (by design — Settings
  // browses independently of the main workspace) — before this fix, leaving
  // Settings back to Overview with a browsed-but-not-switched-to episode
  // left the accordion trusting that stale `ep=`, expanding the WRONG row
  // while `workflow` stayed scoped to the real `currentEpisodeId`.
  it('the accordion expands currentEpisodeId, not a URL ep= that has drifted away from it (Important #2)', async () => {
    render(<ProjectWorkspace project={PROJECT} teamId="t1" onBack={noop} />);

    // No `ep` param seeded → falls back to the lowest sort_order episode (Ep 1).
    await waitFor(() =>
      expect(screen.getByTestId('ep-accordion-row-1')).toHaveAttribute('data-open', 'true'),
    );
    expect(screen.getByTestId('ep-accordion-row-2')).not.toHaveAttribute('data-open', 'true');

    // Simulate the Settings-mirror desync: URL `ep=` now points at episode 2
    // (as if `handleNodeSettingsEpisodeChange` had written it while the
    // writer browsed Settings) while `currentEpisodeId` — real React state,
    // untouched by that mirror — is still episode 1.
    mockSearchParams.current = new URLSearchParams('ep=2');
    // Force a fresh render that re-reads searchParams, without touching
    // currentEpisodeId itself (a genuine, unrelated activeModule change).
    fireEvent.click(await screen.findByTestId('ws-module-episodes'));
    fireEvent.click(await screen.findByTestId('ws-module-overview'));

    // Still episode 1's row — NOT the stale URL ep=2 — because `workflow`
    // (fetched for currentEpisodeId) has nothing for episode 2.
    await waitFor(() =>
      expect(screen.getByTestId('ep-accordion-row-1')).toHaveAttribute('data-open', 'true'),
    );
    expect(screen.getByTestId('ep-accordion-row-2')).not.toHaveAttribute('data-open', 'true');
  });

  // Review fix round 1 (Important #2): `?shot=` is a one-shot deep-link
  // trigger, consumed by ProjectWorkspace's own URL-shot effect (moved out of
  // EpisodeStoryboardPage in Task 3 修复轮2). Before THIS fix,
  // `handleStoryboardViewChange` only wrote `view` — a manually-picked tab
  // left the stale `shot` in the URL, so a later remount (refresh, or
  // navigating out of and back into the Storyboard module) re-read it and
  // forcibly re-fired the shot focus, discarding whatever the writer was doing.
  it('clears the sticky ?shot= deep-link trigger from the URL on a manual view-tab switch', async () => {
    render(<ProjectWorkspace project={PROJECT} teamId="t1" onBack={noop} />);

    await openStoryboardModule();
    const tabs = await screen.findByTestId('episode-view-tabs');
    setSearchParamsSpy.mockClear();
    fireEvent.click(tabs.querySelector('[data-view="canvas"]')!);

    await waitFor(() => expect(setSearchParamsSpy).toHaveBeenCalled());
    const lastCall = setSearchParamsSpy.mock.calls[setSearchParamsSpy.mock.calls.length - 1];
    const updater = lastCall[0] as (prev: URLSearchParams) => URLSearchParams;
    // Simulate a URL that still carries a consumed deep-link's `shot`.
    const result = updater(new URLSearchParams('module=storyboard&shot=900'));
    expect(result.get('view')).toBe('canvas');
    expect(result.has('shot')).toBe(false);
  });

  // Shot-nodes-on-canvas Task 5 (2026-08-11, supersedes Task 3 修复轮2; the
  // embedded-editor destination this superseded was fully retired in Task
  // 6): a scene board shot-card click switches EpisodeStoryboardPage to its
  // OWN Canvas tab and focuses the shot there.
  it("a shot-card click switches to this page's own Canvas tab and focuses the shot there (no embedded editor)", async () => {
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
    mockSceneService.listShots.mockResolvedValue([
      {
        id: '9007199254740997',
        scene_id: '200',
        shot_number: 1,
        shot_type: 'WIDE',
        camera_angle: 'EYE',
        camera_movement: 'STATIC',
        focal_length: '35mm',
        lighting: null,
        description: '',
        image_url: null,
        thumbnail_url: null,
        video_url: null,
        status: 'empty',
        sort_order: 0,
      },
    ]);
    render(<ProjectWorkspace project={PROJECT} teamId="t1" onBack={noop} />);

    await openStoryboardModule();
    const shotCard = await screen.findByTestId('shot-card-9007199254740997');
    fireEvent.click(shotCard);

    const embed = await screen.findByTestId('mock-storyboard-canvas-embed');
    expect(embed).toHaveAttribute('data-focus-shot-id', '9007199254740997');
    expect(embed).toHaveAttribute('data-episode-id', '1');
    // The embedded editor is never mounted by this path.
    expect(screen.queryByTestId('mock-editor-shell')).toBeNull();
  });

  // Entry point ② (shot-nodes-on-canvas Task 5): `?shot=<id>` always focuses
  // the shot in the storyboard CANVAS now — Task 6 retired the embedded
  // editor's storyboard rail this used to alternate with depending on
  // `view=canvas`, so `?view=canvas&shot=` and a bare `?shot=` converge on
  // the same destination (see the next test).
  it('URL ?view=canvas&shot= focuses the shot in the embedded storyboard canvas', async () => {
    mockSearchParams.current = new URLSearchParams('module=storyboard&view=canvas&shot=9007199254740997');
    mockScriptService.fetchScriptProjects.mockResolvedValue({
      data: [
        { id: 's1', name: 'Draft', status: 'active', created_at: '', updated_at: '2026-07-01T00:00:00Z', episode_id: '1' },
      ],
      total: 1,
    });
    render(<ProjectWorkspace project={PROJECT} teamId="t1" onBack={noop} />);

    await screen.findByTestId('mock-storyboard-canvas-embed');
    // `canvasFocusShotId` only populates once `currentEpisode` resolves
    // (the shot-URL effect's own guard) — wait for that async leg rather
    // than asserting on the embed's very first (pre-resolve) render.
    await waitFor(() =>
      expect(screen.getByTestId('mock-storyboard-canvas-embed')).toHaveAttribute(
        'data-focus-shot-id',
        '9007199254740997',
      ),
    );
    expect(screen.queryByTestId('mock-editor-shell')).toBeNull();

    // True one-shot — the URL's `shot` param is cleared once consumed.
    await waitFor(() => expect(setSearchParamsSpy).toHaveBeenCalled());
    const shotClearingCall = setSearchParamsSpy.mock.calls.find((call) => {
      const updater = call[0] as (prev: URLSearchParams) => URLSearchParams;
      const result = updater(new URLSearchParams('module=storyboard&view=canvas&shot=9007199254740997'));
      return !result.has('shot');
    });
    expect(shotClearingCall).toBeTruthy();
  });

  // URL `?shot=` deep-link (an agent panel or a shared link may carry
  // `shot=<id>` without a `scene` or `view=canvas`). Task 6 (shot-nodes-on-
  // canvas epic) retired the OLD editor-deep-link destination this used to
  // reach without `view=canvas` — every bare `?shot=` now lands on the same
  // canvas focus as the test above, and still clears itself from the URL
  // once consumed (true one-shot).
  it('URL ?shot= (no view=canvas) also focuses the shot in the embedded storyboard canvas and clears itself from the URL', async () => {
    mockSearchParams.current = new URLSearchParams('module=storyboard&shot=9007199254740997');
    mockScriptService.fetchScriptProjects.mockResolvedValue({
      data: [
        { id: 's1', name: 'Draft', status: 'active', created_at: '', updated_at: '2026-07-01T00:00:00Z', episode_id: '1' },
      ],
      total: 1,
    });
    render(<ProjectWorkspace project={PROJECT} teamId="t1" onBack={noop} />);

    await waitFor(() =>
      expect(screen.getByTestId('mock-storyboard-canvas-embed')).toHaveAttribute(
        'data-focus-shot-id',
        '9007199254740997',
      ),
    );
    expect(screen.queryByTestId('mock-editor-shell')).toBeNull();

    await waitFor(() => expect(setSearchParamsSpy).toHaveBeenCalled());
    const shotClearingCall = setSearchParamsSpy.mock.calls.find((call) => {
      const updater = call[0] as (prev: URLSearchParams) => URLSearchParams;
      const result = updater(new URLSearchParams('module=storyboard&shot=9007199254740997'));
      return !result.has('shot');
    });
    expect(shotClearingCall).toBeTruthy();
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

  // Storyboard is now its own standalone module (IA redesign Task 2, building
  // on PR-A's 三视图主工作面) — a bare click (sidebar 分镜 row, or a
  // workflow-strip node — see the next test) routes to EpisodeStoryboardPage
  // instead of diving straight into the embedded editor.
  it('lands on the Storyboard module — not the embedded editor — when 分镜 is clicked', async () => {
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

    expect(await screen.findByTestId('scene-column-200')).toBeInTheDocument();
    expect(screen.getByTestId('episode-view-tabs')).toBeInTheDocument();
    expect(mockSceneService.listScenes).toHaveBeenCalledWith('s1');
    expect(screen.queryByTestId('mock-editor-shell')).toBeNull();
    expect(navigate).not.toHaveBeenCalled();
  });

  // UI polish fix #3 (2026-08-11): the 分镜 sidebar row's active highlight
  // used to be gated on `isScript && activeWorkView === 'storyboard'` — a
  // holdover from when Storyboard was a `script` embedded-editor rail view.
  // Since Storyboard became its own top-level `activeModule` (IA redesign
  // Task 2), that condition could never be true again: `activeWorkView` is
  // hard-null whenever `activeModule !== 'script'`, so the row never lit up
  // (剧本 kept its highlight — the accordion click screenshot bug report).
  it('highlights the 分镜 row (not 剧本) once Storyboard is the active module', async () => {
    mockScriptService.fetchScriptProjects.mockResolvedValue({
      data: [
        { id: 's1', name: 'Draft', status: 'active', created_at: '', updated_at: '2026-07-01T00:00:00Z', episode_id: '1' },
      ],
      total: 1,
    });
    mockWorkflowService.fetchProjectWorkflow.mockResolvedValue(storyboardWorkflow());
    mockSceneService.listScenes.mockResolvedValue([]);
    render(<ProjectWorkspace project={PROJECT} teamId="t1" onBack={noop} />);

    await expandEpisodesTree();
    const storyboardRow = await screen.findByTestId('ws-ep-storyboard');
    const scriptRow = screen.getByTestId('ws-ep-script');
    // Neither row is active before anything is clicked.
    expect(storyboardRow.className).not.toContain('accent-soft');
    expect(scriptRow.className).not.toContain('accent-soft');

    fireEvent.click(storyboardRow);
    await screen.findByTestId('episode-view-tabs');

    expect(storyboardRow.className).toContain('accent-soft');
    expect(scriptRow.className).not.toContain('accent-soft');
  });

  // IA redesign Task 4: the workflow strip moved from the always-mounted,
  // whole-project WorkflowSection into the Overview accordion's expanded-row
  // body, and node clicks no longer route anywhere by themselves — a click
  // now only writes URL `node=` (`WorkspaceOverview`'s `onSelectNode`); what
  // "selected" means is Task 5's `EpisodeNodeCard` job (via the
  // `renderNodeCard` slot, still a placeholder here). This replaces the old
  // "clicking the current node's storyboard-surface capsule routes to the
  // Storyboard module" pin — that auto-navigation is retired.
  it('clicking a workflow-strip node writes URL node= and does not auto-navigate anywhere', async () => {
    mockWorkflowService.fetchProjectWorkflow.mockResolvedValue(storyboardWorkflow());
    render(<ProjectWorkspace project={PROJECT} teamId="t1" onBack={noop} />);

    setSearchParamsSpy.mockClear();
    fireEvent.click(await screen.findByTestId('workflow-strip-node'));

    await waitFor(() => expect(setSearchParamsSpy).toHaveBeenCalled());
    const lastCall = setSearchParamsSpy.mock.calls[setSearchParamsSpy.mock.calls.length - 1];
    const updater = lastCall[0] as (prev: URLSearchParams) => URLSearchParams;
    const result = updater(new URLSearchParams('ep=1'));
    expect(result.get('node')).toBe('1');
    expect(screen.queryByTestId('scene-column-200')).toBeNull();
    expect(screen.queryByTestId('mock-editor-shell')).toBeNull();
  });

  // Task 7 (小尾巴 A, 2026-08-09 拍板) used to decouple "current node → surface
  // panel" from "non-current node → its own Stage Board" — both of those
  // navigation paths are now retired by Task 4 (see the test above): EVERY
  // node click, current or not, is just a URL `node=` write. Kept as its own
  // test to pin that a non-current node's click still doesn't fall back to
  // the old Stage Board route either.
  it('clicking a non-current workflow-strip node also only writes URL node=, never opens a Stage Board or surface panel', async () => {
    const currentNode = stageNode({ id: '1', name: 'Storyboard', status: 'in_progress', surface: 'storyboard' });
    const otherNode = stageNode({ id: '2', name: 'Script Pass 2', status: 'pending', surface: 'script' });
    mockWorkflowService.fetchProjectWorkflow.mockResolvedValue({
      has_workflow: true,
      current_node_id: currentNode.id,
      agents_active: 0,
      nodes: [currentNode, otherNode],
    });
    render(<ProjectWorkspace project={PROJECT} teamId="t1" onBack={noop} />);

    const nodeButtons = await screen.findAllByTestId('workflow-strip-node');
    const otherButton = nodeButtons.find((el) => el.getAttribute('data-node-id') === '2');
    expect(otherButton).toBeTruthy();
    setSearchParamsSpy.mockClear();
    fireEvent.click(otherButton!);

    await waitFor(() => expect(setSearchParamsSpy).toHaveBeenCalled());
    const lastCall = setSearchParamsSpy.mock.calls[setSearchParamsSpy.mock.calls.length - 1];
    const updater = lastCall[0] as (prev: URLSearchParams) => URLSearchParams;
    const result = updater(new URLSearchParams('ep=1'));
    expect(result.get('node')).toBe('2');
    expect(mockWorkflowService.fetchStageBoard).not.toHaveBeenCalled();
    expect(screen.queryByTestId('workspace-stage-board')).toBeNull();
    expect(screen.queryByTestId('episode-view-tabs')).toBeNull();
    expect(screen.queryByTestId('mock-editor-shell')).toBeNull();
    expect(mockScriptService.createScriptProject).not.toHaveBeenCalled();
  });

  // Bug A fix (2026-08-11 拍板): `handleSelectNode` — the card's
  // `onEnterSurface` handler — used to have an early-return gate that forced
  // EVERY non-cursor node into its Stage Board before the surface `switch`
  // ever ran, even though `EpisodeNodeCard`'s entry-button LABEL already read
  // `resolveSurface(node)` unconditionally (Task 5). Net effect: a Done,
  // non-cursor Script node showed "Open Script" but clicking it landed on the
  // Stage Board. Product decision: the button does what it says — route
  // purely by surface, cursor or not (distinct from the `workflow-strip-node`
  // click tested above, which only ever writes URL `node=` and never
  // navigates by itself).
  it('a non-cursor Script-surface node\'s entry click opens the script editor, not the Stage Board', async () => {
    mockScriptService.fetchScriptProjects.mockResolvedValue({
      data: [
        { id: 's1', name: 'Draft', status: 'active', created_at: '', updated_at: '2026-07-01T00:00:00Z', episode_id: '1' },
      ],
      total: 1,
    });
    const currentNode = stageNode({ id: '1', name: 'Storyboard', status: 'in_progress', surface: 'storyboard' });
    const scriptNode = stageNode({ id: '2', name: 'Script Pass 2', status: 'done', surface: 'script' });
    mockWorkflowService.fetchProjectWorkflow.mockResolvedValue({
      has_workflow: true,
      current_node_id: currentNode.id,
      agents_active: 0,
      nodes: [currentNode, scriptNode],
    });
    // Select the non-cursor Script node's card via URL `node=` (mirrors how
    // WorkspaceOverview's `renderNodeCard` resolves `selectedNodeId`).
    mockSearchParams.current = new URLSearchParams('ep=1&node=2');

    // The Overview accordion (with the node card) is what's on screen by
    // default — no expand click needed, unlike the SIDEBAR's separate
    // "Episodes" tree (`expandEpisodesTree`, used elsewhere in this file).
    render(<ProjectWorkspace project={PROJECT} teamId="t1" onBack={noop} />);

    fireEvent.click(await screen.findByTestId('node-card-enter'));

    const shell = await screen.findByTestId('mock-editor-shell');
    expect(shell).toHaveAttribute('data-initial-rail-view', 'script');
    expect(screen.queryByTestId('workspace-stage-board')).toBeNull();
    expect(mockWorkflowService.fetchStageBoard).not.toHaveBeenCalled();
  });

  // Companion pin: a deliverable-only node (surface === null) — cursor or
  // not — is unaffected by the fix above and still falls back to its own
  // Stage Board via the same entry button.
  it('a non-cursor deliverable-only node\'s entry click still opens its own Stage Board', async () => {
    const currentNode = stageNode({ id: '1', name: 'Storyboard', status: 'in_progress', surface: 'storyboard' });
    const deliverableNode = stageNode({
      id: '2',
      name: 'Final Delivery',
      status: 'pending',
      surface: null,
      deliverable_required: true,
      deliverable_label: 'Final Cut',
    });
    mockWorkflowService.fetchProjectWorkflow.mockResolvedValue({
      has_workflow: true,
      current_node_id: currentNode.id,
      agents_active: 0,
      nodes: [currentNode, deliverableNode],
    });
    mockWorkflowService.fetchStageBoard.mockResolvedValue({
      node: deliverableNode,
      issue: null,
      files: [],
    });
    mockSearchParams.current = new URLSearchParams('ep=1&node=2');

    render(<ProjectWorkspace project={PROJECT} teamId="t1" onBack={noop} />);

    fireEvent.click(await screen.findByTestId('node-card-enter'));

    expect(await screen.findByTestId('workspace-stage-board')).toBeInTheDocument();
    expect(screen.queryByTestId('mock-editor-shell')).toBeNull();
  });

  // Task 6 (shot-nodes-on-canvas epic) retired the OLD destination this used
  // to deep-link into (the embedded editor's storyboard rail, focused via
  // studioFocusSceneId/initialFocusSceneId — both since deleted). The click
  // is now handled entirely locally by EpisodeStoryboardPage (see its own
  // test for the scroll assertion) — from ProjectWorkspace's perspective,
  // clicking Open must never mount the embedded editor.
  it('a scene card\'s Open button stays on the Storyboard module (does not mount the embedded editor)', async () => {
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
    // jsdom doesn't implement scrollIntoView at all — EpisodeStoryboardPage's
    // local handleOpenScene calls it on click.
    Element.prototype.scrollIntoView = vi.fn();
    render(<ProjectWorkspace project={PROJECT} teamId="t1" onBack={noop} />);

    await openStoryboardModule();
    await screen.findByTestId('scene-column-200');
    fireEvent.click(screen.getByTestId('ep-scene-open-200'));

    // Still the Storyboard module's view one — no editor mount, no module switch.
    expect(screen.getByTestId('scene-column-200')).toBeInTheDocument();
    expect(screen.queryByTestId('mock-editor-shell')).toBeNull();
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

    await openStoryboardModule();
    await screen.findByTestId('scene-column-200');
    const tabs = screen.getByTestId('episode-view-tabs');
    fireEvent.click(tabs.querySelector('[data-view="shotlist"]')!);

    expect(await screen.findByTestId('ep-shotlist-row-900')).toBeInTheDocument();
    // The table's own built-in Export button is suppressed (hideExport) — the
    // one visible trigger lives in the tabs' actions slot, driven via ref.
    expect(screen.getAllByTestId('ep-shotlist-export')).toHaveLength(1);
    fireEvent.click(screen.getByTestId('ep-shotlist-export'));
    expect(createSpy).toHaveBeenCalledTimes(1);
  });

  // Review fix (Task 3 round 1, Critical), re-mounted on EpisodeStoryboardPage
  // by Task 2 (IA redesign): opening the Storyboard module must not itself
  // provision anything — its scriptId resolution MUST be read-only. This test
  // is the anti-regression pin — zero create calls just from opening the page.
  it('never provisions a script just from opening the Storyboard module — renders the "no script" empty state instead', async () => {
    mockScriptService.fetchScriptProjects.mockResolvedValue({ data: [], total: 0 });
    render(<ProjectWorkspace project={PROJECT} teamId="t1" onBack={noop} />);

    await openStoryboardModule();

    expect(await screen.findByTestId('episode-surface-no-script')).toBeInTheDocument();
    expect(screen.getByTestId('episode-surface-start-storyboard')).toBeInTheDocument();
    expect(screen.queryByTestId('scene-column-200')).toBeNull();
    expect(mockScriptService.createScriptProject).not.toHaveBeenCalled();
  });

  it('clicking "Start Storyboard" provisions a script and the page refreshes to the scene board', async () => {
    mockScriptService.fetchScriptProjects.mockResolvedValue({ data: [], total: 0 });
    mockScriptService.createScriptProject.mockResolvedValue({ id: 'created-sb', name: 'Ep 1 — Pilot' });
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

    await openStoryboardModule();
    fireEvent.click(await screen.findByTestId('episode-surface-start-storyboard'));

    expect(await screen.findByTestId('scene-column-200')).toBeInTheDocument();
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

// Task 7 (shot-nodes-on-canvas epic — keep-alive 显隐切换 + chunk 预加载): the
// studio (EditorShell) and Storyboard (EpisodeStoryboardPage) subtrees used
// to be an EXCLUSIVE ternary — switching modules fully unmounted whichever
// was open. This is the root fix for the reported jank (剧本↔节拍 stayed
// instant because that switch never left EditorShell's own mounted shell;
// 分镜 was a lazy REMOUNTING module) — both now mount once and stay resident,
// toggled only by `display:none`.
describe('ProjectWorkspace — Task 7 keep-alive + preload', () => {
  it('keeps EditorShell mounted (not unmounted) across a Script → Storyboard → Script round trip', async () => {
    mockScriptService.fetchScriptProjects.mockResolvedValue({
      data: [
        { id: 's1', name: 'Draft', status: 'active', created_at: '', updated_at: '2026-07-01T00:00:00Z', episode_id: '1' },
      ],
      total: 1,
    });
    mockWorkflowService.fetchProjectWorkflow.mockResolvedValue(storyboardWorkflow());
    render(<ProjectWorkspace project={PROJECT} teamId="t1" onBack={noop} />);

    await expandEpisodesTree();
    fireEvent.click(await screen.findByTestId('ws-ep-script'));
    await screen.findByTestId('mock-editor-shell');
    expect(mockEditorShellUnmount).not.toHaveBeenCalled();

    // Switch to Storyboard — EditorShell must stay in the DOM, just hidden
    // (display:none), NOT unmounted.
    fireEvent.click(await screen.findByTestId('ws-ep-storyboard'));
    await screen.findByTestId('episode-view-tabs');
    expect(screen.getByTestId('mock-editor-shell')).toBeInTheDocument();
    expect(screen.getByTestId('mock-editor-shell')).not.toBeVisible();
    expect(mockEditorShellUnmount).not.toHaveBeenCalled();
    // Storyboard, meanwhile, is the one visible.
    expect(screen.getByTestId('episode-storyboard-page')).toBeVisible();

    // ...and back to Script — instantly visible again, same mounted instance.
    // (The click still goes through the async resolve chain — same episode,
    // same script, so it resolves near-instantly — but the state update
    // isn't synchronous with the click, hence `waitFor`.)
    fireEvent.click(await screen.findByTestId('ws-ep-script'));
    await waitFor(() => expect(screen.getByTestId('mock-editor-shell')).toBeVisible());
    expect(screen.getByTestId('episode-storyboard-page')).not.toBeVisible();
    expect(mockEditorShellUnmount).not.toHaveBeenCalled();
  });

  it('a hidden (kept-alive but inactive) Storyboard module ignores a shotFocusBus request — no URL write', async () => {
    mockWorkflowService.fetchProjectWorkflow.mockResolvedValue(storyboardWorkflow());
    render(<ProjectWorkspace project={PROJECT} teamId="t1" onBack={noop} />);

    await openStoryboardModule();
    await screen.findByTestId('episode-view-tabs');

    // Switch away — Storyboard goes inactive but stays mounted (see the test
    // above); the module list is reachable without re-expanding the tree.
    fireEvent.click(await screen.findByTestId('ws-module-overview'));
    expect(await screen.findByTestId('ws-overview')).toBeInTheDocument();
    expect(screen.getByTestId('episode-storyboard-page')).not.toBeVisible();

    setSearchParamsSpy.mockClear();
    act(() => requestShotFocus('hidden-bus-shot'));

    // No reaction from the hidden page: no URL write (the tab-switch path
    // that would normally fire one, `handleTabChange` → `onViewChange` →
    // `setSearchParams`, never runs).
    expect(setSearchParamsSpy).not.toHaveBeenCalled();
    // Still on Overview — the hidden page didn't silently jump the writer.
    expect(screen.getByTestId('ws-overview')).toBeInTheDocument();
  });

  it('idle-preloads the EpisodeStoryboardPage chunk after mount', async () => {
    const idleCallback = vi.fn((cb: () => void) => {
      cb();
      return 1;
    });
    const original = (window as { requestIdleCallback?: unknown }).requestIdleCallback;
    (window as { requestIdleCallback?: unknown }).requestIdleCallback = idleCallback;
    try {
      render(<ProjectWorkspace project={PROJECT} teamId="t1" onBack={noop} />);
      await screen.findByTestId('ws-module-overview');
      expect(idleCallback).toHaveBeenCalledTimes(1);
    } finally {
      (window as { requestIdleCallback?: unknown }).requestIdleCallback = original;
    }
  });

  it('falls back to setTimeout when requestIdleCallback is unavailable', async () => {
    const original = (window as { requestIdleCallback?: unknown }).requestIdleCallback;
    delete (window as { requestIdleCallback?: unknown }).requestIdleCallback;
    const setTimeoutSpy = vi.spyOn(window, 'setTimeout');
    try {
      render(<ProjectWorkspace project={PROJECT} teamId="t1" onBack={noop} />);
      await screen.findByTestId('ws-module-overview');
      expect(setTimeoutSpy).toHaveBeenCalled();
    } finally {
      (window as { requestIdleCallback?: unknown }).requestIdleCallback = original;
      setTimeoutSpy.mockRestore();
    }
  });
});

// ── Task 9 (角色门控行内编辑): canEditConfig computation + onPatchNode wiring ──
// EpisodeNodeCard itself holds no optimistic/toast state (see its file-doc
// comment) — the real optimistic-update / 403-revert / generic-error-revert /
// toast / reload-on-success behavior all lives in `handlePatchNode` here, so
// that's what these tests exercise end to end (real EpisodeNodeCard, real
// `ApiError`, mocked `updateProjectNode`/`fetchProjectWorkflow`).
describe('ProjectWorkspace — Task 9 node config inline edit', () => {
  it('project owner sees the owner fact as an editable BUTTON (canEditConfig=true via project.owner_id)', async () => {
    mockWorkflowService.fetchProjectWorkflow.mockResolvedValue(storyboardWorkflow());
    render(<ProjectWorkspace project={PROJECT} teamId="t1" onBack={noop} />);

    const ownerFact = await screen.findByTestId('node-card-owner');
    expect(ownerFact.tagName).toBe('BUTTON');
  });

  it('a non-owner viewer (neither project owner nor this episode\'s owner) sees the owner fact as a read-only <span>', async () => {
    mockWorkflowService.fetchProjectWorkflow.mockResolvedValue(storyboardWorkflow());
    // PROJECT.owner_id is 'u1' (== the mocked currentUserId) — override so
    // neither the project nor the (ownerless, per EPISODES fixture) episode
    // grants edit access.
    render(
      <ProjectWorkspace project={{ ...PROJECT, owner_id: 'someone-else' }} teamId="t1" onBack={noop} />,
    );

    const ownerFact = await screen.findByTestId('node-card-owner');
    expect(ownerFact.tagName).toBe('SPAN');
  });

  it('the current episode\'s own owner (not the project owner) also gets canEditConfig=true', async () => {
    mockWorkflowService.fetchProjectWorkflow.mockResolvedValue(storyboardWorkflow());
    mockProjectsService.fetchEpisodesProgress.mockResolvedValue(
      EPISODES.map((e) => (e.episode_id === '1' ? { ...e, owner_id: 'u1' } : e)),
    );
    render(
      <ProjectWorkspace project={{ ...PROJECT, owner_id: 'someone-else' }} teamId="t1" onBack={noop} />,
    );

    const ownerFact = await screen.findByTestId('node-card-owner');
    expect(ownerFact.tagName).toBe('BUTTON');
  });

  it('selecting an owner PATCHes via updateProjectNode and reloads the workflow on success', async () => {
    mockWorkflowService.fetchProjectWorkflow.mockResolvedValue(storyboardWorkflow());
    mockProjectsService.fetchProjectMembers.mockResolvedValue([
      { project_id: 'p1', user_id: 'alice-uuid', role: 'editor', invited_by: null, email: 'alice@example.com', joined_at: '' },
    ]);
    mockWorkflowService.updateProjectNode.mockResolvedValue(stageNode({ owner_user_id: 'alice-uuid' }));
    render(<ProjectWorkspace project={PROJECT} teamId="t1" onBack={noop} />);

    fireEvent.click(await screen.findByTestId('node-card-owner'));
    fireEvent.click(await screen.findByText('alice@example.com'));

    await waitFor(() =>
      expect(mockWorkflowService.updateProjectNode).toHaveBeenCalledWith('p1', '1', {
        owner_user_id: 'alice-uuid',
        owner_agent_id: null,
      }),
    );
    // Success reloads (existing `reloadWorkflow`) so the strip/card end up
    // reflecting server truth, not just the optimistic local patch.
    await waitFor(() => expect(mockWorkflowService.fetchProjectWorkflow).toHaveBeenCalledTimes(2));
    expect(addToast).not.toHaveBeenCalled();
  });

  it('a 403 node_config_forbidden PATCH reverts the optimistic value and shows the typed forbidden toast', async () => {
    mockWorkflowService.fetchProjectWorkflow.mockResolvedValue(storyboardWorkflow());
    mockProjectsService.fetchProjectMembers.mockResolvedValue([
      { project_id: 'p1', user_id: 'alice-uuid', role: 'editor', invited_by: null, email: 'alice@example.com', joined_at: '' },
    ]);
    mockWorkflowService.updateProjectNode.mockRejectedValue(
      new ApiError('Forbidden', 403, { details: { code: 'node_config_forbidden' } }),
    );
    render(<ProjectWorkspace project={PROJECT} teamId="t1" onBack={noop} />);

    // Empty before — the storyboardWorkflow() node has no owner.
    expect((await screen.findByTestId('node-card-owner')).textContent).toMatch(/assign/i);

    fireEvent.click(screen.getByTestId('node-card-owner'));
    fireEvent.click(await screen.findByText('alice@example.com'));

    // Optimistic: immediately flips to the filled (non-pill) state.
    await waitFor(() => expect(screen.getByTestId('node-card-owner').tagName).toBe('BUTTON'));

    await waitFor(() =>
      expect(addToast).toHaveBeenCalledWith(
        'projects.nodeCard.forbidden',
        'error',
      ),
    );
    // Reverted back to the empty pill — the 403 must not leave the
    // optimistic (wrong) value on screen.
    await waitFor(() =>
      expect(screen.getByTestId('node-card-owner').textContent).toMatch(/assign/i),
    );
    // 修复轮1 (Important #1): the revert path RE-FETCHES server truth
    // (`reloadWorkflow()`) rather than re-merging a captured pre-edit
    // snapshot — see `handlePatchNode`'s file-doc comment. That's the
    // second `fetchProjectWorkflow` call (first was the initial mount).
    expect(mockWorkflowService.fetchProjectWorkflow).toHaveBeenCalledTimes(2);
  });

  it('a non-403 PATCH failure reverts the optimistic value and shows the generic error toast', async () => {
    mockWorkflowService.fetchProjectWorkflow.mockResolvedValue(storyboardWorkflow());
    mockProjectsService.fetchProjectMembers.mockResolvedValue([
      { project_id: 'p1', user_id: 'alice-uuid', role: 'editor', invited_by: null, email: 'alice@example.com', joined_at: '' },
    ]);
    mockWorkflowService.updateProjectNode.mockRejectedValue(new Error('network blip'));
    render(<ProjectWorkspace project={PROJECT} teamId="t1" onBack={noop} />);

    fireEvent.click(await screen.findByTestId('node-card-owner'));
    fireEvent.click(await screen.findByText('alice@example.com'));

    await waitFor(() => expect(addToast).toHaveBeenCalledWith('common.error', 'error'));
    await waitFor(() =>
      expect(screen.getByTestId('node-card-owner').textContent).toMatch(/assign/i),
    );
  });

  // 评审修复轮1 (Important #1): pins the concurrency fix directly — a
  // schedule PATCH succeeds and reloads (server now has the new schedule),
  // then a fast-follow owner PATCH on the SAME node fails. Before the fix,
  // the failure handler re-merged a `prevNode` snapshot captured BEFORE
  // either edit, which would silently wipe the already-confirmed schedule
  // back to empty too — with no toast hinting why. The fix (revert via
  // `reloadWorkflow()` instead of a snapshot merge) means the schedule
  // survives because it re-fetches real server state, which still has it.
  it('a later failing PATCH does not roll back an earlier already-succeeded field on the same node', async () => {
    const baseNode = stageNode({
      id: '1',
      owner_user_id: null,
      planned_start: '2026-08-01',
      planned_due: null,
    });
    const afterSchedule = { ...baseNode, planned_due: '2026-08-10' };
    mockWorkflowService.fetchProjectWorkflow
      .mockResolvedValueOnce({ has_workflow: true, current_node_id: '1', agents_active: 0, nodes: [baseNode] }) // initial mount
      .mockResolvedValueOnce({ has_workflow: true, current_node_id: '1', agents_active: 0, nodes: [afterSchedule] }) // reload after schedule PATCH succeeds
      .mockResolvedValueOnce({ has_workflow: true, current_node_id: '1', agents_active: 0, nodes: [afterSchedule] }); // revert-reload after owner PATCH fails — schedule is untouched server-side
    mockProjectsService.fetchProjectMembers.mockResolvedValue([
      { project_id: 'p1', user_id: 'alice-uuid', role: 'editor', invited_by: null, email: 'alice@example.com', joined_at: '' },
    ]);
    mockWorkflowService.updateProjectNode
      .mockResolvedValueOnce(afterSchedule) // schedule PATCH succeeds
      .mockRejectedValueOnce(new ApiError('Forbidden', 403, { details: { code: 'node_config_forbidden' } })); // owner PATCH fails
    render(<ProjectWorkspace project={PROJECT} teamId="t1" onBack={noop} />);

    // 1) Schedule PATCH: planned_start is already set (from baseNode), so a
    // SINGLE day click completes the range (DateRangePopover's
    // commit-on-complete: pendingStart pre-seeded from the prop, pendingEnd
    // null → this click completes rather than starting a fresh draft).
    fireEvent.click(await screen.findByTestId('node-card-schedule'));
    fireEvent.click(await screen.findByRole('button', { name: '2026-08-10' }));
    await waitFor(() =>
      expect(mockWorkflowService.updateProjectNode).toHaveBeenNthCalledWith(1, 'p1', '1', {
        planned_start: '2026-08-01',
        planned_due: '2026-08-10',
      }),
    );
    await waitFor(() => expect(mockWorkflowService.fetchProjectWorkflow).toHaveBeenCalledTimes(2));
    expect(screen.getByTestId('node-card-schedule').textContent).toContain('2026-08-10');

    // 2) Owner PATCH, on the SAME node, fails.
    fireEvent.click(screen.getByTestId('node-card-owner'));
    fireEvent.click(await screen.findByText('alice@example.com'));
    await waitFor(() => expect(addToast).toHaveBeenCalledWith('projects.nodeCard.forbidden', 'error'));
    await waitFor(() => expect(mockWorkflowService.fetchProjectWorkflow).toHaveBeenCalledTimes(3));

    // The already-confirmed schedule survives — not stomped by a stale
    // pre-both-edits snapshot merge.
    expect(screen.getByTestId('node-card-schedule').textContent).toContain('2026-08-10');
    expect(screen.getByTestId('node-card-owner').textContent).toMatch(/assign/i);
  });
});

// ── Task 10 修复轮1 ───────────────────────────────────────────────────────
describe('ProjectWorkspace — Task 10 修复轮1 (Node Config settings)', () => {
  // Critical #1: `handleOpenNodeSettings` previously only wrote the URL —
  // `activeModule` is a plain `useState` seeded ONCE from the URL on mount
  // (never resynced from `searchParams` afterward), so the address bar
  // changed but the screen didn't. This exercises the REAL click path (the
  // node card's Settings button), not `WorkspaceNodeSettings` fed props
  // directly — the fix must make the click itself switch the module.
  it('clicking a node card\'s Settings button switches the module to Settings\' Node Config tab, node preselected', async () => {
    mockWorkflowService.fetchProjectWorkflow.mockResolvedValue(storyboardWorkflow());
    const { rerender } = render(<ProjectWorkspace project={PROJECT} teamId="t1" onBack={noop} />);

    const callsBefore = setSearchParamsSpy.mock.calls.length;
    fireEvent.click(await screen.findByTestId('node-card-settings'));

    // The screen must actually switch — not just the URL.
    expect(await screen.findByTestId('settings-tabs')).toBeTruthy();
    expect(screen.queryByTestId('ws-overview')).toBeNull();

    // This suite's router mock doesn't auto-apply `setSearchParams` writes
    // onto `mockSearchParams.current` (see the file-header comment on
    // `setSearchParamsSpy`) — replay EVERY updater fired by this click, IN
    // ORDER, the same way a real router would apply them sequentially, then
    // re-render, to verify `WorkspaceNodeSettings`' own deep-link
    // preselection end to end (real click → real URL writes → real
    // re-render). `handleOpenNodeSettings` itself fires one `setSearchParams`
    // call, but `setActiveModule('settings')` ALSO triggers the module-sync
    // `useEffect` (`[activeModule, stageNodeId, setSearchParams]`), which
    // fires a SECOND, later call — picking only the LAST call (as if it were
    // the only one) would silently drop `handleOpenNodeSettings`'s own
    // `tab=nodes`/`ep=`/`node=` writes.
    const newCalls = setSearchParamsSpy.mock.calls.slice(callsBefore);
    for (const call of newCalls) {
      mockSearchParams.current = (call[0] as (prev: URLSearchParams) => URLSearchParams)(
        mockSearchParams.current,
      );
    }
    rerender(<ProjectWorkspace project={PROJECT} teamId="t1" onBack={noop} />);

    expect(screen.getByTestId('settings-tab-nodes')).toHaveAttribute('aria-current', 'true');
    await waitFor(() => {
      expect(screen.getByTestId('node-settings-form')).toHaveTextContent('Storyboard');
    });
  });

  // Important #2: two independent `useProjectWorkflow` instances (this
  // file's own Overview/top-bar one, and `WorkspaceNodeSettings`' own) can
  // both be watching the SAME episode. A node PATCH made through Settings
  // must reload the main instance too, or the Overview/top-bar go stale
  // until some unrelated refetch happens to fire.
  it('a node PATCH in Settings reloads the main workspace instance when the patched episode IS the current one', async () => {
    // ep=1 is both this fixture's default current episode (lowest
    // sort_order) and the URL-seeded Settings episode — same-episode case.
    mockSearchParams.current = new URLSearchParams('module=settings&tab=nodes&ep=1&node=1');
    mockWorkflowService.fetchProjectWorkflow.mockResolvedValue(storyboardWorkflow());
    mockWorkflowService.updateProjectNode.mockResolvedValue(stageNode({ brief: 'Focus on act 2' }));
    render(<ProjectWorkspace project={PROJECT} teamId="t1" onBack={noop} />);

    await screen.findByTestId('node-settings-form');
    // Two instances, both resolved to ep=1: this file's own (Overview/top
    // bar) + WorkspaceNodeSettings' own.
    await waitFor(() => expect(mockWorkflowService.fetchProjectWorkflow).toHaveBeenCalledTimes(2));

    const brief = screen.getByTestId('node-settings-brief');
    fireEvent.change(brief, { target: { value: 'Focus on act 2' } });
    fireEvent.blur(brief);

    await waitFor(() => expect(mockWorkflowService.updateProjectNode).toHaveBeenCalled());
    // +1 for WorkspaceNodeSettings' own `reload()`, +1 for the main
    // instance's `reloadWorkflow()` fired via `onNodePatched` — same
    // episode, so the gate in `handleNodePatchedInSettings` lets it through.
    await waitFor(() => expect(mockWorkflowService.fetchProjectWorkflow).toHaveBeenCalledTimes(4));
  });

  it('a node PATCH in Settings does NOT reload the main workspace instance when the patched episode differs from the current one', async () => {
    mockSearchParams.current = new URLSearchParams('module=settings&tab=nodes&ep=1&node=1');
    mockWorkflowService.fetchProjectWorkflow.mockResolvedValue(storyboardWorkflow());
    mockWorkflowService.updateProjectNode.mockResolvedValue(stageNode({ brief: 'Focus on act 2' }));
    render(<ProjectWorkspace project={PROJECT} teamId="t1" onBack={noop} />);

    await screen.findByTestId('node-settings-form');
    await waitFor(() => expect(mockWorkflowService.fetchProjectWorkflow).toHaveBeenCalledTimes(2));

    // Switch episodes INSIDE Settings only — this diverges
    // `WorkspaceNodeSettings`' own `selectedEpisodeId` from the main
    // workspace's `currentEpisodeId` (which stays '1'; it's only re-derived
    // from the URL once, on the initial episodes fetch — see
    // `ProjectWorkspace`'s own file-doc on `currentEpisodeId`).
    fireEvent.click(screen.getByTestId('node-settings-episode-switch'));
    fireEvent.click(screen.getByText('Ep 2 — Cutdown'));
    // The episode switch itself fires one more fetch (WorkspaceNodeSettings'
    // instance, now on ep=2).
    await waitFor(() => expect(mockWorkflowService.fetchProjectWorkflow).toHaveBeenCalledTimes(3));

    const brief = await screen.findByTestId('node-settings-brief');
    fireEvent.change(brief, { target: { value: 'Focus on act 2' } });
    fireEvent.blur(brief);

    await waitFor(() => expect(mockWorkflowService.updateProjectNode).toHaveBeenCalled());
    // Only +1 (WorkspaceNodeSettings' own reload, for ep=2) — the main
    // instance (still watching ep=1) must NOT reload for a different
    // episode's PATCH.
    await waitFor(() => expect(mockWorkflowService.fetchProjectWorkflow).toHaveBeenCalledTimes(4));
    // Give any wrongly-fired extra reload a chance to show up before asserting it didn't.
    await new Promise((r) => setTimeout(r, 50));
    expect(mockWorkflowService.fetchProjectWorkflow).toHaveBeenCalledTimes(4);
  });
});
