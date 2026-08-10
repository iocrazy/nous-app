/**
 * WorkspaceNodeSettings (IA redesign Task 10) — the Settings module's Node
 * Config tab. Fetches its OWN workflow instance keyed off a local
 * `selectedEpisodeId` (independent of `ProjectWorkspace`'s own
 * `currentEpisodeId`), so switching episodes in this panel never disturbs
 * the main workspace's episode/view/scene/shot state — see the file-doc in
 * WorkspaceNodeSettings.tsx for the full rationale.
 */
import { render, screen, fireEvent, waitFor, cleanup } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { I18nextProvider, initReactI18next } from 'react-i18next';
import { createInstance, type i18n as I18n } from 'i18next';

import enJson from '../../public/locales/en.json';
import { WorkspaceNodeSettings, type WorkspaceNodeSettingsProps } from './WorkspaceNodeSettings';
import { ApiError } from '../../services/apiClient';
import type { EpisodeProgress, ProjectStageNode, ProjectWorkflow } from '../../types';
import type { PersonOption } from '../workflow/OwnerCandidateList';

function makeI18n(): I18n {
  const instance = createInstance();
  instance.use(initReactI18next).init({
    lng: 'en',
    fallbackLng: 'en',
    resources: { en: { translation: enJson } },
    interpolation: { escapeValue: false },
  });
  return instance;
}

const mockWorkflowService = vi.hoisted(() => ({
  fetchProjectWorkflow: vi.fn(),
  updateProjectNode: vi.fn(),
  normalizeInstanceNode: (n: unknown) => n,
}));
vi.mock('../../services/workflowService', () => mockWorkflowService);

const mockProjectsService = vi.hoisted(() => ({
  updateEpisode: vi.fn(),
}));
vi.mock('../../services/projectsService', () => mockProjectsService);

// Mirrors ProjectWorkspace.test.tsx's own idiom (mock the whole `../Toast`
// module and assert on the `addToast` spy directly) rather than rendering a
// real ToastProvider — ToastItem has no `role="alert"` to query against.
const addToast = vi.fn();
vi.mock('../Toast', () => ({
  useOptionalToast: () => ({ addToast }),
  useToast: () => ({ addToast }),
}));

const EPISODES: EpisodeProgress[] = [
  {
    episode_id: 'ep1',
    title: 'Ep 1 — Pilot',
    sort_order: 10,
    owner_id: 'alice-uuid',
    script_count: 1,
    scene_count: 4,
    shots_total: 12,
    shots_done: 9,
    renders_count: 1,
    status: 'boarding',
  },
  {
    episode_id: 'ep2',
    title: 'Ep 2 — Cutdown',
    sort_order: 20,
    owner_id: null,
    script_count: 1,
    scene_count: 2,
    shots_total: 4,
    shots_done: 0,
    renders_count: 0,
    status: 'drafting',
  },
];

function makeNode(overrides: Partial<ProjectStageNode> = {}): ProjectStageNode {
  return {
    id: 'n1',
    project_id: 'p1',
    source_template_node_id: null,
    legacy_stage_id: null,
    name: 'Script',
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
    deliverable_file_count: 0,
    skipped: false,
    surface: 'script',
    members: [],
    completion_policy: 'owner',
    events: { notify_on_arrival: true, notify_on_complete: false, suggest_agent_run: false },
    brief: '',
    ...overrides,
  } as ProjectStageNode;
}

const NODE_A = makeNode({ id: 'n1', name: 'Script', status: 'in_progress' });
const NODE_B = makeNode({
  id: 'n2',
  name: 'Storyboard',
  sort_order: 1,
  status: 'pending',
  surface: 'storyboard',
  deliverable_label: 'Shot list',
});
const NODE_SKIPPED = makeNode({
  id: 'n3',
  name: 'Review',
  sort_order: 2,
  status: 'skipped',
  skipped: true,
  surface: null,
});

const WORKFLOW_EP1: ProjectWorkflow = {
  has_workflow: true,
  current_node_id: 'n1',
  agents_active: 0,
  nodes: [NODE_A, NODE_B, NODE_SKIPPED],
};

const WORKFLOW_EP2: ProjectWorkflow = {
  has_workflow: true,
  current_node_id: 'e2n1',
  agents_active: 0,
  nodes: [makeNode({ id: 'e2n1', name: 'Draft', status: 'in_progress' })],
};

const PEOPLE: PersonOption[] = [
  { id: 'alice-uuid', name: 'Alice' },
  { id: 'bob-uuid', name: 'Bob' },
];

function renderPanel(overrides: Partial<WorkspaceNodeSettingsProps> = {}) {
  const base: WorkspaceNodeSettingsProps = {
    projectId: 'p1',
    episodes: EPISODES,
    initialEpisodeId: null,
    initialNodeId: null,
    canEditFor: () => true,
    canAssignEpisodeOwner: true,
    people: PEOPLE,
    ...overrides,
  };
  const utils = render(
    <I18nextProvider i18n={makeI18n()}>
      <WorkspaceNodeSettings {...base} />
    </I18nextProvider>,
  );
  return { ...utils, props: base };
}

beforeEach(() => {
  mockWorkflowService.fetchProjectWorkflow.mockReset();
  mockWorkflowService.updateProjectNode.mockReset();
  mockProjectsService.updateEpisode.mockReset();
  addToast.mockReset();
  mockWorkflowService.fetchProjectWorkflow.mockImplementation((_projectId: string, episodeId: string) =>
    Promise.resolve(episodeId === 'ep2' ? WORKFLOW_EP2 : WORKFLOW_EP1),
  );
});

afterEach(() => cleanup());

describe('WorkspaceNodeSettings', () => {
  it('renders the node strip for the selected episode; a skipped node cannot be selected', async () => {
    renderPanel();
    // Wait for the FULL settle (node-settings-form, not just workflow-strip)
    // — the strip commits one render before the "re-pick node" effect
    // resolves `selectedNodeId`/mounts the form's fields; interacting with a
    // form field (e.g. BriefField's local state) before that later render
    // settles gets clobbered by it.
    await screen.findByTestId('node-settings-form');
    const skippedCapsule = screen
      .getAllByTestId('workflow-strip-node')
      .find((el) => el.getAttribute('data-node-id') === 'n3')!;
    fireEvent.click(skippedCapsule);
    // Selection stays on the default (cursor node n1) — clicking the skipped
    // node is a no-op, never switches the form onto it.
    expect(screen.getByTestId('node-settings-form')).toHaveTextContent('Script');
    expect(screen.getByTestId('node-settings-form')).not.toHaveTextContent('Review');
  });

  it('clicking a node shows only that node’s form', async () => {
    renderPanel();
    // Wait for the FULL settle (node-settings-form, not just workflow-strip)
    // — the strip commits one render before the "re-pick node" effect
    // resolves `selectedNodeId`/mounts the form's fields; interacting with a
    // form field (e.g. BriefField's local state) before that later render
    // settles gets clobbered by it.
    await screen.findByTestId('node-settings-form');
    expect(screen.getByTestId('node-settings-form')).toHaveTextContent('Script');

    const nodeB = screen
      .getAllByTestId('workflow-strip-node')
      .find((el) => el.getAttribute('data-node-id') === 'n2')!;
    fireEvent.click(nodeB);

    const form = screen.getByTestId('node-settings-form');
    expect(form).toHaveTextContent('Storyboard');
    expect(form).not.toHaveTextContent('Script');
    // Task 10 form fields: owner / members / schedule / deliverable / brief.
    expect(screen.getByTestId('node-settings-schedule')).toBeTruthy();
    expect(screen.getByTestId('node-settings-brief')).toBeTruthy();
    expect(form).toHaveTextContent('Shot list');
  });

  it('deep-link initialNodeId preselects that node without a click', async () => {
    renderPanel({ initialEpisodeId: 'ep1', initialNodeId: 'n2' });
    await waitFor(() => {
      expect(screen.getByTestId('node-settings-form')).toHaveTextContent('Storyboard');
    });
  });

  it('deep-link initialEpisodeId preselects that episode', async () => {
    renderPanel({ initialEpisodeId: 'ep2' });
    await waitFor(() => {
      expect(mockWorkflowService.fetchProjectWorkflow).toHaveBeenCalledWith('p1', 'ep2');
    });
    await waitFor(() => {
      expect(screen.getByTestId('node-settings-form')).toHaveTextContent('Draft');
    });
  });

  it('episode owner row is a button (editable) when canAssignEpisodeOwner is true', async () => {
    renderPanel({ initialEpisodeId: 'ep1', canAssignEpisodeOwner: true });
    // Wait for the FULL settle (node-settings-form, not just workflow-strip)
    // — the strip commits one render before the "re-pick node" effect
    // resolves `selectedNodeId`/mounts the form's fields; interacting with a
    // form field (e.g. BriefField's local state) before that later render
    // settles gets clobbered by it.
    await screen.findByTestId('node-settings-form');
    const ownerEl = screen.getByTestId('node-settings-episode-owner');
    expect(ownerEl.tagName).toBe('BUTTON');
    expect(ownerEl.textContent).toContain('Alice');
  });

  it('episode owner row is read-only text when canAssignEpisodeOwner is false', async () => {
    renderPanel({ initialEpisodeId: 'ep1', canAssignEpisodeOwner: false });
    // Wait for the FULL settle (node-settings-form, not just workflow-strip)
    // — the strip commits one render before the "re-pick node" effect
    // resolves `selectedNodeId`/mounts the form's fields; interacting with a
    // form field (e.g. BriefField's local state) before that later render
    // settles gets clobbered by it.
    await screen.findByTestId('node-settings-form');
    const ownerEl = screen.getByTestId('node-settings-episode-owner');
    expect(ownerEl.tagName).toBe('SPAN');
    expect(ownerEl.textContent).toContain('Alice');
  });

  it('picking a candidate PATCHes updateEpisode(owner_id) and bubbles onEpisodeOwnerChanged', async () => {
    mockProjectsService.updateEpisode.mockResolvedValue(undefined);
    const onEpisodeOwnerChanged = vi.fn();
    renderPanel({ initialEpisodeId: 'ep1', canAssignEpisodeOwner: true, onEpisodeOwnerChanged });
    // Wait for the FULL settle (node-settings-form, not just workflow-strip)
    // — the strip commits one render before the "re-pick node" effect
    // resolves `selectedNodeId`/mounts the form's fields; interacting with a
    // form field (e.g. BriefField's local state) before that later render
    // settles gets clobbered by it.
    await screen.findByTestId('node-settings-form');

    fireEvent.click(screen.getByTestId('node-settings-episode-owner'));
    fireEvent.click(await screen.findByText('Bob'));

    await waitFor(() => {
      expect(mockProjectsService.updateEpisode).toHaveBeenCalledWith('ep1', { owner_id: 'bob-uuid' });
    });
    await waitFor(() => expect(onEpisodeOwnerChanged).toHaveBeenCalledWith('ep1', 'bob-uuid'));
  });

  it('a 403 episode_owner_forbidden PATCH surfaces a typed toast, not a silent no-op', async () => {
    mockProjectsService.updateEpisode.mockRejectedValue(
      new ApiError('Forbidden', 403, { details: { code: 'episode_owner_forbidden' } }),
    );
    renderPanel({ initialEpisodeId: 'ep1', canAssignEpisodeOwner: true });
    // Wait for the FULL settle (node-settings-form, not just workflow-strip)
    // — the strip commits one render before the "re-pick node" effect
    // resolves `selectedNodeId`/mounts the form's fields; interacting with a
    // form field (e.g. BriefField's local state) before that later render
    // settles gets clobbered by it.
    await screen.findByTestId('node-settings-form');

    fireEvent.click(screen.getByTestId('node-settings-episode-owner'));
    fireEvent.click(await screen.findByText('Bob'));

    await waitFor(() =>
      expect(addToast).toHaveBeenCalledWith(
        'Only the project owner can assign the episode owner',
        'error',
      ),
    );
  });

  it('node owner/members/schedule/brief PATCH via updateProjectNode when canEditFor is true', async () => {
    mockWorkflowService.updateProjectNode.mockResolvedValue(NODE_A);
    renderPanel({ initialEpisodeId: 'ep1', canEditFor: () => true });
    await screen.findByTestId('node-settings-form');

    const field = screen.getByTestId('node-settings-brief') as HTMLTextAreaElement;
    fireEvent.change(field, { target: { value: 'Focus on act 2' } });
    fireEvent.blur(field);

    await waitFor(() => {
      expect(mockWorkflowService.updateProjectNode).toHaveBeenCalledWith('p1', 'n1', {
        brief: 'Focus on act 2',
      });
    });
  });

  it('node form fields are disabled when canEditFor(episodeId) is false', async () => {
    renderPanel({ initialEpisodeId: 'ep1', canEditFor: () => false });
    // Wait for the FULL settle (node-settings-form, not just workflow-strip)
    // — the strip commits one render before the "re-pick node" effect
    // resolves `selectedNodeId`/mounts the form's fields; interacting with a
    // form field (e.g. BriefField's local state) before that later render
    // settles gets clobbered by it.
    await screen.findByTestId('node-settings-form');
    expect(screen.getByTestId('node-settings-brief')).toBeDisabled();
    expect(screen.getByTestId('node-settings-schedule')).toBeDisabled();
  });

  it('switching episodes via the switcher re-fetches that episode’s workflow', async () => {
    renderPanel({ initialEpisodeId: 'ep1' });
    // Wait for the FULL settle (node-settings-form, not just workflow-strip)
    // — the strip commits one render before the "re-pick node" effect
    // resolves `selectedNodeId`/mounts the form's fields; interacting with a
    // form field (e.g. BriefField's local state) before that later render
    // settles gets clobbered by it.
    await screen.findByTestId('node-settings-form');
    expect(screen.getByTestId('node-settings-form')).toHaveTextContent('Script');

    fireEvent.click(screen.getByTestId('node-settings-episode-switch'));
    fireEvent.click(screen.getByText('Ep 2 — Cutdown'));

    await waitFor(() => {
      expect(mockWorkflowService.fetchProjectWorkflow).toHaveBeenCalledWith('p1', 'ep2');
    });
    await waitFor(() => {
      expect(screen.getByTestId('node-settings-form')).toHaveTextContent('Draft');
    });
  });

  it('fires onEpisodeChange/onNodeChange so a host can mirror the selection into the URL', async () => {
    const onEpisodeChange = vi.fn();
    const onNodeChange = vi.fn();
    renderPanel({ initialEpisodeId: 'ep1', onEpisodeChange, onNodeChange });
    // Wait for the FULL settle (node-settings-form, not just workflow-strip)
    // — the strip commits one render before the "re-pick node" effect
    // resolves `selectedNodeId`/mounts the form's fields; interacting with a
    // form field (e.g. BriefField's local state) before that later render
    // settles gets clobbered by it.
    await screen.findByTestId('node-settings-form');

    const nodeB = screen
      .getAllByTestId('workflow-strip-node')
      .find((el) => el.getAttribute('data-node-id') === 'n2')!;
    fireEvent.click(nodeB);
    expect(onNodeChange).toHaveBeenCalledWith('n2');

    fireEvent.click(screen.getByTestId('node-settings-episode-switch'));
    fireEvent.click(screen.getByText('Ep 2 — Cutdown'));
    expect(onEpisodeChange).toHaveBeenCalledWith('ep2');
  });

  // ── Task 10 修复轮1 (Important #2): onNodePatched carries the episode id ──
  // so a host holding its OWN separate `useProjectWorkflow` instance for the
  // same episode (e.g. `ProjectWorkspace`'s Overview/top-bar one) knows to
  // reload it too — this panel's own `reload()` only refreshes ITS instance.
  it('a successful node PATCH fires onNodePatched with the patched node\'s episode id', async () => {
    mockWorkflowService.updateProjectNode.mockResolvedValue(NODE_A);
    const onNodePatched = vi.fn();
    renderPanel({ initialEpisodeId: 'ep1', canEditFor: () => true, onNodePatched });
    await screen.findByTestId('node-settings-form');

    const field = screen.getByTestId('node-settings-brief') as HTMLTextAreaElement;
    fireEvent.change(field, { target: { value: 'Focus on act 2' } });
    fireEvent.blur(field);

    await waitFor(() => expect(onNodePatched).toHaveBeenCalledWith('ep1'));
  });

  it('a FAILED node PATCH does not fire onNodePatched', async () => {
    mockWorkflowService.updateProjectNode.mockRejectedValue(
      new ApiError('Forbidden', 403, { details: { code: 'node_config_forbidden' } }),
    );
    const onNodePatched = vi.fn();
    renderPanel({ initialEpisodeId: 'ep1', canEditFor: () => true, onNodePatched });
    await screen.findByTestId('node-settings-form');

    const field = screen.getByTestId('node-settings-brief') as HTMLTextAreaElement;
    fireEvent.change(field, { target: { value: 'Focus on act 2' } });
    fireEvent.blur(field);

    await waitFor(() => expect(mockWorkflowService.updateProjectNode).toHaveBeenCalled());
    expect(onNodePatched).not.toHaveBeenCalled();
  });

  // ── Task 10 修复轮1: cold deep-link mount (episodes arrive AFTER mount) ──
  it('resolves the initial episode once `episodes` arrives, even if it was still empty at mount (cold deep-link)', async () => {
    const utils = render(
      <I18nextProvider i18n={makeI18n()}>
        <WorkspaceNodeSettings
          projectId="p1"
          episodes={[]}
          initialEpisodeId="ep1"
          initialNodeId={null}
          canEditFor={() => true}
          canAssignEpisodeOwner={false}
          people={PEOPLE}
        />
      </I18nextProvider>,
    );
    // Nothing to show yet — `episodes` is empty, `selectedEpisodeId` can't
    // resolve, `useProjectWorkflow` skips its fetch on a null episode id.
    expect(screen.queryByTestId('node-settings-form')).toBeNull();
    expect(mockWorkflowService.fetchProjectWorkflow).not.toHaveBeenCalled();

    // The host's episodes fetch resolves — a prop update, same component
    // instance (mirrors ProjectWorkspace's real `episodes` state flow).
    utils.rerender(
      <I18nextProvider i18n={makeI18n()}>
        <WorkspaceNodeSettings
          projectId="p1"
          episodes={EPISODES}
          initialEpisodeId="ep1"
          initialNodeId={null}
          canEditFor={() => true}
          canAssignEpisodeOwner={false}
          people={PEOPLE}
        />
      </I18nextProvider>,
    );

    await waitFor(() => expect(mockWorkflowService.fetchProjectWorkflow).toHaveBeenCalledWith('p1', 'ep1'));
    await screen.findByTestId('node-settings-form');
  });
});
