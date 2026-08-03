import type { UnifiedTask } from '../../contexts/TaskManagerContext';
import { classifyFailure } from '../../utils/taskFailure';

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
  task: Pick<UnifiedTask, 'status' | 'resource_id' | 'error_msg'>,
): TaskRowActions {
  const hasResource = !!task.resource_id;
  const running = task.status === 'pending' || task.status === 'processing';
  // 'lost' = worker died (zombie reaper / sweepers) — terminal + retryable,
  // backend retry_task accepts it. Without it a lost row showed no Retry.
  const retryable =
    task.status === 'failed' || task.status === 'cancelled' || task.status === 'lost';
  return {
    // A resource may exist even on a partial failure, so gate open/download on
    // the resource itself rather than on success.
    open: hasResource && !running,
    download: hasResource && !running,
    // Hide Retry on permanent failures (source unavailable / not found) —
    // re-running the same input fails identically, so the button misleads.
    retry: retryable && !classifyFailure(task.error_msg).permanent,
    cancel: running,
  };
}

function isTerminalSuccessLike(status: UnifiedTask['status']): boolean {
  return status === 'completed';
}

/** The awaiting_input marker input_gate writes into task_tracking.metadata
 * while an issue-dispatch workflow is suspended waiting for the user's answer
 * (needs_input first-class, spec 2026-07-30). */
export interface AwaitingInputMarker {
  prompt?: string;
  since?: string;
  issue_id?: number | string;
}

/**
 * The awaiting_input marker for a row that can actually consume an answer, or
 * null. Terminal rows are excluded even when a stale marker survives (a
 * cancel/reap race can leave one behind) — a workflow that already ended can
 * never receive the reply, so highlighting it would be a lie.
 */
export function taskAwaitingInput(task: {
  status: UnifiedTask['status'];
  metadata?: Record<string, unknown>;
}): AwaitingInputMarker | null {
  if (task.status !== 'pending' && task.status !== 'processing') return null;
  const marker = task.metadata?.awaiting_input;
  if (!marker || typeof marker !== 'object' || Array.isArray(marker)) return null;
  return marker as AwaitingInputMarker;
}
