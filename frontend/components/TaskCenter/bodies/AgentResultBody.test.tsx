import { render, cleanup } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { AgentResultBody } from './AgentResultBody';
import type { UnifiedTask } from '../../../contexts/TaskManagerContext';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (_k: string, d?: string) => d ?? _k }),
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

describe('AgentResultBody — todo list (harness phase 2)', () => {
  it('lists every step with its status and the counts', () => {
    const { container } = render(
      <AgentResultBody
        task={task({
          agent_output: 'done',
          todos: {
            todos: [
              { id: 1, content: 'Outline', status: 'completed', active_form: null },
              { id: 2, content: 'Draft', status: 'in_progress', active_form: 'Drafting' },
              { id: 3, content: 'Polish', status: 'pending', active_form: null },
            ],
            counts: { total: 3, completed: 1, in_progress: 1 },
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
});
