import { render, screen, cleanup, fireEvent, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { ActiveTaskCard } from './ActiveTaskCard';
import type { UnifiedTask } from '../../contexts/TaskManagerContext';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (k: string, arg?: unknown) => (arg && typeof arg === 'object' ? `${k}:${Object.values(arg as Record<string, unknown>).join(',')}` : k),
  }),
}));
const deliverSteer = vi.fn();
vi.mock('../../services/agentInboxService', async (importOriginal) => {
  const mod = await importOriginal<typeof import('../../services/agentInboxService')>();
  return { ...mod, deliverSteer: (...args: unknown[]) => deliverSteer(...args) };
});

const pauseIssue = vi.fn();
vi.mock('../../services/issuesService', async (importOriginal) => {
  const mod = await importOriginal<typeof import('../../services/issuesService')>();
  return { ...mod, pauseIssue: (...args: unknown[]) => pauseIssue(...args) };
});

afterEach(cleanup);

// A run in flight: agentRunToTask maps agent_runs 'running' → 'processing',
// and an agent run has no flow_id, so FlowTaskList draws it with
// ActiveTaskCard — not TaskCenterRow. This is the row the user watches.
function runningAgentTask(overrides: Partial<UnifiedTask> = {}): UnifiedTask {
  return {
    id: '340140596649215',
    user_id: 'u1',
    task_type: 'agent',
    status: 'processing',
    title: 'What is in this frame?',
    progress: 0,
    metadata: { agent_id: 'e7abaa05-4628-4dc9-943d-440928f3625a', agent_name: 'Analyze' },
    created_at: '2026-08-19T03:15:37.852624+00:00',
    started_at: '2026-08-19T03:15:37.852624+00:00',
    ...overrides,
  } as UnifiedTask;
}

const renderCard = (task: UnifiedTask) =>
  render(<ActiveTaskCard task={task} now={Date.parse(task.started_at!) + 4000} onCancel={() => {}} />);

describe('ActiveTaskCard — agent attribution while the run is executing', () => {
  it('badges the running agent run with the agent name', () => {
    const { container } = renderCard(runningAgentTask());
    const badge = container.querySelector('[data-testid="agent-name-badge"]');
    expect(badge).not.toBeNull();
    expect(badge!.textContent).toBe('Analyze');
    expect(screen.getByText('What is in this frame?')).toBeTruthy();
  });

  it('renders no badge when the running run has no agent name', () => {
    const { container } = renderCard(
      runningAgentTask({ metadata: { agent_id: 'e7abaa05', agent_name: null } }),
    );
    expect(container.querySelector('[data-testid="agent-name-badge"]')).toBeNull();
    expect(screen.getByText('What is in this frame?')).toBeTruthy();
  });

  it('never badges a non-agent running task', () => {
    const { container } = renderCard(
      runningAgentTask({ task_type: 'download', metadata: { agent_name: 'Analyze' } }),
    );
    expect(container.querySelector('[data-testid="agent-name-badge"]')).toBeNull();
  });
});

describe('ActiveTaskCard — agent step progress (harness phase 2)', () => {
  const snap = {
    todos: [
      { id: 1, content: 'Outline', status: 'completed', active_form: null },
      { id: 2, content: 'Draft scene 2', status: 'in_progress', active_form: 'Drafting scene 2' },
    ],
    counts: { total: 7, completed: 3, in_progress: 1 },
  };

  it('renders "3/7 · Drafting scene 2" from the todo snapshot', () => {
    const { container } = renderCard(
      runningAgentTask({ metadata: { agent_name: 'Analyze', todos: snap } }),
    );
    const el = container.querySelector('[data-testid="todo-progress"]');
    expect(el).not.toBeNull();
    expect(el!.textContent).toBe('3/7 · Drafting scene 2');
  });

  it('draws nothing for a run that never kept a list', () => {
    const { container } = renderCard(runningAgentTask());
    expect(container.querySelector('[data-testid="agent-progress"]')).toBeNull();
  });

  it('shows the live retry wait, then the past-tense form once elapsed', () => {
    const started = '2026-08-19T03:15:37.852624+00:00';
    const at = new Date(Date.parse(started) + 1000).toISOString();
    const task = runningAgentTask({
      metadata: { agent_name: 'Analyze', last_retry: { attempt: 2, max_retries: 4, delay_ms: 5000, at } },
    });
    // now = started + 4000 → 2.0s of the 5s backoff remain
    const live = renderCard(task);
    expect(live.container.querySelector('[data-testid="retry-progress"]')!.textContent).toBe(
      'Retry 2/4 · waiting 2.0s',
    );
    cleanup();
    const later = render(
      <ActiveTaskCard task={task} now={Date.parse(started) + 60_000} onCancel={() => {}} />,
    );
    expect(later.container.querySelector('[data-testid="retry-progress"]')!.textContent).toBe(
      'Retried 2/4',
    );
  });
});


describe('ActiveTaskCard — cockpit line + steer (harness P4 T11)', () => {
  const view = { v: 1, phase: 'running', step: { done: 3, total: 7, label: 'Drafting' }, current: { turn: 1, step: 4, model: 'm' }, retry: null, context: { used_pct: 62, window: 128000 }, blocked: null, children: { total: 0, done: 0 }, ended: null, inbox_pending: 0, budget: { pct: 82, state: 'warn', spent_cents: 82 }, revision: 5 };

  it('reads turn/step, context and budget through the selectors', () => {
    const { container } = renderCard(runningAgentTask({ metadata: { agent_name: 'Analyze', view } }));
    const line = container.querySelector('[data-testid="agent-cockpit"]')!;
    expect(line.textContent).toContain('turn 1 · step 4');
    expect(line.textContent).toContain('ctx 62%');
    expect(container.querySelector('[data-testid="agent-budget"]')!.textContent).toBe('topbar.budgetUsed:82');
    // todo progress still comes view-first
    expect(container.querySelector('[data-testid="todo-progress"]')!.textContent).toBe('3/7 · Drafting');
  });

  it('draws no cockpit line for an old row without view', () => {
    const { container } = renderCard(runningAgentTask());
    expect(container.querySelector('[data-testid="agent-cockpit"]')).toBeNull();
    expect(container.querySelector('[data-testid="agent-steer"]')).toBeNull(); // no target either
  });

  it('steer posts to the issue when the run serves one, else the conversation; never for a finished run', async () => {
    deliverSteer.mockResolvedValue({ id: '1' });
    const { fireEvent, waitFor } = await import('@testing-library/react');
    const { container } = renderCard(runningAgentTask({ metadata: { agent_name: 'Analyze', agent_issue_id: '48', agent_conversation_id: '9' } }));
    fireEvent.click(container.querySelector('[data-testid="agent-steer-open"]')!);
    const input = container.querySelector('[data-testid="agent-steer-input"]') as HTMLInputElement;
    fireEvent.change(input, { target: { value: 'colder' } });
    fireEvent.submit(input.closest('form')!);
    await waitFor(() => expect(deliverSteer).toHaveBeenCalledWith('issue', '48', 'colder'));
    await waitFor(() => expect(container.textContent).toContain('topbar.steerSent'));
    cleanup();
    const conv = renderCard(runningAgentTask({ metadata: { agent_name: 'Analyze', agent_conversation_id: '9' } }));
    fireEvent.click(conv.container.querySelector('[data-testid="agent-steer-open"]')!);
    fireEvent.change(conv.container.querySelector('[data-testid="agent-steer-input"]')!, { target: { value: 'x' } });
    fireEvent.submit(conv.container.querySelector('form')!);
    await waitFor(() => expect(deliverSteer).toHaveBeenLastCalledWith('conversation', '9', 'x'));
    cleanup();
    const done = renderCard(runningAgentTask({ status: 'completed', metadata: { agent_conversation_id: '9' } }));
    expect(done.container.querySelector('[data-testid="agent-steer"]')).toBeNull();
  });
});

describe('ActiveTaskCard — target-level pause (phase 2a §2)', () => {
  it('offers Pause on an issue-backed run and calls pauseIssue with the issue id', async () => {
    pauseIssue.mockReset().mockResolvedValue({ issue_id: '48', paused_at: '2026-09-08T00:00:00Z', run_id: 'r1' });
    const { container } = renderCard(runningAgentTask({ metadata: { agent_name: 'Analyze', agent_issue_id: '48' } }));
    const btn = container.querySelector('[data-testid="agent-pause"]') as HTMLButtonElement;
    expect(btn).not.toBeNull();
    fireEvent.click(btn);
    await waitFor(() => expect(pauseIssue).toHaveBeenCalledWith(48));
    await waitFor(() => expect(btn.getAttribute('data-state')).toBe('paused'));
    expect(btn.disabled).toBe(true);
  });

  it('marks the button failed when the pause is rejected', async () => {
    pauseIssue.mockReset().mockRejectedValue(new Error('already_paused'));
    const { container } = renderCard(runningAgentTask({ metadata: { agent_name: 'Analyze', agent_issue_id: '48' } }));
    const btn = container.querySelector('[data-testid="agent-pause"]') as HTMLButtonElement;
    fireEvent.click(btn);
    await waitFor(() => expect(btn.getAttribute('data-state')).toBe('failed'));
    expect(btn.disabled).toBe(false);
  });

  it('offers no Pause on a conversation-backed run (conversations have no pause)', () => {
    const { container } = renderCard(runningAgentTask({ metadata: { agent_name: 'Analyze', agent_conversation_id: '9' } }));
    expect(container.querySelector('[data-testid="agent-pause"]')).toBeNull();
  });
});
