/**
 * StageBriefMirror — the mirror-issue-side read-only echo of a workflow
 * node's `brief` while it's in_review (M4 Autopilot task O1/O2/O3).
 */
import { render, screen, waitFor } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { I18nextProvider, initReactI18next } from 'react-i18next';
import { createInstance, type i18n as I18n } from 'i18next';

import enJson from '../../public/locales/en.json';
import { StageBriefMirror } from './StageBriefMirror';
import type { ProjectStageNode, StageBoardData } from '../../types';

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
  fetchStageBoard: vi.fn(),
}));
vi.mock('../../services/workflowService', () => mockWorkflowService);

function node(over: Partial<ProjectStageNode> = {}): ProjectStageNode {
  return {
    id: '1',
    project_id: '10',
    source_template_node_id: null,
    legacy_stage_id: null,
    name: 'Script',
    sort_order: 0,
    parallel_group: null,
    status: 'in_review',
    owner_user_id: null,
    owner_agent_id: null,
    planned_start: null,
    planned_due: null,
    review_required: true,
    deliverable_required: false,
    deliverable_label: null,
    skipped: false,
    members: [],
    completion_policy: 'owner',
    events: { notify_on_arrival: true, notify_on_complete: false, suggest_agent_run: false },
    brief: 'Check pacing in act 2.',
    ...over,
  };
}

function renderMirror() {
  return render(
    <I18nextProvider i18n={makeI18n()}>
      <StageBriefMirror projectId="10" nodeId="1" />
    </I18nextProvider>,
  );
}

beforeEach(() => {
  mockWorkflowService.fetchStageBoard.mockReset();
});

describe('StageBriefMirror', () => {
  it('renders the brief when the node is in_review with a non-empty brief', async () => {
    mockWorkflowService.fetchStageBoard.mockResolvedValue(
      { node: node(), issue: null, files: [] } satisfies StageBoardData,
    );
    renderMirror();
    expect(await screen.findByTestId('issue-stage-brief-pinned')).toHaveTextContent(
      'Check pacing in act 2.',
    );
  });

  it('renders nothing when the node is not in_review', async () => {
    mockWorkflowService.fetchStageBoard.mockResolvedValue(
      { node: node({ status: 'in_progress' }), issue: null, files: [] } satisfies StageBoardData,
    );
    renderMirror();
    await waitFor(() => expect(mockWorkflowService.fetchStageBoard).toHaveBeenCalled());
    expect(screen.queryByTestId('issue-stage-brief-pinned')).toBeNull();
  });

  it('renders nothing when in_review but the brief is empty', async () => {
    mockWorkflowService.fetchStageBoard.mockResolvedValue(
      { node: node({ brief: '' }), issue: null, files: [] } satisfies StageBoardData,
    );
    renderMirror();
    await waitFor(() => expect(mockWorkflowService.fetchStageBoard).toHaveBeenCalled());
    expect(screen.queryByTestId('issue-stage-brief-pinned')).toBeNull();
  });

  it('renders nothing when the fetch fails', async () => {
    mockWorkflowService.fetchStageBoard.mockRejectedValue(new Error('network down'));
    renderMirror();
    await waitFor(() => expect(mockWorkflowService.fetchStageBoard).toHaveBeenCalled());
    expect(screen.queryByTestId('issue-stage-brief-pinned')).toBeNull();
  });
});
