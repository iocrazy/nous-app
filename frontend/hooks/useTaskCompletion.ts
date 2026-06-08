import { useEffect, useRef } from 'react';
import { useTaskManager, type UnifiedTask } from '../contexts/TaskManagerContext';

interface UseTaskCompletionOptions {
  /** Fired exactly once when the watched task reaches `completed`. */
  onComplete?: (task: UnifiedTask) => void;
  /** Fired exactly once when the watched task reaches `failed` or `cancelled`. */
  onError?: (task: UnifiedTask) => void;
}

export interface UseTaskCompletionResult {
  status?: UnifiedTask['status'];
  task?: UnifiedTask;
}

/**
 * Watch a single async task (by its task_tracking `id` ==
 * dbos_workflow_id) and fire a terminal callback exactly once.
 *
 * Built on top of `useTaskManager().tasks`, which is kept fresh by the
 * task_tracking Realtime subscription. Pass `null` while no task is in
 * flight; pass the dispatched `task_id` to start watching. When `taskId`
 * changes the fire-guard resets so the same hook instance can watch a
 * second dispatch (e.g. "Regenerate").
 *
 * Returns `{ status, task }` so callers can render "Generating…" UI.
 */
export function useTaskCompletion(
  taskId: string | null,
  opts: UseTaskCompletionOptions,
): UseTaskCompletionResult {
  const { tasks } = useTaskManager();
  const task = taskId ? tasks.find((t) => t.id === taskId) : undefined;
  const status = task?.status;

  // Keep the latest callbacks without re-arming the fire effect on every
  // render (callers commonly pass fresh inline closures).
  const optsRef = useRef(opts);
  optsRef.current = opts;

  // One-shot guard: prevents double-firing if the task row updates again
  // after reaching a terminal state. Reset whenever the watched id changes.
  const firedRef = useRef(false);
  useEffect(() => {
    firedRef.current = false;
  }, [taskId]);

  useEffect(() => {
    if (!taskId || !task || firedRef.current) return;
    if (status === 'completed') {
      firedRef.current = true;
      optsRef.current.onComplete?.(task);
    } else if (status === 'failed' || status === 'cancelled') {
      firedRef.current = true;
      optsRef.current.onError?.(task);
    }
  }, [taskId, task, status]);

  return { status, task };
}
