import { render, screen, cleanup } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { TaskCenterRow } from './TaskCenterRow';
import type { UnifiedTask } from '../../contexts/TaskManagerContext';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (k: string) => k }),
}));

// The row only reads mediaToken (for the download href) from auth.
vi.mock('../../contexts/AuthContext', () => ({
  useAuth: () => ({ mediaToken: null }),
}));

afterEach(cleanup);

function agentTask(overrides: Partial<UnifiedTask> = {}): UnifiedTask {
  return {
    id: '340140596649215',
    user_id: 'u1',
    task_type: 'agent',
    status: 'completed',
    title: 'What is in this frame?',
    subtitle: 'A solid deep blue field.',
    progress: 0,
    metadata: { agent_id: 'e7abaa05-4628-4dc9-943d-440928f3625a', agent_name: 'Analyze' },
    created_at: '2026-08-19T03:15:37.852624+00:00',
    ...overrides,
  } as UnifiedTask;
}

const noop = () => {};
const renderRow = (task: UnifiedTask) =>
  render(
    <TaskCenterRow
      task={task}
      onCancel={noop}
      onRetry={noop}
      onOpenResource={noop}
      onOpenDetail={noop}
    />,
  );

describe('TaskCenterRow — agent attribution', () => {
  it('badges the agent that ran, alongside the request and the result', () => {
    const { container } = renderRow(agentTask());
    expect(container.querySelector('[data-testid="agent-name-badge"]')!.textContent).toBe('Analyze');
    expect(screen.getByText('What is in this frame?')).toBeTruthy();
    expect(screen.getByText('A solid deep blue field.')).toBeTruthy();
  });

  it('renders no badge when the run has no agent name', () => {
    const { container } = renderRow(agentTask({ metadata: { agent_id: 'e7abaa05', agent_name: null } }));
    expect(container.querySelector('[data-testid="agent-name-badge"]')).toBeNull();
    // The row itself still renders — a missing name degrades the badge only.
    expect(screen.getByText('What is in this frame?')).toBeTruthy();
  });

  it('gives the agent icon badge the agent semantic color, not the neutral default', () => {
    const { container } = renderRow(agentTask());
    // Scope to the icon container: the name badge carries the same two classes,
    // so an unscoped query would pass even with the taskTypeBg case gone.
    const icon = container.querySelector('.w-8.h-8');
    expect(icon!.className).toContain('bg-agent-soft');
    expect(icon!.className).toContain('text-agent');
    expect(icon!.className).not.toContain('bg-ink-700/50');
  });

  it('leaves non-agent rows unbadged and neutrally colored', () => {
    const { container } = renderRow(
      agentTask({ task_type: 'parse', metadata: { agent_name: 'Analyze' } }),
    );
    expect(container.querySelector('[data-testid="agent-name-badge"]')).toBeNull();
    expect(container.querySelector('.w-8.h-8')!.className).toContain('bg-ink-700/50');
  });
});
