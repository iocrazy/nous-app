/**
 * Linear-style flat task list view (A7).
 *
 * Renders task groups as collapsible sections; each row is a single
 * line: status dot · ID prefix · title · type · agent · timestamp.
 * Click row → opens TaskDetailDrawer.
 */

import React, { useState } from 'react';
import { ChevronRight, ChevronDown, CircleDot, Circle, CheckCircle2, XCircle, Ban, Loader2 } from 'lucide-react';
import type { UnifiedTask, TaskStatus } from '../../contexts/TaskManagerContext';
import { taskTypeLabel } from '../../contexts/TaskManagerContext';
import { taskIdLabel, statusVisual, relativeTime, type TaskGroup } from '../../utils/taskDisplay';
import { TaskRowExpanded } from './TaskRowExpanded';

interface TaskListViewProps {
  groups: TaskGroup[];
  expandedIds: Set<string>;
  onToggle: (task: UnifiedTask) => void;
}

function StatusIcon({ status }: { status: TaskStatus | undefined }) {
  const v = statusVisual(status);
  switch (status) {
    case 'processing':
      return <Loader2 size={12} className={`${v.color} animate-spin`} />;
    case 'completed':
      return <CheckCircle2 size={12} className={v.color} />;
    case 'failed':
      return <XCircle size={12} className={v.color} />;
    case 'cancelled':
      return <Ban size={12} className={v.color} />;
    case 'pending':
    default:
      return <Circle size={12} className={v.color} />;
  }
}

const TaskRow: React.FC<{
  task: UnifiedTask;
  expanded: boolean;
  onToggle: () => void;
}> = ({ task, expanded, onToggle }) => {
  const md = (task.metadata ?? {}) as Record<string, unknown>;
  const agentId = md.agent_id as string | undefined;
  const flowId = md.flow_id as string | undefined;
  return (
    <>
      <button
        type="button"
        onClick={onToggle}
        className={`w-full flex items-center gap-3 px-4 py-1.5 text-left text-xs transition border-b border-zinc-900/60 last:border-b-0 ${
          expanded ? 'bg-indigo-500/10' : 'hover:bg-zinc-800/40'
        }`}
      >
        {expanded
          ? <ChevronDown size={11} className="text-zinc-400 shrink-0" />
          : <ChevronRight size={11} className="text-zinc-600 shrink-0" />}
        <StatusIcon status={task.status} />
        <span className="font-mono text-[10px] text-zinc-500 w-16 shrink-0 uppercase tracking-wider">
          {taskIdLabel(task)}
        </span>
        <span className="flex-1 truncate text-zinc-200">{task.title || '(no title)'}</span>
        {task.error_msg && (
          <span className="text-rose-400 truncate max-w-[160px]" title={task.error_msg}>
            {task.error_msg}
          </span>
        )}
        <span className="text-[10px] text-zinc-500 hidden md:inline-block">
          {taskTypeLabel(task.task_type)}
        </span>
        {agentId && (
          <span className="px-1.5 py-0.5 rounded bg-purple-500/10 text-purple-300 text-[10px] hidden lg:inline-block" title={`Agent ${agentId}`}>
            AG
          </span>
        )}
        {flowId && (
          <span className="px-1.5 py-0.5 rounded bg-emerald-500/10 text-emerald-300 text-[10px] hidden lg:inline-block" title={`Flow ${flowId}`}>
            FL
          </span>
        )}
        <span className="text-[10px] text-zinc-500 w-16 text-right shrink-0">
          {relativeTime(task.updated_at ?? task.created_at)}
        </span>
      </button>
      {expanded && <TaskRowExpanded task={task} />}
    </>
  );
};

const GroupSection: React.FC<{
  group: TaskGroup;
  expandedIds: Set<string>;
  onToggle: (t: UnifiedTask) => void;
}> = ({ group, expandedIds, onToggle }) => {
  const [open, setOpen] = useState(true);
  return (
    <div className="border-b border-zinc-800/80 last:border-b-0">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="w-full flex items-center gap-2 px-4 py-1.5 text-left bg-zinc-900/40 hover:bg-zinc-800/40 sticky top-0"
      >
        {open ? <ChevronDown size={11} className="text-zinc-500" /> : <ChevronRight size={11} className="text-zinc-500" />}
        <CircleDot size={10} className="text-zinc-500" />
        <span className="text-[11px] font-medium text-zinc-300 uppercase tracking-wider">{group.label}</span>
        <span className="text-[10px] text-zinc-500 ml-auto">{group.tasks.length}</span>
      </button>
      {open && (
        <div className="bg-zinc-950/30">
          {group.tasks.map((t) => (
            <TaskRow
              key={t.id}
              task={t}
              expanded={expandedIds.has(t.id)}
              onToggle={() => onToggle(t)}
            />
          ))}
        </div>
      )}
    </div>
  );
};

export const TaskListView: React.FC<TaskListViewProps> = ({ groups, expandedIds, onToggle }) => {
  if (groups.length === 0) {
    return (
      <div className="flex items-center justify-center py-24 text-sm text-zinc-500">
        No tasks match the current filters.
      </div>
    );
  }
  return (
    <div className="border-x border-b border-zinc-800/80">
      {groups.map((g) => (
        <GroupSection key={g.key} group={g} expandedIds={expandedIds} onToggle={onToggle} />
      ))}
    </div>
  );
};
