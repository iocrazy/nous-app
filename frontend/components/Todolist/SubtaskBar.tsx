/**
 * SubtaskBar — a compact done/total progress bar for an issue's sub-issues,
 * used in both the list row's Subtasks column and the detail header's Tasks
 * cell. Renders nothing when there are no children (no `0/0` placeholder); goes
 * emerald once every child is terminal.
 */

import { isSubtaskComplete, type SubtaskCount } from './issueFlow';

interface Props {
  count: SubtaskCount;
  /** Slightly larger fill for the detail header. */
  size?: 'row' | 'detail';
}

export function SubtaskBar({ count, size = 'row' }: Props) {
  if (count.total <= 0) return null;

  const complete = isSubtaskComplete(count);
  const pct = Math.round((count.done / count.total) * 100);
  const barWidth = size === 'detail' ? 'w-16' : 'w-10';
  const fillClass = complete ? 'bg-emerald-500' : 'bg-indigo-500';

  return (
    <span
      data-testid="subtask-bar"
      className="inline-flex items-center gap-1.5 shrink-0"
      title={`${count.done} of ${count.total} sub-issues done`}
    >
      <span className={`${barWidth} h-1.5 rounded-full bg-ink-700 overflow-hidden`}>
        <span
          className={`block h-full rounded-full transition-all ${fillClass}`}
          style={{ width: `${pct}%` }}
        />
      </span>
      <span
        className={`text-[11px] tabular-nums whitespace-nowrap ${
          complete ? 'text-emerald-400' : 'text-ink-400'
        }`}
      >
        {count.done}/{count.total}
      </span>
    </span>
  );
}

export default SubtaskBar;
