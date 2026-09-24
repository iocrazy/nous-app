import { render, cleanup } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { AgentResultBody } from './AgentResultBody';
import type { UnifiedTask } from '../../../contexts/TaskManagerContext';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (_k: string, d?: unknown) => (typeof d === 'string' ? d : _k) }),
}));
const activity = { events: [] as unknown[], denials: [], activities: [], nodes: [], loaded: true };
vi.mock('../../agentActivity/useRunToolActivity', () => ({
  useRunToolActivity: () => activity,
}));

afterEach(cleanup);

const task = (metadata: Record<string, unknown>): UnifiedTask =>
  ({
    id: '1',
    user_id: 'u1',
    task_type: 'agent',
    status: 'completed',
    title: 'Write the pitch',
    progress: 100,
    metadata,
    created_at: '2026-08-27T06:00:00Z',
  }) as UnifiedTask;

describe('AgentResultBody — todo list from view.todos', () => {
  it('lists every step with its status and the counts', () => {
    const { container } = render(
      <AgentResultBody
        task={task({
          agent_output: 'done',
          view: {
            v: 1, phase: 'running', step: { done: 1, total: 3, label: 'Drafting' }, current: null, retry: null,
            context: null, blocked: null, children: { total: 0, done: 0 }, ended: null, inbox_pending: 0,
            budget: null, revision: 2,
            todos: [
              { id: 1, content: 'Outline', status: 'completed', active_form: null },
              { id: 2, content: 'Draft', status: 'in_progress', active_form: 'Drafting' },
              { id: 3, content: 'Polish', status: 'pending', active_form: null },
            ],
          },
        })}
      />,
    );
    const items = container.querySelectorAll('[data-testid="agent-todo-list"] li');
    expect(items.length).toBe(3);
    expect(items[0].getAttribute('data-status')).toBe('completed');
    expect(items[1].textContent).toBe('Drafting'); // active_form wins while in progress
    expect(items[2].textContent).toBe('Polish');
    expect(container.querySelector('[data-testid="agent-todo-list"]')!.textContent).toContain('1/3');
  });

  it('renders no list block when the run never kept one', () => {
    const { container } = render(<AgentResultBody task={task({ agent_output: 'done' })} />);
    expect(container.querySelector('[data-testid="agent-todo-list"]')).toBeNull();
  });

  it('ignores the dropped legacy todos key (framework hardening B)', () => {
    const { container } = render(
      <AgentResultBody
        task={task({
          agent_output: 'done',
          todos: { todos: [{ id: 1, content: 'a', status: 'completed', active_form: null }], counts: { total: 1, completed: 1, in_progress: 0 } },
        })}
      />,
    );
    expect(container.querySelector('[data-testid="agent-todo-list"]')).toBeNull();
  });
});


describe('AgentResultBody — view-first counts + trajectory (harness P4 T11)', () => {
  it('reads counts from view.step and draws the run trajectory', () => {
    activity.events = [
      { seq: 1, event_type: 'step_start', payload: { turn: 1, step: 1 }, created_at: '', turn: 1, step: 1 },
      { seq: 2, event_type: 'tool_call', payload: { tool: 'ReadScene', iteration: 1 }, created_at: '' },
      { seq: 3, event_type: 'step_end', payload: { turn: 1, step: 1 }, created_at: '', turn: 1, step: 1 },
    ];
    const { container } = render(
      <AgentResultBody
        task={task({
          view: { v: 1, phase: 'ended', step: { done: 5, total: 6, label: null }, current: null, retry: null, context: null, blocked: null, children: { total: 0, done: 0 }, ended: { reason: 'completed' }, inbox_pending: 0, budget: null, revision: 3, todos: [{ id: 1, content: 'a', status: 'completed', active_form: null }] },
        })}
      />,
    );
    expect(container.querySelector('[data-testid="agent-todo-list"]')!.textContent).toContain('5/6');
    expect(container.querySelector('[data-testid="agent-trajectory"]')).not.toBeNull();
    expect(container.querySelectorAll('[data-testid="traj-step"]')).toHaveLength(1);
    activity.events = [];
  });
});
