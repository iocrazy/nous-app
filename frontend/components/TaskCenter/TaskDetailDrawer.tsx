/**
 * Right-side detail drawer for a task (A7).
 *
 * Shows full metadata, progress, error, timestamps, and offers
 * Cancel / Retry / Delete actions delegating to TaskManagerContext.
 */

import React from 'react';
import { X, RotateCcw, Ban, Trash2, ExternalLink } from 'lucide-react';
import type { UnifiedTask } from '../../contexts/TaskManagerContext';
import { taskTypeLabel, useTaskManager } from '../../contexts/TaskManagerContext';
import { taskIdLabel, statusVisual, relativeTime } from '../../utils/taskDisplay';

interface TaskDetailDrawerProps {
  task: UnifiedTask | null;
  onClose: () => void;
}

export const TaskDetailDrawer: React.FC<TaskDetailDrawerProps> = ({ task, onClose }) => {
  const { cancelTask, retryTask, deleteTask } = useTaskManager();
  if (!task) return null;
  const v = statusVisual(task.status);
  const md = (task.metadata ?? {}) as Record<string, unknown>;
  const isTerminal = task.status === 'completed' || task.status === 'failed' || task.status === 'cancelled';
  const isFailedRetryable = task.status === 'failed';

  const onCancel = async () => { await cancelTask(task.id); };
  const onRetry  = async () => { await retryTask(task.id); };
  const onDelete = async () => {
    if (window.confirm('Delete this task row? Underlying job state stays in the workflow log.')) {
      await deleteTask(task.id);
      onClose();
    }
  };

  return (
    <aside className="fixed top-0 right-0 h-full w-full sm:w-[420px] bg-zinc-950 border-l border-zinc-800 shadow-2xl z-30 flex flex-col">
      <header className="flex items-center gap-2 px-4 py-3 border-b border-zinc-800">
        <span className={`px-2 py-0.5 text-[10px] rounded ${v.bg} ${v.color} ring-1 ${v.border} uppercase tracking-wider`}>
          {v.label}
        </span>
        <span className="font-mono text-[10px] text-zinc-500 uppercase">{taskIdLabel(task)}</span>
        <button onClick={onClose} className="ml-auto p-1 text-zinc-400 hover:text-zinc-200 hover:bg-zinc-800 rounded" title="Close">
          <X size={14} />
        </button>
      </header>

      <div className="flex-1 overflow-y-auto px-4 py-3 space-y-4">
        <div>
          <div className="text-[10px] text-zinc-500 uppercase tracking-wider mb-1">Title</div>
          <div className="text-sm text-zinc-100 break-words">{task.title || '(no title)'}</div>
          {task.subtitle && (
            <div className="text-xs text-zinc-400 mt-1 break-words">{task.subtitle}</div>
          )}
        </div>

        <div className="grid grid-cols-2 gap-3 text-xs">
          <div>
            <div className="text-[10px] text-zinc-500 uppercase tracking-wider mb-0.5">Type</div>
            <div className="text-zinc-300">{taskTypeLabel(task.task_type)}</div>
          </div>
          <div>
            <div className="text-[10px] text-zinc-500 uppercase tracking-wider mb-0.5">Progress</div>
            <div className="text-zinc-300">{task.progress ?? 0}%</div>
          </div>
          <div>
            <div className="text-[10px] text-zinc-500 uppercase tracking-wider mb-0.5">Created</div>
            <div className="text-zinc-300">{relativeTime(task.created_at)}</div>
          </div>
          <div>
            <div className="text-[10px] text-zinc-500 uppercase tracking-wider mb-0.5">Updated</div>
            <div className="text-zinc-300">{relativeTime(task.updated_at ?? task.created_at)}</div>
          </div>
          {task.started_at && (
            <div>
              <div className="text-[10px] text-zinc-500 uppercase tracking-wider mb-0.5">Started</div>
              <div className="text-zinc-300">{relativeTime(task.started_at)}</div>
            </div>
          )}
          {task.completed_at && (
            <div>
              <div className="text-[10px] text-zinc-500 uppercase tracking-wider mb-0.5">Completed</div>
              <div className="text-zinc-300">{relativeTime(task.completed_at)}</div>
            </div>
          )}
        </div>

        {task.status === 'processing' && (
          <div>
            <div className="text-[10px] text-zinc-500 uppercase tracking-wider mb-1">Progress</div>
            <div className="h-1.5 bg-zinc-800 rounded overflow-hidden">
              <div className="h-full bg-blue-500 transition-all" style={{ width: `${Math.min(100, task.progress)}%` }} />
            </div>
          </div>
        )}

        {task.error_msg && (
          <div>
            <div className="text-[10px] text-rose-400 uppercase tracking-wider mb-1">Error</div>
            <div className="text-xs text-rose-300 bg-rose-500/10 border border-rose-500/30 rounded p-2 whitespace-pre-wrap break-words">
              {task.error_msg}
            </div>
            {task.error_code && (
              <div className="text-[10px] text-rose-500 mt-1 font-mono">{task.error_code}</div>
            )}
          </div>
        )}

        {(md.flow_id || md.agent_id) && (
          <div className="flex flex-wrap gap-2">
            {typeof md.flow_id === 'string' && (
              <a
                href={`/api/v1/flows/${md.flow_id}`}
                className="inline-flex items-center gap-1 px-2 py-0.5 text-[10px] rounded bg-emerald-500/10 text-emerald-300 ring-1 ring-emerald-500/30"
              >
                Flow {md.flow_id.slice(0, 8)}
                <ExternalLink size={9} />
              </a>
            )}
            {typeof md.agent_id === 'string' && (
              <span className="inline-flex items-center gap-1 px-2 py-0.5 text-[10px] rounded bg-purple-500/10 text-purple-300 ring-1 ring-purple-500/30">
                Agent {md.agent_id.slice(0, 8)}
              </span>
            )}
          </div>
        )}

        <details className="text-xs">
          <summary className="cursor-pointer text-[10px] text-zinc-500 uppercase tracking-wider hover:text-zinc-300">
            Metadata
          </summary>
          <pre className="mt-2 text-[10px] text-zinc-400 bg-zinc-900 border border-zinc-800 rounded p-2 overflow-x-auto whitespace-pre-wrap break-all">
            {JSON.stringify(task.metadata ?? {}, null, 2)}
          </pre>
        </details>

        <details className="text-xs">
          <summary className="cursor-pointer text-[10px] text-zinc-500 uppercase tracking-wider hover:text-zinc-300">
            DBOS workflow
          </summary>
          <div className="mt-2 font-mono text-[10px] text-zinc-400 break-all">
            {task.dbos_workflow_id ?? task.id}
          </div>
        </details>
      </div>

      <footer className="border-t border-zinc-800 px-4 py-2 flex items-center gap-2">
        {!isTerminal && (
          <button
            onClick={onCancel}
            className="inline-flex items-center gap-1 px-2 py-1 text-xs rounded bg-amber-500/10 text-amber-300 ring-1 ring-amber-500/30 hover:bg-amber-500/20"
          >
            <Ban size={12} /> Cancel
          </button>
        )}
        {isFailedRetryable && (
          <button
            onClick={onRetry}
            className="inline-flex items-center gap-1 px-2 py-1 text-xs rounded bg-blue-500/10 text-blue-300 ring-1 ring-blue-500/30 hover:bg-blue-500/20"
          >
            <RotateCcw size={12} /> Retry
          </button>
        )}
        <button
          onClick={onDelete}
          className="inline-flex items-center gap-1 px-2 py-1 text-xs rounded text-zinc-400 hover:text-rose-400 hover:bg-rose-500/10 ml-auto"
        >
          <Trash2 size={12} /> Delete
        </button>
      </footer>
    </aside>
  );
};
