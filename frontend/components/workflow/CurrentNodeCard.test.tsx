/**
 * CurrentNodeCard — suggest-agent-run chip (Project Workflow M2, Task E3).
 *
 * The chip is a pure navigation shortcut: it only renders when the node's
 * `events.suggest_agent_run` flag is on AND the node has an agent owner, and
 * clicking it fires the exact same handler as "Open in Todolist" — it never
 * calls a dispatch API itself (that gate lives in the Todolist run-confirm
 * flow). See task-E3-brief.md.
 */
import { render, screen, fireEvent } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { I18nextProvider, initReactI18next } from 'react-i18next';
import { createInstance, type i18n as I18n } from 'i18next';

import enJson from '../../public/locales/en.json';
import { CurrentNodeCard } from './CurrentNodeCard';
import type { AgentOption, PersonOption } from './OwnerPicker';
import type { ProjectStageNode } from '../../types';

// CurrentNodeCard's owner/schedule/brief edits all round-trip through
// updateProjectNode — mocked so a brief blur-save test never hits a real
// fetch (mirrors WorkspaceStageBoard.test.tsx's own workflowService mock).
const mockWorkflowService = vi.hoisted(() => ({
  updateProjectNode: vi.fn(),
}));
vi.mock('../../services/workflowService', () => mockWorkflowService);

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

function node(over: Partial<ProjectStageNode>): ProjectStageNode {
  return {
    id: '1',
    project_id: '10',
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
    skipped: false,
    members: [],
    completion_policy: 'owner',
    events: { notify_on_arrival: true, notify_on_complete: false, suggest_agent_run: false },
    ...over,
  };
}

const people: PersonOption[] = [];
const agents: AgentOption[] = [{ id: 'agent-1', name: 'Script Bot' }];

function renderCard(
  overrides: Partial<ProjectStageNode>,
  opts: {
    onOpenTodolist?: () => void;
    onOpenStage?: (nodeId: string) => void;
    canWrite?: boolean;
    onPatched?: () => void;
    onRequestAdvance?: (direction: 'forward' | 'back') => void;
    isActive?: boolean;
    allNodes?: ProjectStageNode[];
    onStartEarly?: (nodeId: string) => void;
    startEarlyBusy?: boolean;
  } = {},
) {
  const onOpenTodolist = opts.onOpenTodolist ?? vi.fn();
  const onPatched = opts.onPatched ?? vi.fn();
  const onRequestAdvance = opts.onRequestAdvance ?? vi.fn();
  const utils = render(
    <I18nextProvider i18n={makeI18n()}>
      <CurrentNodeCard
        projectId="10"
        node={node(overrides)}
        canWrite={opts.canWrite ?? false}
        people={people}
        agents={agents}
        onPatched={onPatched}
        onRequestAdvance={onRequestAdvance}
        onOpenTodolist={onOpenTodolist}
        onOpenStage={opts.onOpenStage}
        isActive={opts.isActive}
        allNodes={opts.allNodes}
        onStartEarly={opts.onStartEarly}
        startEarlyBusy={opts.startEarlyBusy}
      />
    </I18nextProvider>,
  );
  return { ...utils, onOpenTodolist, onPatched, onRequestAdvance };
}

beforeEach(() => {
  mockWorkflowService.updateProjectNode.mockReset().mockResolvedValue(node({}));
});

describe('CurrentNodeCard — suggest-agent-run chip', () => {
  it('renders with the agent name when both the flag and an agent owner are set', () => {
    renderCard({
      owner_agent_id: 'agent-1',
      events: { notify_on_arrival: false, notify_on_complete: false, suggest_agent_run: true },
    });
    expect(screen.getByTestId('workflow-suggest-agent-chip')).toHaveTextContent(
      'Suggested: run Script Bot',
    );
  });

  it('falls back to the i18n generic-agent copy when the agent owner is not in the known agents list', () => {
    // Regression guard (#final-review E3): this fallback used to be a hardcoded
    // English literal ('agent') interpolated straight into the (possibly
    // Chinese) translated sentence — now it comes from
    // projects.workflow.genericAgent so a zh locale gets a real translation
    // instead of a stray English word.
    renderCard({
      owner_agent_id: 'agent-unknown',
      events: { notify_on_arrival: false, notify_on_complete: false, suggest_agent_run: true },
    });
    expect(screen.getByTestId('workflow-suggest-agent-chip')).toHaveTextContent(
      'Suggested: run Agent',
    );
  });

  it('hides the chip when suggest_agent_run is false, even with an agent owner', () => {
    renderCard({
      owner_agent_id: 'agent-1',
      events: { notify_on_arrival: false, notify_on_complete: false, suggest_agent_run: false },
    });
    expect(screen.queryByTestId('workflow-suggest-agent-chip')).toBeNull();
  });

  it('hides the chip when the flag is on but the owner is a person, not an agent', () => {
    renderCard({
      owner_user_id: 'user-1',
      owner_agent_id: null,
      events: { notify_on_arrival: false, notify_on_complete: false, suggest_agent_run: true },
    });
    expect(screen.queryByTestId('workflow-suggest-agent-chip')).toBeNull();
  });

  it('hides the chip when neither flag nor agent owner is set', () => {
    renderCard({});
    expect(screen.queryByTestId('workflow-suggest-agent-chip')).toBeNull();
  });

  it('clicking the chip fires the same handler as Open in Todolist', () => {
    const onOpenTodolist = vi.fn();
    renderCard(
      {
        owner_agent_id: 'agent-1',
        events: { notify_on_arrival: false, notify_on_complete: false, suggest_agent_run: true },
      },
      { onOpenTodolist },
    );
    fireEvent.click(screen.getByTestId('workflow-suggest-agent-chip'));
    expect(onOpenTodolist).toHaveBeenCalledTimes(1);
  });
});

describe('CurrentNodeCard — Run now chip (M3 Task H3)', () => {
  it('renders the solid Run now button, not the suggest text chip, once metadata.run_prepared_at is set', () => {
    renderCard({
      owner_agent_id: 'agent-1',
      events: {
        notify_on_arrival: false,
        notify_on_complete: false,
        suggest_agent_run: true,
        prepare_agent_run: true,
      },
      metadata: { run_prepared_at: '2026-07-27T00:00:00+00:00' },
    });
    expect(screen.getByTestId('workflow-run-now-chip')).toHaveTextContent('Run now');
    expect(screen.queryByTestId('workflow-suggest-agent-chip')).toBeNull();
  });

  it('keeps the plain suggest text chip when suggest_agent_run is on but the hook has not fired yet (no run_prepared_at)', () => {
    renderCard({
      owner_agent_id: 'agent-1',
      events: {
        notify_on_arrival: false,
        notify_on_complete: false,
        suggest_agent_run: true,
        prepare_agent_run: true,
      },
      // metadata omitted entirely — normalizeInstanceNode would default this to
      // {} in real data; the raw node() fixture leaves it undefined, which the
      // component must tolerate the same way (`node.metadata?.run_prepared_at`).
    });
    expect(screen.getByTestId('workflow-suggest-agent-chip')).toHaveTextContent(
      'Suggested: run Script Bot',
    );
    expect(screen.queryByTestId('workflow-run-now-chip')).toBeNull();
  });

  it('never renders the Run now button when suggest_agent_run is off, even with run_prepared_at set', () => {
    // Guards against a hook race: metadata could in principle still carry a
    // stale run_prepared_at from before the template toggle was flipped off —
    // the base gate (suggest_agent_run && owner_agent_id) must still win.
    renderCard({
      owner_agent_id: 'agent-1',
      events: {
        notify_on_arrival: false,
        notify_on_complete: false,
        suggest_agent_run: false,
        prepare_agent_run: true,
      },
      metadata: { run_prepared_at: '2026-07-27T00:00:00+00:00' },
    });
    expect(screen.queryByTestId('workflow-run-now-chip')).toBeNull();
    expect(screen.queryByTestId('workflow-suggest-agent-chip')).toBeNull();
  });

  it('clicking Run now calls onOpenStage with the node id when the host has wired the Stage Board route — never onOpenTodolist, never dispatch', () => {
    const onOpenStage = vi.fn();
    const onOpenTodolist = vi.fn();
    renderCard(
      {
        id: 'node-42',
        owner_agent_id: 'agent-1',
        events: {
          notify_on_arrival: false,
          notify_on_complete: false,
          suggest_agent_run: true,
          prepare_agent_run: true,
        },
        metadata: { run_prepared_at: '2026-07-27T00:00:00+00:00' },
      },
      { onOpenTodolist, onOpenStage },
    );
    fireEvent.click(screen.getByTestId('workflow-run-now-chip'));
    expect(onOpenStage).toHaveBeenCalledTimes(1);
    expect(onOpenStage).toHaveBeenCalledWith('node-42');
    expect(onOpenTodolist).not.toHaveBeenCalled();
  });

  it('clicking Run now falls back to onOpenTodolist when onOpenStage is not provided (card mounted standalone, no Stage Board route wired)', () => {
    const onOpenTodolist = vi.fn();
    renderCard(
      {
        owner_agent_id: 'agent-1',
        events: {
          notify_on_arrival: false,
          notify_on_complete: false,
          suggest_agent_run: true,
          prepare_agent_run: true,
        },
        metadata: { run_prepared_at: '2026-07-27T00:00:00+00:00' },
      },
      { onOpenTodolist },
    );
    fireEvent.click(screen.getByTestId('workflow-run-now-chip'));
    expect(onOpenTodolist).toHaveBeenCalledTimes(1);
  });
});

describe('CurrentNodeCard — brief (M4 Autopilot task O1/O2/O3)', () => {
  it('seeds the textarea from node.brief', () => {
    renderCard({ brief: 'Focus on the opening hook.' }, { canWrite: true });
    expect(screen.getByTestId('workflow-node-brief')).toHaveValue('Focus on the opening hook.');
  });

  it('saves on blur only when the value actually changed', () => {
    renderCard({ brief: 'Original note' }, { canWrite: true });
    const field = screen.getByTestId('workflow-node-brief');

    // Blur with no edit at all — must not manufacture a PATCH (the exact
    // phantom-save bug StageNodeForm's lastSaved fix guards against).
    fireEvent.blur(field);
    expect(mockWorkflowService.updateProjectNode).not.toHaveBeenCalled();

    fireEvent.change(field, { target: { value: 'Updated note' } });
    fireEvent.blur(field);
    expect(mockWorkflowService.updateProjectNode).toHaveBeenCalledTimes(1);
    expect(mockWorkflowService.updateProjectNode).toHaveBeenCalledWith(
      '10',
      '1',
      { brief: 'Updated note' },
    );
  });

  it('is read-only once the node is done', () => {
    renderCard({ brief: 'Original note', status: 'done' }, { canWrite: true });
    expect(screen.getByTestId('workflow-node-brief')).toBeDisabled();
  });

  it('is read-only once the node is skipped', () => {
    renderCard({ brief: 'Original note', skipped: true }, { canWrite: true });
    expect(screen.getByTestId('workflow-node-brief')).toBeDisabled();
  });

  it('stays editable while the node is in_review', () => {
    renderCard({ brief: 'Original note', status: 'in_review' }, { canWrite: true });
    expect(screen.getByTestId('workflow-node-brief')).not.toBeDisabled();
  });

  it('pins a read-only brief block when the node is in_review with a non-empty brief', () => {
    renderCard({ brief: 'Reviewer: check pacing in act 2.', status: 'in_review' });
    expect(screen.getByTestId('workflow-node-brief-pinned')).toHaveTextContent(
      'Reviewer: check pacing in act 2.',
    );
  });

  it('does not pin when in_review but brief is empty', () => {
    renderCard({ brief: '', status: 'in_review' });
    expect(screen.queryByTestId('workflow-node-brief-pinned')).toBeNull();
  });

  it('does not pin when brief is set but the node is not in_review', () => {
    renderCard({ brief: 'Some note', status: 'in_progress' });
    expect(screen.queryByTestId('workflow-node-brief-pinned')).toBeNull();
  });
});

describe('CurrentNodeCard — Start early (M4 Autopilot task O2/O3)', () => {
  const dep = (over: Partial<ProjectStageNode>) => node({ id: 'dep-1', name: 'Script', ...over });

  it('never renders for the active node (isActive default true), even if otherwise eligible', () => {
    renderCard(
      { id: 'n1', status: 'pending' },
      { canWrite: true, allNodes: [node({ id: 'n1', status: 'pending' })], onStartEarly: vi.fn() },
    );
    expect(screen.queryByTestId('workflow-start-early')).toBeNull();
  });

  it('renders for a future node (isActive=false) whose dependencies are all satisfied', () => {
    renderCard(
      { id: 'n1', status: 'pending', depends_on: ['dep-1'] },
      {
        canWrite: true,
        isActive: false,
        allNodes: [dep({ status: 'done' }), node({ id: 'n1', status: 'pending', depends_on: ['dep-1'] })],
        onStartEarly: vi.fn(),
      },
    );
    expect(screen.getByTestId('workflow-start-early')).toBeInTheDocument();
  });

  it('hides the button when a dependency is not yet satisfied', () => {
    renderCard(
      { id: 'n1', status: 'pending', depends_on: ['dep-1'] },
      {
        canWrite: true,
        isActive: false,
        allNodes: [dep({ status: 'pending' }), node({ id: 'n1', status: 'pending', depends_on: ['dep-1'] })],
        onStartEarly: vi.fn(),
      },
    );
    expect(screen.queryByTestId('workflow-start-early')).toBeNull();
  });

  it('hides the button when the future node is not pending (already started)', () => {
    renderCard(
      { id: 'n1', status: 'in_progress' },
      { canWrite: true, isActive: false, allNodes: [node({ id: 'n1', status: 'in_progress' })], onStartEarly: vi.fn() },
    );
    expect(screen.queryByTestId('workflow-start-early')).toBeNull();
  });

  it('hides the button when the future node is skipped', () => {
    renderCard(
      { id: 'n1', status: 'pending', skipped: true },
      { canWrite: true, isActive: false, allNodes: [node({ id: 'n1', status: 'pending', skipped: true })], onStartEarly: vi.fn() },
    );
    expect(screen.queryByTestId('workflow-start-early')).toBeNull();
  });

  it('hides the button when no onStartEarly handler is provided', () => {
    renderCard(
      { id: 'n1', status: 'pending' },
      { canWrite: true, isActive: false, allNodes: [node({ id: 'n1', status: 'pending' })] },
    );
    expect(screen.queryByTestId('workflow-start-early')).toBeNull();
  });

  it('hides the button when canWrite is false', () => {
    renderCard(
      { id: 'n1', status: 'pending' },
      { canWrite: false, isActive: false, allNodes: [node({ id: 'n1', status: 'pending' })], onStartEarly: vi.fn() },
    );
    expect(screen.queryByTestId('workflow-start-early')).toBeNull();
  });

  it('clicking the button calls onStartEarly with the node id', () => {
    const onStartEarly = vi.fn();
    renderCard(
      { id: 'n1', status: 'pending' },
      { canWrite: true, isActive: false, allNodes: [node({ id: 'n1', status: 'pending' })], onStartEarly },
    );
    fireEvent.click(screen.getByTestId('workflow-start-early'));
    expect(onStartEarly).toHaveBeenCalledWith('n1');
  });

  it('disables the button while startEarlyBusy is true', () => {
    renderCard(
      { id: 'n1', status: 'pending' },
      {
        canWrite: true,
        isActive: false,
        allNodes: [node({ id: 'n1', status: 'pending' })],
        onStartEarly: vi.fn(),
        startEarlyBusy: true,
      },
    );
    expect(screen.getByTestId('workflow-start-early')).toBeDisabled();
  });

  it('hides Back/Complete-stage on a future-node peek card (isActive=false)', () => {
    renderCard(
      { id: 'n1', status: 'pending' },
      { canWrite: true, isActive: false, allNodes: [node({ id: 'n1', status: 'pending' })], onStartEarly: vi.fn() },
    );
    expect(screen.queryByTestId('workflow-back-btn')).toBeNull();
    expect(screen.queryByTestId('workflow-complete-stage')).toBeNull();
  });

  it('keeps Back/Complete-stage for the active node (isActive default true)', () => {
    renderCard({ id: 'n1', status: 'in_progress' }, { canWrite: true });
    expect(screen.getByTestId('workflow-back-btn')).toBeInTheDocument();
    expect(screen.getByTestId('workflow-complete-stage')).toBeInTheDocument();
  });
});
