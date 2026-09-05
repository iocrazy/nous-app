import { render, screen, cleanup, fireEvent } from '@testing-library/react';
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

// ── fan-out flows do not auto-expand (2026-09-05) ────────────────────────────
//
// "Generate 3 images" is one flow of three IDENTICAL siblings. Following the
// in-flight step (the rich live card a parse→download→transcribe pipeline
// wants) drew a second "Generate image" row under the card, and the user read
// it as a second task. A fan-out has nothing to follow step by step: dots only,
// click a dot to open one.
describe('FlowStepCard — fan-out flows', () => {
  it('does not auto-expand a step for a homogeneous fan-out', () => {
    renderFlow([
      task({ task_type: 'canvas_gen', title: 'Generate image', status: 'processing', progress: 10, flow_id: 'f1' }),
      task({ task_type: 'canvas_gen', title: 'Generate image', status: 'pending', progress: 0, flow_id: 'f1' }),
    ]);
    expect(screen.queryByTestId('flow-expanded-step')).toBeNull();
    // Exactly one "Generate image" on screen: the flow title. A second one is
    // the expanded child the user pointed at.
    expect(screen.getAllByText('Generate image')).toHaveLength(1);
  });

  it('still follows the in-flight step of a pipeline (positive control)', () => {
    renderFlow([
      task({ task_type: 'parse', title: 'Parse https://x', subtitle: 'Some video', status: 'processing', progress: 40, flow_id: 'f2' }),
      task({ task_type: 'download', title: 'Download Some video', status: 'pending', progress: 0, flow_id: 'f2' }),
    ]);
    expect(screen.getByTestId('flow-expanded-step')).toBeInTheDocument();
  });

  it('a pinned step still opens on a fan-out (click beats the default)', () => {
    const { flow } = renderFlow([
      task({ task_type: 'canvas_gen', title: 'Generate image', status: 'processing', progress: 10, flow_id: 'f3' }),
      task({ task_type: 'canvas_gen', title: 'Generate image', status: 'pending', progress: 0, flow_id: 'f3' }),
    ]);
    const dots = screen.getAllByRole('button').filter((b) => b.getAttribute('data-step-id'));
    expect(dots.length).toBe(flow.steps.length);
    fireEvent.click(dots[1]);
    expect(screen.getByTestId('flow-expanded-step')).toBeInTheDocument();
  });
});
