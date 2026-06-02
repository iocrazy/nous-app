import React from 'react';
import { X } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import {
  formatSpeed,
  taskTypeIcon,
  taskTypeLabel,
  type UnifiedTask,
} from '../../contexts/TaskManagerContext';
import { formatElapsed } from './taskElapsed';

interface ActiveTaskCardProps {
  task: UnifiedTask;
  /** Shared clock (ms) ticked by the panel, so all cards update in step. */
  now: number;
  onCancel: (id: string) => void;
}

/**
 * Prominent card for a task that is executing right now — pulsing icon, live
 * elapsed time, RUNNING badge, and a progress bar. Used in the Task Center's
 * Active tab to make in-flight work (downloads, parses, AI/agent runs) obvious.
 */
export const ActiveTaskCard: React.FC<ActiveTaskCardProps> = ({ task, now, onCancel }) => {
  const { t } = useTranslation();

  const startedRaw = task.started_at || task.created_at;
  const startedMs = startedRaw ? Date.parse(startedRaw) : now;
  const elapsed = formatElapsed(now - startedMs);
  const pct = Math.max(task.progress || 0, 2);

  return (
    <div className="mx-3 my-2 rounded-xl border border-emerald-500/20 bg-emerald-500/5 p-3">
      <div className="flex items-start gap-3">
        {/* Pulsing status icon */}
        <div className="relative w-9 h-9 shrink-0">
          <span className="absolute inset-0 rounded-full bg-emerald-500/30 animate-ping" />
          <div className="relative w-9 h-9 rounded-full bg-emerald-500/20 text-emerald-300 flex items-center justify-center text-sm">
            {taskTypeIcon(task.task_type)}
          </div>
        </div>

        <div className="flex-1 min-w-0">
          <div className="flex items-center justify-between gap-2">
            <span className="text-xs font-medium text-zinc-100 truncate">{task.title}</span>
            <span className="flex items-center gap-1 shrink-0 px-1.5 py-0.5 rounded-full bg-emerald-500/15 text-emerald-400 text-[10px] font-semibold">
              <span className="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-pulse" />
              {t('topbar.running').toUpperCase()}
            </span>
          </div>

          <div className="flex items-center gap-2 mt-0.5">
            <span className="text-[10px] text-zinc-500">{taskTypeLabel(task.task_type)}</span>
            {task.subtitle && (
              <span className="text-[10px] text-zinc-500 truncate">{task.subtitle}</span>
            )}
            <span className="text-[10px] text-emerald-500/80 ml-auto shrink-0 tabular-nums">{elapsed}</span>
            <button
              onClick={() => onCancel(task.id)}
              className="p-0.5 rounded text-zinc-600 hover:text-red-400 transition-colors shrink-0"
              title={t('common.cancel')}
            >
              <X size={12} />
            </button>
          </div>

          {/* Progress */}
          <div className="mt-2 flex items-center gap-2">
            <div className="flex-1 h-1.5 bg-zinc-800 rounded-full overflow-hidden">
              <div
                className="h-full rounded-full bg-emerald-500 transition-all duration-300"
                style={{ width: `${pct}%` }}
              />
            </div>
            {task.progress > 0 && (
              <span className="text-[10px] text-zinc-400 shrink-0 tabular-nums">{task.progress}%</span>
            )}
            {task.speed != null && task.speed > 0 && (
              <span className="text-[10px] text-zinc-600 shrink-0">{formatSpeed(task.speed)}</span>
            )}
          </div>
        </div>
      </div>
    </div>
  );
};
