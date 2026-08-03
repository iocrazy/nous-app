import type { TaskStatus } from '../../contexts/TaskManagerContext';
import { taskAwaitingInput } from './taskRowPresentation';

// Pure summary logic for the Task Center panel header: how many tasks are
// running / queued / completed / failed / waiting on the user. Kept separate
// so the counts the user reads at a glance are unit tested.

export interface CountableTask {
  status: TaskStatus;
  /** task_tracking.metadata — read for the awaiting_input marker. */
  metadata?: Record<string, unknown>;
}

export interface TaskCounts {
  /** processing — actively executing right now. */
  running: number;
  /** pending — waiting in the queue. */
  queued: number;
  completed: number;
  /** failed + cancelled, grouped (both are terminal non-success). */
  failed: number;
  /** Active rows suspended on the needs_input gate — the agent is waiting for
   * the user's answer. Overlaps running/queued (a waiting row is still an
   * active row); surfaced separately because it needs the user, not time. */
  waiting: number;
}

/**
 * Tally tasks by status. `extraRunning` folds in client-side activity that
 * isn't a task_tracking row yet (e.g. in-flight uploads tracked by
 * UploadContext) so the "running" count matches what the user sees.
 */
export function summarizeTasks(tasks: CountableTask[], extraRunning = 0): TaskCounts {
  const counts: TaskCounts = {
    running: extraRunning, queued: 0, completed: 0, failed: 0, waiting: 0,
  };
  for (const t of tasks) {
    switch (t.status) {
      case 'processing': counts.running++; break;
      case 'pending':    counts.queued++; break;
      case 'completed':  counts.completed++; break;
      case 'failed':
      case 'cancelled':  counts.failed++; break;
    }
    if (taskAwaitingInput(t)) counts.waiting++;
  }
  return counts;
}

/** A task is "active" (belongs on the Active tab) while pending or processing. */
export function isActiveStatus(status: TaskStatus): boolean {
  return status === 'pending' || status === 'processing';
}
