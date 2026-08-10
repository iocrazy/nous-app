/**
 * WorkflowSection — empty-state "Set up workflow" CTA (M1.x opt-in migration
 * path). For a No-workflow project (`has_workflow=false`) a writer sees an
 * empty-state card with an "Attach workflow" action that opens
 * AttachWorkflowModal; a non-writer sees nothing (unchanged prior behavior —
 * this component used to `return null` outright for any No-workflow project).
 */
import { render, screen, fireEvent, waitFor, cleanup } from '@testing-library/react';
import { describe, expect, it, vi, afterEach } from 'vitest';
import { I18nextProvider, initReactI18next } from 'react-i18next';
import { createInstance, type i18n as I18n } from 'i18next';

import enJson from '../../public/locales/en.json';
import { WorkflowSection } from './WorkflowSection';
import { ToastProvider } from '../Toast';
import { ApiError } from '../../services/apiClient';
import type { ProjectWorkflow } from '../../types';

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

// WorkflowSection itself uses addProjectNode/deleteProjectNode/fetchStageLibrary
// /startEarlyNode; AttachWorkflowModal (mounted inside the empty state) uses
// fetchTemplates/attachProjectWorkflow — both import from this same module.
const mockWorkflowService = vi.hoisted(() => ({
  addProjectNode: vi.fn(),
  deleteProjectNode: vi.fn(),
  fetchStageLibrary: vi.fn().mockResolvedValue([]),
  startEarlyNode: vi.fn(),
  fetchTemplates: vi.fn(),
  attachProjectWorkflow: vi.fn(),
}));
vi.mock('../../services/workflowService', () => mockWorkflowService);

const mockProjectsService = vi.hoisted(() => ({
  fetchProjectMembers: vi.fn().mockResolvedValue([]),
}));
vi.mock('../../services/projectsService', () => mockProjectsService);

const mockAiLibraryService = vi.hoisted(() => ({
  aiLibraryService: { listAgents: vi.fn().mockResolvedValue([]) },
}));
vi.mock('../../services/aiLibraryService', () => mockAiLibraryService);

const EMPTY_WORKFLOW: ProjectWorkflow = {
  has_workflow: false,
  current_node_id: null,
  agents_active: 0,
  nodes: [],
};

const NODE_WORKFLOW: ProjectWorkflow = {
  has_workflow: true,
  current_node_id: 'n1',
  agents_active: 0,
  nodes: [
    {
      id: 'n1',
      project_id: '500',
      source_template_node_id: null,
      legacy_stage_id: null,
      name: 'Script',
      sort_order: 0,
      parallel_group: null,
      status: 'pending',
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
    } as ProjectWorkflow['nodes'][number],
  ],
};

function renderSection(
  overrides: {
    canWrite?: boolean;
    canArrange?: boolean;
    workflow?: ProjectWorkflow;
    onReload?: () => void;
  } = {},
) {
  const onReload = overrides.onReload ?? vi.fn();
  render(
    <I18nextProvider i18n={makeI18n()}>
      <ToastProvider>
        <WorkflowSection
          projectId="500"
          teamId="42"
          workflow={overrides.workflow ?? EMPTY_WORKFLOW}
          canWrite={overrides.canWrite ?? true}
          canArrange={overrides.canArrange}
          onReload={onReload}
          onRequestAdvance={() => undefined}
          onOpenTodolist={() => undefined}
          focusNodeId={null}
        />
      </ToastProvider>
    </I18nextProvider>,
  );
  return { onReload };
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe('WorkflowSection empty state', () => {
  it('renders nothing for a non-writer on a No-workflow project', () => {
    renderSection({ canWrite: false });
    expect(screen.queryByTestId('workflow-empty-state')).toBeNull();
  });

  it('shows the "Set up workflow" CTA for a writer on a No-workflow project', () => {
    renderSection({ canWrite: true });
    expect(screen.getByTestId('workflow-empty-state')).toBeInTheDocument();
    expect(screen.getByTestId('workflow-empty-state-attach')).toBeInTheDocument();
  });

  it('opens AttachWorkflowModal, loads templates for the project team, and attaches the chosen one', async () => {
    mockWorkflowService.fetchTemplates.mockResolvedValue([
      { id: 'tpl-1', name: 'Short-form', node_count: 5, is_default: true },
      { id: 'tpl-2', name: 'Long-form', node_count: 11, is_default: false },
    ]);
    mockWorkflowService.attachProjectWorkflow.mockResolvedValue([
      { id: 'n1', name: 'Script' },
    ]);

    const { onReload } = renderSection({ canWrite: true });

    fireEvent.click(screen.getByTestId('workflow-empty-state-attach'));
    expect(screen.getByTestId('attach-workflow-modal')).toBeInTheDocument();
    await waitFor(() => expect(mockWorkflowService.fetchTemplates).toHaveBeenCalledWith('42'));

    // Defaults to the is_default template.
    await waitFor(() =>
      expect(screen.getByTestId('attach-workflow-template-tpl-1')).toBeInTheDocument(),
    );
    fireEvent.click(screen.getByTestId('attach-workflow-template-tpl-2'));
    fireEvent.click(screen.getByTestId('attach-workflow-submit'));

    await waitFor(() =>
      expect(mockWorkflowService.attachProjectWorkflow).toHaveBeenCalledWith('500', {
        template_id: 'tpl-2',
        method: 'hybrid',
      }),
    );
    await waitFor(() => expect(onReload).toHaveBeenCalled());
    // The modal closes itself on success.
    expect(screen.queryByTestId('attach-workflow-modal')).toBeNull();
  });
});

// ── 评审修复轮 (Important #4): node arrangement is project-owner-only ────────
describe('WorkflowSection node arrangement gate', () => {
  it('canArrange=false hides the Add-stage capsule even when canWrite is true', () => {
    renderSection({ canWrite: true, canArrange: false, workflow: NODE_WORKFLOW });
    expect(screen.queryByTestId('workflow-add-stage')).toBeNull();
  });

  it('canArrange defaults to canWrite (true) — Add-stage shows for a writer', () => {
    renderSection({ canWrite: true, workflow: NODE_WORKFLOW });
    expect(screen.getByTestId('workflow-add-stage')).toBeInTheDocument();
  });

  it('a 403 arrangement_forbidden on add-node surfaces the typed toast, not the generic addFailed one', async () => {
    mockWorkflowService.addProjectNode.mockRejectedValue(
      new ApiError('Forbidden', 403, { code: 'arrangement_forbidden' }),
    );
    mockWorkflowService.fetchStageLibrary.mockResolvedValue([]);
    renderSection({ canWrite: true, canArrange: true, workflow: NODE_WORKFLOW });

    fireEvent.click(screen.getByTestId('workflow-add-stage'));
    await waitFor(() => expect(screen.getByTestId('workflow-blank-stage-input')).toBeInTheDocument());
    fireEvent.change(screen.getByTestId('workflow-blank-stage-input'), {
      target: { value: 'Voiceover' },
    });
    fireEvent.click(screen.getByTestId('workflow-add-blank-stage'));

    await waitFor(() =>
      expect(screen.getByText(enJson.projects.workflow.arrangementForbidden)).toBeInTheDocument(),
    );
  });
});
