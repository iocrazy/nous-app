import { render, screen, cleanup } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { ActiveTaskCard } from './ActiveTaskCard';
import type { UnifiedTask } from '../../contexts/TaskManagerContext';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (k: string) => k }),
}));

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
