/**
 * Inline detail block for an expanded task row (A7 polish).
 *
 * Mirrors the admin Arco `Descriptions`-grid pattern: 3-column key-value
 * grid + subtitle line + error block + action footer. No drawer.
 */

import React from 'react';
import { RotateCcw, Ban, Trash2, ExternalLink } from 'lucide-react';
import {
  taskTypeLabel, useTaskManager, type UnifiedTask,
} from '../../contexts/TaskManagerContext';
import { taskIdLabel, statusVisual, relativeTime } from '../../utils/taskDisplay';

interface TaskRowExpandedProps {
  task: UnifiedTask;
}

const Field: React.FC<{ label: string; children: React.ReactNode }> = ({ label, children }) => (
  <div className="min-w-0">
    <div className="text-[10px] uppercase tracking-wider text-zinc-500 mb-0.5">{label}</div>
    <div className="text-xs text-zinc-200 truncate">{children}</div>
  </div>
);

function formatRuntime(started?: string, completed?: string): string {
  if (!started) return '-';
  const startMs = new Date(started).getTime();
  const endMs = completed ? new Date(completed).getTime() : Date.now();
  const sec = Math.max(0, Math.floor((endMs - startMs) / 1000));
  if (sec < 60) return `${sec}s`;
  const min = Math.floor(sec / 60);
  const remSec = sec % 60;
  if (min < 60) return `${min}m ${remSec}s`;
  const hr = Math.floor(min / 60);
  return `${hr}h ${min % 60}m`;
}

export const TaskRowExpanded: React.FC<TaskRowExpandedProps> = ({ task }) => {
  const { cancelTask, retryTask, deleteTask } = useTaskManager();
  const v = statusVisual(task.status);
  const md = (task.metadata ?? {}) as Record<string, unknown>;
  const isTerminal = task.status === 'completed' || task.status === 'failed' || task.status === 'cancelled';
  const isFailedRetryable = task.status === 'failed';

  const onCancel = async () => { await cancelTask(task.id); };
  const onRetry  = async () => { await retryTask(task.id); };
  const onDelete = async () => {
    if (window.confirm('Delete this task row? Underlying job state stays in the workflow log.')) {
      await deleteTask(task.id);
    }
  };

  return (
    <div className="bg-zinc-950/60 border-y border-zinc-800/80 px-6 py-3 space-y-3">
      <div className="grid grid-cols-2 md:grid-cols-3 gap-x-6 gap-y-2.5">
        <Field label="Task ID">
          <span className="font-mono text-[11px]">{taskIdLabel(task)}</span>
          <span className="text-zinc-500 ml-1.5 font-mono text-[10px]">{task.id.slice(0, 8)}</span>
        </Field>
        <Field label="Type">
          <span className="px-1.5 py-0.5 rounded bg-zinc-800 text-zinc-300 text-[10px]">{taskTypeLabel(task.task_type)}</span>
        </Field>
        <Field label="Status">
          <span className={`px-1.5 py-0.5 rounded text-[10px] uppercase tracking-wider ${v.badgeBg} ${v.badgeText} ring-1 ${v.border} font-semibold`}>
            {v.label}
          </span>
        </Field>
        <Field label="Phase">{task.phase ?? '-'}</Field>
        <Field label="Progress">{`${task.progress ?? 0}%`}</Field>
        <Field label="Runtime">{formatRuntime(task.started_at, task.completed_at)}</Field>
        <Field label="Created">{relativeTime(task.created_at)}</Field>
        <Field label="Started">{task.started_at ? relativeTime(task.started_at) : '-'}</Field>
        <Field label="Completed">{task.completed_at ? relativeTime(task.completed_at) : '-'}</Field>
        <Field label="Resource ID">{task.resource_id ?? '-'}</Field>
        <Field label="Media ID">{task.media_id ?? '-'}</Field>
        {typeof md.flow_id === 'string' && (
          <Field label="Flow">
            <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded bg-emerald-500/15 text-emerald-300 text-[10px] ring-1 ring-emerald-500/30">
              {md.flow_id.slice(0, 8)} <ExternalLink size={9} />
            </span>
          </Field>
        )}
        {typeof md.agent_id === 'string' && (
          <Field label="Agent">
            <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded bg-purple-500/15 text-purple-300 text-[10px] ring-1 ring-purple-500/30">
              {md.agent_id.slice(0, 8)}
            </span>
          </Field>
        )}
        {typeof md.original_url === 'string' && (
          <div className="col-span-2 md:col-span-3 min-w-0">
            <div className="text-[10px] uppercase tracking-wider text-zinc-500 mb-0.5">Original URL</div>
            <div className="text-xs text-zinc-300 break-all">{md.original_url}</div>
          </div>
        )}
      </div>

      {task.subtitle && (
        <div className="text-xs">
          <span className="text-zinc-500 font-medium">Subtitle: </span>
          <span className="text-zinc-300">{task.subtitle}</span>
        </div>
      )}

      {task.status === 'processing' && (
        <div className="h-1 bg-zinc-800 rounded overflow-hidden">
          <div className="h-full bg-blue-500 transition-all" style={{ width: `${Math.min(100, task.progress)}%` }} />
        </div>
      )}

      {task.error_msg && (
        <div className="text-xs">
          <span className="text-rose-400 font-medium">Error: </span>
          <span className="text-rose-300">{task.error_msg}</span>
          {task.error_code && (
            <span className="ml-2 px-1.5 py-0.5 rounded bg-rose-500/15 text-rose-200 text-[10px] ring-1 ring-rose-500/30 font-mono">
              {task.error_code}
            </span>
          )}
        </div>
      )}

      <details className="text-xs">
        <summary className="cursor-pointer text-[10px] text-zinc-500 uppercase tracking-wider hover:text-zinc-300 inline-block">
          Metadata · DBOS workflow
        </summary>
        <pre className="mt-2 text-[10px] text-zinc-400 bg-zinc-900 border border-zinc-800 rounded p-2 overflow-x-auto whitespace-pre-wrap break-all max-h-48 overflow-y-auto">
{`workflow_uuid: ${task.dbos_workflow_id ?? task.id}\n`}
{`metadata: ${JSON.stringify(task.metadata ?? {}, null, 2)}`}
        </pre>
      </details>

      <div className="flex items-center gap-2 pt-1">
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
      </div>
    </div>
  );
};
