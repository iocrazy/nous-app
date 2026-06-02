import type { UnifiedTask } from '../../contexts/TaskManagerContext';

// Pure presentation logic for a task-center row. Kept separate from the React
// component so the branching (which actions/affordances a row exposes) is unit
// testable without a DOM.

/** Whether a row should render the produced resource's cover thumbnail. */
export function taskShowsCover(task: Pick<UnifiedTask, 'status' | 'resource_id'>): boolean {
  // Only completed tasks have a final resource worth previewing; a row with no
  // resource_id (e.g. a failed parse) falls back to its type icon.
  return isTerminalSuccessLike(task.status) && !!task.resource_id;
}

export interface TaskRowActions {
  /** Open the produced resource's detail page. */
  open: boolean;
  /** Download the produced resource's file. */
  download: boolean;
  /** Re-enqueue a failed/cancelled task. */
  retry: boolean;
  /** Cancel an in-flight task. */
  cancel: boolean;
}

/** Which actions a row exposes, derived purely from task state. */
export function taskRowActions(
  task: Pick<UnifiedTask, 'status' | 'resource_id'>,
): TaskRowActions {
  const hasResource = !!task.resource_id;
  const running = task.status === 'pending' || task.status === 'processing';
  const retryable = task.status === 'failed' || task.status === 'cancelled';
  return {
    // A resource may exist even on a partial failure, so gate open/download on
    // the resource itself rather than on success.
    open: hasResource && !running,
    download: hasResource && !running,
    retry: retryable,
    cancel: running,
  };
}

function isTerminalSuccessLike(status: UnifiedTask['status']): boolean {
  return status === 'completed';
}
