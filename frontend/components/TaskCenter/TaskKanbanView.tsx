/**
 * Linear-style kanban view of tasks (A7).
 *
 * One column per group (typically status). Compact cards inside
 * columns. No drag-drop in v1 — read-only.
 */

import React from 'react';
import { Loader2, Circle, CheckCircle2, XCircle, Ban } from 'lucide-react';
import type { UnifiedTask, TaskStatus } from '../../contexts/TaskManagerContext';
import { taskTypeLabel } from '../../contexts/TaskManagerContext';
import { taskIdLabel, relativeTime, statusVisual, type TaskGroup } from '../../utils/taskDisplay';

interface TaskKanbanViewProps {
  groups: TaskGroup[];
  selectedTaskId: string | null;
  onSelect: (task: UnifiedTask) => void;
}

function StatusDot({ status }: { status: TaskStatus | undefined }) {
  const v = statusVisual(status);
  switch (status) {
    case 'processing':
      return <Loader2 size={10} className={`${v.color} animate-spin`} />;
    case 'completed':
      return <CheckCircle2 size={10} className={v.color} />;
    case 'failed':
      return <XCircle size={10} className={v.color} />;
    case 'cancelled':
      return <Ban size={10} className={v.color} />;
    case 'pending':
    default:
      return <Circle size={10} className={v.color} />;
  }
}

const KanbanCard: React.FC<{
  task: UnifiedTask;
  selected: boolean;
  onClick: () => void;
}> = ({ task, selected, onClick }) => {
  const md = (task.metadata ?? {}) as Record<string, unknown>;
  const agentId = md.agent_id as string | undefined;
  const flowId = task.flow_id ?? (md.flow_id as string | undefined);
  return (
    <button
      type="button"
      onClick={onClick}
      className={`w-full text-left p-2 rounded border transition mb-1.5 ${
        selected
          ? 'border-indigo-500/50 bg-indigo-500/10'
          : 'border-ink-800 bg-ink-900/60 hover:border-ink-700 hover:bg-ink-800/60'
      }`}
    >
      <div className="flex items-center gap-1.5 mb-1">
        <span className="font-mono text-[9px] text-ink-500 uppercase tracking-wider">{taskIdLabel(task)}</span>
        {(agentId || flowId) && (
          <div className="ml-auto flex items-center gap-1">
            {agentId && (
              <span className="px-1 rounded bg-purple-500/10 text-purple-300 text-[9px]">AG</span>
            )}
            {flowId && (
              <span className="px-1 rounded bg-emerald-500/10 text-emerald-300 text-[9px]">FL</span>
            )}
          </div>
        )}
      </div>
      <div className="text-[11px] text-ink-100 mb-1 line-clamp-2 leading-tight">
        {task.title || '(no title)'}
      </div>
      {task.error_msg && (
        <div className="text-[9px] text-rose-400 truncate mb-1" title={task.error_msg}>
          {task.error_msg}
        </div>
      )}
      <div className="flex items-center justify-between text-[9px] text-ink-500">
        <span className="flex items-center gap-1">
          <StatusDot status={task.status} />
          {taskTypeLabel(task.task_type)}
        </span>
        <span>{relativeTime(task.updated_at ?? task.created_at)}</span>
      </div>
      {task.status === 'processing' && task.progress > 0 && (
        <div className="mt-1.5 h-0.5 bg-ink-800 rounded overflow-hidden">
          <div className="h-full bg-blue-500 transition-all" style={{ width: `${Math.min(100, task.progress)}%` }} />
        </div>
      )}
    </button>
  );
};

export const TaskKanbanView: React.FC<TaskKanbanViewProps> = ({ groups, selectedTaskId, onSelect }) => {
  if (groups.length === 0) {
    return (
      <div className="flex items-center justify-center py-24 text-sm text-ink-500">
        No tasks match the current filters.
      </div>
    );
  }
  return (
    <div className="flex gap-3 px-4 py-3 overflow-x-auto" style={{ minHeight: '60vh' }}>
      {groups.map((g) => (
        <div key={g.key} className="flex-shrink-0 w-[280px] flex flex-col">
          <div className="flex items-center gap-2 px-2 py-1.5 mb-2 border-b border-ink-800/80 sticky top-0 bg-ink-950/40">
            <span className="text-[11px] font-medium text-ink-300 uppercase tracking-wider">{g.label}</span>
            <span className="text-[10px] text-ink-500 ml-auto">{g.tasks.length}</span>
          </div>
          <div className="flex-1 overflow-y-auto pr-1">
            {g.tasks.map((t) => (
              <KanbanCard
                key={t.id}
                task={t}
                selected={t.id === selectedTaskId}
                onClick={() => onSelect(t)}
              />
            ))}
            {g.tasks.length === 0 && (
              <div className="text-[10px] text-ink-600 italic px-2 py-3 text-center">empty</div>
            )}
          </div>
        </div>
      ))}
    </div>
  );
};
