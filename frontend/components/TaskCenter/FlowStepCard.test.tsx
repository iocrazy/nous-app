import { render, screen, cleanup } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { FlowStepCard } from './FlowStepCard';
import { groupTasksByFlow, type FlowItem } from './flowGrouping';
import type { UnifiedTask } from '../../contexts/TaskManagerContext';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (k: string) => k }),
}));
vi.mock('../../contexts/AuthContext', () => ({
  useAuth: () => ({ mediaToken: null }),
}));
vi.mock('../Toast', () => ({ useToast: () => ({ addToast: vi.fn() }) }));

afterEach(cleanup);

let seq = 0;
function task(overrides: Partial<UnifiedTask>): UnifiedTask {
  seq += 1;
  return {
    id: `t${seq}`,
    user_id: 'u1',
    task_type: 'download',
    status: 'completed',
    title: `Task ${seq}`,
    progress: 100,
    metadata: {},
    created_at: `2026-08-14T10:00:${String(seq).padStart(2, '0')}Z`,
    ...overrides,
  } as UnifiedTask;
}

const noop = () => {};

function renderFlow(rows: UnifiedTask[]) {
  const item = groupTasksByFlow(rows)[0];
  if (item.kind !== 'flow') throw new Error('expected a flow item');
  const flow: FlowItem = item;
  const utils = render(
    <FlowStepCard
      flow={flow}
      now={Date.now()}
      onCancel={noop}
      onRetry={noop}
      onOpenResource={noop}
      onOpenDetail={noop}
    />,
  );
  return { ...utils, flow };
}

// Production report, flow 3b0918de: one ai_transcription + THREE ai_summary
// rows (two failures then a success). The card used to draw four circles
// (✓ ✕ ✕ ✓) and keep a failure in the status line even though the last
// attempt succeeded.
const incidentRows = () => [
  task({
    task_type: 'ai_transcription', flow_id: 'f-incident', resource_id: 'r1',
    status: 'completed', created_at: '2026-08-14T10:00:00Z',
  }),
  task({
    task_type: 'ai_summary', flow_id: 'f-incident', resource_id: 'r1',
    status: 'failed', error_msg: 'provider 500', created_at: '2026-08-14T10:01:00Z',
  }),
  task({
    task_type: 'ai_summary', flow_id: 'f-incident', resource_id: 'r1',
    status: 'failed', error_msg: 'provider 500', created_at: '2026-08-14T10:02:00Z',
  }),
  task({
    task_type: 'ai_summary', flow_id: 'f-incident', resource_id: 'r1',
    status: 'completed', created_at: '2026-08-14T10:03:00Z',
  }),
];

describe('FlowStepCard — retried steps read as one step', () => {
  it('draws two step circles for the incident flow, none of them failed', () => {
    const { container } = renderFlow(incidentRows());
    const circles = container.querySelectorAll('[data-testid="flow-step"]');
    expect(circles).toHaveLength(2);
    expect(container.querySelectorAll('[data-step-status="failed"]')).toHaveLength(0);
    expect(container.querySelectorAll('[data-step-status="completed"]')).toHaveLength(2);
  });

  it('marks the retried step with its attempt count', () => {
    const { container } = renderFlow(incidentRows());
    const badges = container.querySelectorAll('[data-testid="flow-step-attempts"]');
    expect(badges).toHaveLength(1);
    expect(badges[0].textContent).toBe('×3');
    // The single-attempt transcription step carries no badge.
    const steps = container.querySelectorAll('[data-testid="flow-step"]');
    expect(steps[0].querySelector('[data-testid="flow-step-attempts"]')).toBeNull();
  });

  it('shows a done status line, not the stale failure text', () => {
    renderFlow(incidentRows());
    expect(screen.getByText('2/2 steps')).toBeTruthy();
    expect(screen.queryByText(/provider 500|failed/i)).toBeNull();
  });

  it('still reports failure when the newest attempt is the failed one', () => {
    const { container } = renderFlow([
      task({
        task_type: 'ai_summary', flow_id: 'f-bad', resource_id: 'r1',
        status: 'completed', created_at: '2026-08-14T10:00:00Z',
      }),
      task({
        task_type: 'ai_summary', flow_id: 'f-bad', resource_id: 'r1',
        status: 'failed', error_msg: 'provider 500', created_at: '2026-08-14T10:05:00Z',
      }),
    ]);
    expect(container.querySelectorAll('[data-step-status="failed"]')).toHaveLength(1);
    expect(container.querySelector('[data-testid="flow-step-attempts"]')!.textContent).toBe('×2');
  });

  it('keeps a batch fan-out as separate steps (same type, different resources)', () => {
    const { container } = renderFlow(
      Array.from({ length: 4 }, (_, i) =>
        task({
          task_type: 'download', flow_id: 'f-batch', resource_id: `r${i}`,
          status: 'completed', created_at: `2026-08-14T10:0${i}:00Z`,
        }),
      ),
    );
    expect(container.querySelectorAll('[data-testid="flow-step"]')).toHaveLength(4);
    expect(container.querySelectorAll('[data-testid="flow-step-attempts"]')).toHaveLength(0);
  });
});
