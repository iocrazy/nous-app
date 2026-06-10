import React, { useState } from 'react';
import { CheckCircle2, XCircle, X, RotateCcw, Download, ExternalLink } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { getResourceCoverUrl, getResourceFileUrl } from '../../services/resourceService';
import { useAuth } from '../../contexts/AuthContext';
import {
  formatSpeed,
  formatFileSize,
  taskTypeLabel,
  type UnifiedTask,
  type TaskStatus,
} from '../../contexts/TaskManagerContext';
import { taskRowActions, taskShowsCover } from './taskRowPresentation';
import { TaskTypeIcon } from './TaskTypeIcon';
import { failureLabel } from '../../utils/taskFailure';

// Task type → background color for the icon badge (fallback when no cover).
function taskTypeBg(type: string): string {
  switch (type) {
    case 'upload':              return 'bg-blue-500/20 text-blue-400';
    case 'download':            return 'bg-purple-500/20 text-purple-400';
    case 'transcode':           return 'bg-amber-500/20 text-amber-400';
    case 'ai_pipeline':         return 'bg-cyan-500/20 text-cyan-400';
    case 'ai_extract':          return 'bg-violet-500/20 text-violet-400';
    case 'ai_transcription':    return 'bg-fuchsia-500/20 text-fuchsia-400';
    case 'ai_summary':          return 'bg-cyan-500/20 text-cyan-400';
    default:                    return 'bg-zinc-700/50 text-zinc-400';
  }
}

function progressBarColor(status: TaskStatus): string {
  switch (status) {
    case 'completed':  return 'bg-emerald-500';
    case 'failed':     return 'bg-red-500';
    case 'cancelled':  return 'bg-zinc-600';
    default:           return 'bg-indigo-500';
  }
}

interface TaskCenterRowProps {
  task: UnifiedTask;
  onCancel: (id: string) => void;
  onRetry: (id: string) => void;
  /** Open the produced resource's detail page (and close the panel). */
  onOpenResource: (resourceId: string) => void;
  /** Open the in-panel typed result modal for a terminal task. */
  onOpenDetail: (task: UnifiedTask) => void;
}

/**
 * One row in the floating Task Center panel. Completed tasks that produced a
 * resource show its cover thumbnail and expose open/download affordances
 * (mirroring the richer task-result UI); active tasks keep their progress bar,
 * failed tasks keep the inline error + retry.
 */
export const TaskCenterRow: React.FC<TaskCenterRowProps> = ({
  task,
  onCancel,
  onRetry,
  onOpenResource,
  onOpenDetail,
}) => {
  const { t } = useTranslation();
  const { mediaToken } = useAuth();
  const [coverFailed, setCoverFailed] = useState(false);

  const actions = taskRowActions(task);
  const isActive = task.status === 'pending' || task.status === 'processing';
  const showCover = taskShowsCover(task) && !coverFailed;

  const handleOpen = () => {
    if (actions.open && task.resource_id) onOpenResource(String(task.resource_id));
  };

  return (
    <div
      className={`group px-3 py-2.5 border-b border-zinc-800/50 last:border-b-0 ${
        !isActive ? 'cursor-pointer hover:bg-zinc-800/40 transition-colors' : ''
      }`}
      onClick={!isActive ? () => onOpenDetail(task) : undefined}
    >
      <div className="flex items-center gap-2.5">
        {/* Cover thumbnail (completed w/ resource) or type-icon badge */}
        {showCover && task.resource_id ? (
          <img
            src={getResourceCoverUrl(String(task.resource_id))}
            alt=""
            className="w-9 h-9 rounded-lg object-cover shrink-0 bg-zinc-800"
            onError={() => setCoverFailed(true)}
            loading="lazy"
          />
        ) : (
          <div
            className={`w-8 h-8 rounded-lg flex items-center justify-center shrink-0 text-sm ${taskTypeBg(
              task.task_type,
            )}`}
          >
            <TaskTypeIcon type={task.task_type} size={14} />
          </div>
        )}

        {/* Task info */}
        <div className="flex-1 min-w-0">
          <div className="flex items-center justify-between">
            <span className="text-xs text-zinc-300 truncate max-w-[160px]">{task.title}</span>
            <div className="flex items-center gap-1.5 shrink-0 ml-2">
              {isActive && (
                <>
                  {task.progress > 0 && (
                    <span className="text-[10px] text-zinc-500">{task.progress}%</span>
                  )}
                  {task.speed != null && task.speed > 0 && (
                    <span className="text-[10px] text-zinc-600">{formatSpeed(task.speed)}</span>
                  )}
                  <button
                    onClick={(e) => { e.stopPropagation(); onCancel(task.id); }}
                    className="p-0.5 rounded text-zinc-600 hover:text-red-400 transition-colors"
                    title={t('common.cancel')}
                  >
                    <X size={12} />
                  </button>
                </>
              )}

              {/* Completed: hover-revealed open/download, plus the status check */}
              {task.status === 'completed' && (
                <>
                  {actions.open && (
                    <button
                      onClick={(e) => { e.stopPropagation(); handleOpen(); }}
                      className="p-0.5 rounded text-zinc-500 hover:text-indigo-400 transition-colors opacity-0 group-hover:opacity-100"
                      title={t('topbar.openResource')}
                    >
                      <ExternalLink size={13} />
                    </button>
                  )}
                  {actions.download && task.resource_id && (
                    <a
                      href={getResourceFileUrl(String(task.resource_id), mediaToken ?? undefined)}
                      onClick={(e) => e.stopPropagation()}
                      className="p-0.5 rounded text-zinc-500 hover:text-indigo-400 transition-colors opacity-0 group-hover:opacity-100"
                      title={t('topbar.downloadResult')}
                      download
                    >
                      <Download size={13} />
                    </a>
                  )}
                  <CheckCircle2 size={14} className="text-emerald-400 shrink-0" />
                </>
              )}

              {(task.status === 'failed' || task.status === 'cancelled') && (
                <>
                  {actions.open && (
                    <button
                      onClick={(e) => { e.stopPropagation(); handleOpen(); }}
                      className="p-0.5 rounded text-zinc-500 hover:text-indigo-400 transition-colors"
                      title={t('topbar.openResource')}
                    >
                      <ExternalLink size={13} />
                    </button>
                  )}
                  {actions.retry && (
                    <button
                      onClick={(e) => { e.stopPropagation(); onRetry(task.id); }}
                      className="p-0.5 rounded text-zinc-500 hover:text-indigo-400 transition-colors"
                      title={t('common.retry')}
                    >
                      <RotateCcw size={12} />
                    </button>
                  )}
                  {task.status === 'failed' ? (
                    <XCircle size={14} className="text-red-400 shrink-0" />
                  ) : (
                    <X size={14} className="text-zinc-500 shrink-0" />
                  )}
                </>
              )}
            </div>
          </div>

          <div className="flex items-center gap-2 mt-0.5">
            <span className="text-[10px] text-zinc-600">{taskTypeLabel(task.task_type)}</span>
            {task.subtitle && (
              <span className="text-[10px] text-zinc-600 truncate">{task.subtitle}</span>
            )}
            {task.total_bytes != null && task.total_bytes > 0 && (
              <span className="text-[10px] text-zinc-600">{formatFileSize(task.total_bytes)}</span>
            )}
          </div>

          {task.error_msg && (
            <div className="text-[10px] text-red-400 mt-0.5 line-clamp-2 break-words">
              {failureLabel(task.error_msg)}
            </div>
          )}
        </div>
      </div>

      {/* Progress bar for active tasks */}
      {isActive && (
        <div className="mt-1.5 h-1 bg-zinc-800 rounded-full overflow-hidden">
          <div
            className={`h-full rounded-full transition-all duration-300 ${progressBarColor(task.status)}`}
            style={{ width: `${Math.max(task.progress, task.status === 'processing' ? 2 : 0)}%` }}
          />
        </div>
      )}
    </div>
  );
};
