/**
 * Pure helpers for Task Center multi-select.
 *
 * Selection is an immutable `Set<string>` of task ids. Only TERMINAL tasks
 * (completed / failed / cancelled) are selectable — in-flight tasks have no
 * batch action. Retry only applies to the non-permanent failed/cancelled
 * subset (a `completed` row can be selected for batch Delete but is skipped
 * by batch Retry).
 */
import type { UnifiedTask, TaskStatus } from '../contexts/TaskManagerContext';
import { classifyFailure } from './taskFailure';

const TERMINAL: readonly TaskStatus[] = ['completed', 'failed', 'cancelled'];

/** A task in a terminal state — eligible for batch select (Retry/Delete). */
export function isTerminal(status: TaskStatus | undefined): boolean {
  return !!status && TERMINAL.includes(status);
}

/** Failed/cancelled and NOT a permanent failure (source gone) → retryable. */
export function isRetryable(task: UnifiedTask): boolean {
  if (task.status !== 'failed' && task.status !== 'cancelled') return false;
  return !classifyFailure(task.error_msg).permanent;
}

/** Toggle one id, returning a NEW set (never mutates the input). */
export function toggleSelection(selected: Set<string>, id: string): Set<string> {
  const next = new Set(selected);
  if (next.has(id)) next.delete(id);
  else next.add(id);
  return next;
}

/** Union the given ids into the selection (new set). */
export function addAll(selected: Set<string>, ids: string[]): Set<string> {
  return new Set([...selected, ...ids]);
}

/** Drop the given ids from the selection (new set). */
export function removeAll(selected: Set<string>, ids: string[]): Set<string> {
  const drop = new Set(ids);
  return new Set([...selected].filter((id) => !drop.has(id)));
}

export interface RetryPartition {
  /** Every selected id that still exists in `tasks`. */
  all: string[];
  /** The retryable subset (drives the Retry button). */
  retryable: string[];
  /** Selected-but-not-retryable count (shown as "N skipped"). */
  skipped: number;
}

/**
 * Split the current selection against the live task list. Ids that no longer
 * exist (deleted out from under us) are dropped silently.
 */
export function partitionForRetry(
  tasks: UnifiedTask[],
  selected: Set<string>,
): RetryPartition {
  const present = tasks.filter((t) => selected.has(t.id));
  const retryable = present.filter(isRetryable).map((t) => t.id);
  return {
    all: present.map((t) => t.id),
    retryable,
    skipped: present.length - retryable.length,
  };
}
