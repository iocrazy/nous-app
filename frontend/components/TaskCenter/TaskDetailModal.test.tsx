import { render, screen, cleanup, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { TaskDetailModal } from './TaskDetailModal';
import type { UnifiedTask } from '../../contexts/TaskManagerContext';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (_k: string, d?: string) => d ?? _k }),
}));

// The modal's result fetch is not what these tests are about; stub the AI
// getters so a failed summary task takes the same route it takes in
// production (kind='summary' → getSummaryByResource) without a real API base.
vi.mock('../../services/aiService', () => ({
  getSummaryByResource: vi.fn().mockRejectedValue(new Error('summary not found')),
  getTranscriptByResource: vi.fn().mockResolvedValue(null),
  getVisualAnalysisByResource: vi.fn().mockResolvedValue(null),
}));
vi.mock('../../services/resourceService', () => ({
  fetchResourceById: vi.fn().mockResolvedValue(null),
}));

afterEach(cleanup);

// Verbatim production shape for dbos workflow
// 2daffe85-a0cd-4daa-9cbe-8682c2ca15be: task_type ai_summary WITH a
// resource_id — which is precisely what routed it away from the only body
// that rendered errors.
const failedSummary = (over: Partial<UnifiedTask> = {}): UnifiedTask =>
  ({
    id: '2daffe85-a0cd-4daa-9cbe-8622',
    dbos_workflow_id: '2daffe85-a0cd-4daa-9cbe-8622',
    task_type: 'ai_summary',
    title: 'Summarize',
    status: 'failed',
    phase: 'failed',
    progress: 25,
    resource_id: '339813762409849',
    error_msg:
      'DBOSMaxStepRetriesExceeded: all 1 model(s) failed: doubao-seed-2-0-pro-260215 ' +
      '(HTTPStatusError HTTP 429: {"error":{"code":"SetLimitExceeded"}})',
    metadata: { error_code: 'PROVIDER_QUOTA_CAP' },
    ...over,
  }) as unknown as UnifiedTask;

describe('TaskDetailModal error block', () => {
  it('shows why an ai_summary task failed even though its kind routes to the summary body', async () => {
    render(
      <TaskDetailModal task={failedSummary()} onClose={vi.fn()} onOpenResource={vi.fn()} />,
    );

    expect(
      await screen.findByText(/configured limit for this model/i),
    ).toBeInTheDocument();
    expect(screen.getByText(/provider console/i)).toBeInTheDocument();
  });

  it('keeps the reason visible while the result fetch is still running', () => {
    // The fetch for a result the task never produced must not gate — or
    // replace — the explanation of why it never produced one.
    render(
      <TaskDetailModal task={failedSummary()} onClose={vi.fn()} onOpenResource={vi.fn()} />,
    );

    expect(screen.getByText(/configured limit for this model/i)).toBeInTheDocument();
  });

  it('shows nothing extra for a task that did not fail', async () => {
    render(
      <TaskDetailModal
        task={failedSummary({ status: 'completed', error_msg: null } as Partial<UnifiedTask>)}
        onClose={vi.fn()}
        onOpenResource={vi.fn()}
      />,
    );

    await waitFor(() =>
      expect(screen.queryByText(/configured limit for this model/i)).toBeNull(),
    );
  });
});
