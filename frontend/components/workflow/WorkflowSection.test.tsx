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

function renderSection(overrides: { canWrite?: boolean; onReload?: () => void } = {}) {
  const onReload = overrides.onReload ?? vi.fn();
  render(
    <I18nextProvider i18n={makeI18n()}>
      <ToastProvider>
        <WorkflowSection
          projectId="500"
          teamId="42"
          workflow={EMPTY_WORKFLOW}
          canWrite={overrides.canWrite ?? true}
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
