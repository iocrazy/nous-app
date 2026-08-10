/**
 * WorkspaceTopBar — Autopilot chip (M4 Autopilot task O1/O2/O3).
 *
 * The chip is the project-level `autopilot_enabled` master switch: shown
 * only when the host has wired both `projectId` and `autopilotEnabled`
 * through, toggled with an optimistic local flip that reverts if the PATCH
 * fails (this is the topbar's first mutation — no prior topbar pattern to
 * match, so it mirrors AgentMemoriesPanel's capture-before-mutate /
 * revert-on-failure idiom instead).
 */
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { I18nextProvider, initReactI18next } from 'react-i18next';
import { createInstance, type i18n as I18n } from 'i18next';

import enJson from '../../public/locales/en.json';
import { WorkspaceTopBar } from './WorkspaceTopBar';
import { ToastProvider } from '../Toast';
import type { Project, ProjectStageNode, ProjectWorkflow } from '../../types';

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

const mockProjectsService = vi.hoisted(() => ({
  updateProject: vi.fn(),
}));
vi.mock('../../services/projectsService', () => mockProjectsService);

function renderTopBar(
  overrides: {
    projectId?: string;
    autopilotEnabled?: boolean;
    onAutopilotChange?: (enabled: boolean) => void;
    canWrite?: boolean;
  } = {},
) {
  const onAutopilotChange = overrides.onAutopilotChange ?? vi.fn();
  const utils = render(
    <I18nextProvider i18n={makeI18n()}>
      <ToastProvider>
        <WorkspaceTopBar
          projectName="Spring Campaign"
          onBack={vi.fn()}
          canWrite={overrides.canWrite ?? true}
          projectId={overrides.projectId ?? '10'}
          autopilotEnabled={overrides.autopilotEnabled}
          onAutopilotChange={onAutopilotChange}
        />
      </ToastProvider>
    </I18nextProvider>,
  );
  return { ...utils, onAutopilotChange };
}

beforeEach(() => {
  mockProjectsService.updateProject.mockReset();
});

describe('WorkspaceTopBar — Autopilot chip', () => {
  it('renders nothing when autopilotEnabled is not provided (host has not wired it through)', () => {
    render(
      <I18nextProvider i18n={makeI18n()}>
        <WorkspaceTopBar projectName="Spring Campaign" onBack={vi.fn()} projectId="10" />
      </I18nextProvider>,
    );
    expect(screen.queryByTestId('workspace-autopilot-chip')).toBeNull();
  });

  it('renders nothing when projectId is not provided', () => {
    render(
      <I18nextProvider i18n={makeI18n()}>
        <WorkspaceTopBar projectName="Spring Campaign" onBack={vi.fn()} autopilotEnabled />
      </I18nextProvider>,
    );
    expect(screen.queryByTestId('workspace-autopilot-chip')).toBeNull();
  });

  it('renders the "on" state when autopilotEnabled is true', () => {
    renderTopBar({ autopilotEnabled: true });
    expect(screen.getByTestId('workspace-autopilot-chip')).toHaveAttribute('data-autopilot', 'on');
  });

  it('renders the "off" state when autopilotEnabled is false', () => {
    renderTopBar({ autopilotEnabled: false });
    expect(screen.getByTestId('workspace-autopilot-chip')).toHaveAttribute('data-autopilot', 'off');
  });

  it('optimistically flips on click and PATCHes autopilot_enabled', async () => {
    mockProjectsService.updateProject.mockResolvedValue({ autopilot_enabled: false } as Project);
    const onAutopilotChange = vi.fn();
    renderTopBar({ autopilotEnabled: true, onAutopilotChange });

    const chip = screen.getByTestId('workspace-autopilot-chip');
    fireEvent.click(chip);

    // Flips immediately, before the PATCH resolves.
    expect(chip).toHaveAttribute('data-autopilot', 'off');
    expect(mockProjectsService.updateProject).toHaveBeenCalledWith('10', { autopilot_enabled: false });

    await waitFor(() => expect(onAutopilotChange).toHaveBeenCalledWith(false));
  });

  it('reverts the optimistic flip when the PATCH fails', async () => {
    mockProjectsService.updateProject.mockRejectedValue(new Error('network down'));
    renderTopBar({ autopilotEnabled: true });

    const chip = screen.getByTestId('workspace-autopilot-chip');
    fireEvent.click(chip);
    expect(chip).toHaveAttribute('data-autopilot', 'off');

    await waitFor(() => expect(chip).toHaveAttribute('data-autopilot', 'on'));
  });

  it('is disabled when canWrite is false', () => {
    renderTopBar({ autopilotEnabled: true, canWrite: false });
    expect(screen.getByTestId('workspace-autopilot-chip')).toBeDisabled();
  });

  it('re-syncs from a fresh autopilotEnabled prop (e.g. navigating to a different project)', () => {
    const { rerender } = renderTopBar({ autopilotEnabled: true });
    expect(screen.getByTestId('workspace-autopilot-chip')).toHaveAttribute('data-autopilot', 'on');

    rerender(
      <I18nextProvider i18n={makeI18n()}>
        <ToastProvider>
          <WorkspaceTopBar
            projectName="Another Project"
            onBack={vi.fn()}
            canWrite
            projectId="99"
            autopilotEnabled={false}
            onAutopilotChange={vi.fn()}
          />
        </ToastProvider>
      </I18nextProvider>,
    );
    expect(screen.getByTestId('workspace-autopilot-chip')).toHaveAttribute('data-autopilot', 'off');
  });
});

/**
 * Editor flow pill (IA redesign Task 10, spec §6) — studio (slate) mode's
 * `● <node name> · <status>` + Complete Stage capsule, top-right of the
 * top bar. Cursor node = `workflow.current_node_id` resolved against
 * `workflow.nodes`; a stale/foreign cursor id (not in `nodes`) is treated the
 * same as "no workflow" — no pill, not a crash.
 */
describe('WorkspaceTopBar — editor flow pill', () => {
  const NODE: ProjectStageNode = {
    id: 'n1',
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
    members: [],
    completion_policy: 'owner',
    events: { notify_on_arrival: true, notify_on_complete: false, suggest_agent_run: false },
  } as ProjectStageNode;

  const WORKFLOW: ProjectWorkflow = {
    has_workflow: true,
    current_node_id: 'n1',
    agents_active: 0,
    nodes: [NODE],
  };

  function renderWithFlow(overrides: {
    slate?: { ep: number; scene?: number | null } | null;
    workflow?: ProjectWorkflow | null;
    canWrite?: boolean;
    onRequestAdvance?: (direction: 'forward' | 'back') => void;
  } = {}) {
    const onRequestAdvance = overrides.onRequestAdvance ?? vi.fn();
    const utils = render(
      <I18nextProvider i18n={makeI18n()}>
        <WorkspaceTopBar
          projectName="Spring Campaign"
          onBack={vi.fn()}
          canWrite={overrides.canWrite ?? true}
          slate={'slate' in overrides ? overrides.slate! : { ep: 1, scene: 2 }}
          workflow={'workflow' in overrides ? overrides.workflow! : WORKFLOW}
          onRequestAdvance={onRequestAdvance}
        />
      </I18nextProvider>,
    );
    return { ...utils, onRequestAdvance };
  }

  it('shows the flow pill with the cursor node name + status, and a Complete Stage action', () => {
    renderWithFlow();
    const pill = screen.getByTestId('workspace-flow-pill');
    expect(pill.textContent).toContain('Storyboard');
    expect(screen.getByTestId('workspace-flow-pill-complete')).toBeTruthy();
  });

  it('clicking Complete Stage fires onRequestAdvance("forward")', () => {
    const { onRequestAdvance } = renderWithFlow();
    fireEvent.click(screen.getByTestId('workspace-flow-pill-complete'));
    expect(onRequestAdvance).toHaveBeenCalledWith('forward');
  });

  it('no pill when there is no workflow', () => {
    renderWithFlow({ workflow: null });
    expect(screen.queryByTestId('workspace-flow-pill')).toBeNull();
  });

  it('no pill when the workflow cursor node id is not in this episode\'s node list', () => {
    renderWithFlow({ workflow: { ...WORKFLOW, current_node_id: 'not-in-this-episode' } });
    expect(screen.queryByTestId('workspace-flow-pill')).toBeNull();
  });

  it('no pill outside studio (slate) mode, even with a live workflow cursor', () => {
    renderWithFlow({ slate: null });
    expect(screen.queryByTestId('workspace-flow-pill')).toBeNull();
  });

  it('shows the status pill without the Complete Stage button when canWrite is false', () => {
    renderWithFlow({ canWrite: false });
    expect(screen.getByTestId('workspace-flow-pill')).toBeTruthy();
    expect(screen.queryByTestId('workspace-flow-pill-complete')).toBeNull();
  });
});
