import type { TaskStatus } from '../../contexts/TaskManagerContext';

// Pure summary logic for the Task Center panel header: how many tasks are
// running / queued / completed / failed. Kept separate so the counts the user
// reads at a glance are unit tested.

export interface CountableTask {
  status: TaskStatus;
}

export interface TaskCounts {
  /** processing — actively executing right now. */
  running: number;
  /** pending — waiting in the queue. */
  queued: number;
  completed: number;
  /** failed + cancelled, grouped (both are terminal non-success). */
  failed: number;
}

/**
 * Tally tasks by status. `extraRunning` folds in client-side activity that
 * isn't a task_tracking row yet (e.g. in-flight uploads tracked by
 * UploadContext) so the "running" count matches what the user sees.
 */
export function summarizeTasks(tasks: CountableTask[], extraRunning = 0): TaskCounts {
  const counts: TaskCounts = { running: extraRunning, queued: 0, completed: 0, failed: 0 };
  for (const t of tasks) {
    switch (t.status) {
      case 'processing': counts.running++; break;
      case 'pending':    counts.queued++; break;
      case 'completed':  counts.completed++; break;
      case 'failed':
      case 'cancelled':  counts.failed++; break;
    }
  }
  return counts;
}

/** A task is "active" (belongs on the Active tab) while pending or processing. */
export function isActiveStatus(status: TaskStatus): boolean {
  return status === 'pending' || status === 'processing';
}
