import { render, screen, cleanup, fireEvent } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { FlowGroupCard } from './FlowGroupCard';
import type { TaskGroup } from '../../utils/taskDisplay';
import type { UnifiedTask } from '../../contexts/TaskManagerContext';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (k: string) => k }),
}));
vi.mock('../Toast', () => ({ useToast: () => ({ addToast: vi.fn() }) }));

afterEach(cleanup);

let seq = 0;
function task(overrides: Partial<UnifiedTask>): UnifiedTask {
  seq += 1;
  return {
    id: `g${seq}`,
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

// Same production shape as the panel card's test (flow 3b0918de): one
// transcription plus three ai_summary rows, the last of which succeeded.
// Settings → Tasks defaults to groupBy='flow', so this card is what the user
// sees first — it used to read "2/4 done · 2 failed" with a red bar.
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

function renderCard(tasks: UnifiedTask[], flowId = 'f-incident') {
  const group: TaskGroup = { key: flowId, label: flowId, tasks };
  return render(
    <FlowGroupCard
      group={group}
      flowId={flowId}
      renderTask={(t) => <div key={t.id} data-testid="child-row">{t.title}</div>}
    />,
  );
}

describe('FlowGroupCard — retries count as one step', () => {
  it('counts the incident flow as 2/2 done, no failures', () => {
    renderCard(incidentRows());
    expect(screen.getByText('2/2 done')).toBeTruthy();
    expect(screen.queryByText(/failed/)).toBeNull();
  });

  it('does not paint the progress bar red once the newest attempt succeeded', () => {
    const { container } = renderCard(incidentRows());
    const bar = container.querySelector('[data-testid="flow-progress-bar"]')!;
    expect(bar.className).toContain('bg-emerald-500');
    expect(bar.className).not.toContain('bg-rose-500');
    expect((bar as HTMLElement).style.width).toBe('100%');
  });

  it('surfaces that a step was retried, so the row count still adds up', () => {
    renderCard(incidentRows());
    expect(screen.getByText('• 1 retried')).toBeTruthy();
  });

  it('still shows every attempt as its own row when expanded', () => {
    const { container } = renderCard(incidentRows());
    fireEvent.click(container.querySelector('button')!);
    expect(screen.getAllByTestId('child-row')).toHaveLength(4);
  });

  it('is not a happy-path liar: newest attempt failed → failed count and red bar', () => {
    const { container } = renderCard([
      task({
        task_type: 'ai_summary', flow_id: 'f-bad', resource_id: 'r1',
        status: 'completed', created_at: '2026-08-14T10:00:00Z',
      }),
      task({
        task_type: 'ai_summary', flow_id: 'f-bad', resource_id: 'r1',
        status: 'failed', error_msg: 'provider 500', created_at: '2026-08-14T10:05:00Z',
      }),
    ], 'f-bad');
    expect(screen.getByText('0/1 done')).toBeTruthy();
    expect(screen.getByText('• 1 failed')).toBeTruthy();
    expect(container.querySelector('[data-testid="flow-progress-bar"]')!.className)
      .toContain('bg-rose-500');
  });

  it('keeps a batch fan-out counted per resource (same type, different resources)', () => {
    renderCard(
      Array.from({ length: 4 }, (_, i) =>
        task({
          task_type: 'download', flow_id: 'f-batch', resource_id: `r${i}`,
          status: i === 0 ? 'failed' : 'completed', created_at: `2026-08-14T10:0${i}:00Z`,
        }),
      ),
      'f-batch',
    );
    expect(screen.getByText('3/4 done')).toBeTruthy();
    expect(screen.getByText('• 1 failed')).toBeTruthy();
    expect(screen.queryByText(/retried/)).toBeNull();
  });

  it('keeps cancel-all available while any raw attempt is still in flight', () => {
    renderCard([
      task({
        task_type: 'ai_summary', flow_id: 'f-live', resource_id: 'r1',
        status: 'processing', created_at: '2026-08-14T10:00:00Z',
      }),
      task({
        task_type: 'ai_summary', flow_id: 'f-live', resource_id: 'r1',
        status: 'pending', created_at: '2026-08-14T10:01:00Z',
      }),
    ], 'f-live');
    expect(screen.getByText('Cancel all')).toBeTruthy();
  });
});
