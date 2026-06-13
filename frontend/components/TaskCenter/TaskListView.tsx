/**
 * Linear-style flat task list view (A7).
 *
 * Renders task groups as collapsible sections; each row is a single
 * line: status dot · ID prefix · title · type · agent · timestamp.
 * Click row → opens TaskDetailDrawer.
 */

import React, { useState, useEffect, useRef } from 'react';
import { ChevronRight, ChevronDown, CircleDot, Circle, CheckCircle2, XCircle, Ban, Loader2 } from 'lucide-react';
import type { UnifiedTask, TaskStatus } from '../../contexts/TaskManagerContext';
import { taskTypeLabel } from '../../contexts/TaskManagerContext';
import { taskIdLabel, statusVisual, relativeTime, type TaskGroup } from '../../utils/taskDisplay';
import { isTerminal } from '../../utils/taskSelection';
import { TaskRowExpanded } from './TaskRowExpanded';
import { FlowGroupCard } from './FlowGroupCard';

interface TaskListViewProps {
  groups: TaskGroup[];
  expandedIds: Set<string>;
  onToggle: (task: UnifiedTask) => void;
  selectedIds: Set<string>;
  onToggleSelect: (id: string) => void;
  focusedId: string | null;
  onFocusRow: (id: string) => void;
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
  selected: boolean;
  onToggleSelect: (id: string) => void;
  focused: boolean;
  onFocus: (id: string) => void;
}> = ({ task, expanded, onToggle, selected, onToggleSelect, focused, onFocus }) => {
  const md = (task.metadata ?? {}) as Record<string, unknown>;
  const agentId = md.agent_id as string | undefined;
  // Prefer the task_tracking.flow_id column; fall back to metadata.flow_id
  // for legacy rows that stamped flow into metadata before the column wired.
  const flowId = task.flow_id ?? (md.flow_id as string | undefined);
  // Only terminal tasks (completed/failed/cancelled) get a checkbox — batch
  // Retry/Delete have no meaning for in-flight rows.
  const selectable = isTerminal(task.status);
  // Keep the keyboard-focused row scrolled into view as ↑/↓ walks the list.
  const rowRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (focused) rowRef.current?.scrollIntoView({ block: 'nearest' });
  }, [focused]);
  return (
    <>
      <div
        ref={rowRef}
        onMouseDown={() => onFocus(task.id)}
        className={`flex items-center border-b border-ink-900/60 last:border-b-0 ${
          focused ? 'ring-1 ring-inset ring-indigo-400/60 ' : ''
        }${
          expanded ? 'bg-indigo-500/10' : selected ? 'bg-indigo-500/[0.06]' : 'hover:bg-ink-800/40'
        }`}
      >
        {selectable ? (
          <label
            className="flex items-center pl-3 pr-0.5 cursor-pointer shrink-0"
            onClick={(e) => e.stopPropagation()}
          >
            <input
              type="checkbox"
              checked={selected}
              onChange={() => onToggleSelect(task.id)}
              className="h-3 w-3 rounded border-ink-600 bg-ink-800 text-indigo-500 focus:ring-0 focus:ring-offset-0 cursor-pointer"
            />
          </label>
        ) : (
          <span className="w-[26px] shrink-0" aria-hidden />
        )}
      <button
        type="button"
        onClick={onToggle}
        className={`flex-1 min-w-0 flex items-center gap-3 pl-1 pr-4 py-1.5 text-left text-xs transition outline-none focus:outline-none`}
      >
        {expanded
          ? <ChevronDown size={11} className="text-ink-400 shrink-0" />
          : <ChevronRight size={11} className="text-ink-600 shrink-0" />}
        <StatusIcon status={task.status} />
        <span className="font-mono text-[10px] text-ink-500 w-16 shrink-0 uppercase tracking-wider">
          {taskIdLabel(task)}
        </span>
        <span className="flex-1 truncate text-ink-200">{task.title || '(no title)'}</span>
        {task.error_msg && (
          <span className="text-rose-400 truncate max-w-[160px]" title={task.error_msg}>
            {task.error_msg}
          </span>
        )}
        <span className="text-[10px] text-ink-500 hidden md:inline-block">
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
        <span className="text-[10px] text-ink-500 w-16 text-right shrink-0">
          {relativeTime(task.updated_at ?? task.created_at)}
        </span>
      </button>
      </div>
      {expanded && <TaskRowExpanded task={task} />}
    </>
  );
};

const GroupSection: React.FC<{
  group: TaskGroup;
  expandedIds: Set<string>;
  onToggle: (t: UnifiedTask) => void;
  selectedIds: Set<string>;
  onToggleSelect: (id: string) => void;
  focusedId: string | null;
  onFocusRow: (id: string) => void;
}> = ({ group, expandedIds, onToggle, selectedIds, onToggleSelect, focusedId, onFocusRow }) => {
  const [open, setOpen] = useState(true);

  const renderRow = (t: UnifiedTask) => (
    <TaskRow
      key={t.id}
      task={t}
      expanded={expandedIds.has(t.id)}
      onToggle={() => onToggle(t)}
      selected={selectedIds.has(t.id)}
      onToggleSelect={onToggleSelect}
      focused={focusedId === t.id}
      onFocus={onFocusRow}
    />
  );

  // Empty label = "no grouping" (groupBy='none' returns a single bucket
  // with label=''). Render rows flat without a header — paperclip-style.
  if (!group.label) {
    return <div>{group.tasks.map(renderRow)}</div>;
  }

  // groupBy='flow' upgrades the section into a FlowGroupCard with
  // aggregate progress + cascade-cancel. taskDisplay.ts seeds the group
  // key as the actual flow_id (or '__standalone__' for ungrouped rows).
  // The standalone bucket falls through to the plain section header.
  if (group.key !== '__standalone__' && group.tasks.some((t) => t.flow_id || (t.metadata as Record<string, unknown> | undefined)?.['flow_id'])) {
    const t0 = group.tasks[0];
    const flowId = (t0.flow_id ?? ((t0.metadata as Record<string, unknown> | undefined)?.['flow_id'] as string | undefined)) || group.key;
    return <FlowGroupCard group={group} flowId={flowId} renderTask={renderRow} />;
  }

  return (
    <div className="border-b border-ink-800/80 last:border-b-0">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="w-full flex items-center gap-2 px-4 py-1.5 text-left bg-ink-900/40 hover:bg-ink-800/40 sticky top-0"
      >
        {open ? <ChevronDown size={11} className="text-ink-500" /> : <ChevronRight size={11} className="text-ink-500" />}
        <CircleDot size={10} className="text-ink-500" />
        <span className="text-[11px] font-medium text-ink-300 uppercase tracking-wider">{group.label}</span>
        <span className="text-[10px] text-ink-500 ml-auto">{group.tasks.length}</span>
      </button>
      {open && (
        <div className="bg-ink-950/30">
          {group.tasks.map(renderRow)}
        </div>
      )}
    </div>
  );
};

export const TaskListView: React.FC<TaskListViewProps> = ({ groups, expandedIds, onToggle, selectedIds, onToggleSelect, focusedId, onFocusRow }) => {
  if (groups.length === 0) {
    return (
      <div className="flex items-center justify-center py-24 text-sm text-ink-500">
        No tasks match the current filters.
      </div>
    );
  }
  return (
    <div className="border-x border-b border-ink-800/80">
      {groups.map((g) => (
        <GroupSection
          key={g.key}
          group={g}
          expandedIds={expandedIds}
          onToggle={onToggle}
          selectedIds={selectedIds}
          onToggleSelect={onToggleSelect}
          focusedId={focusedId}
          onFocusRow={onFocusRow}
        />
      ))}
    </div>
  );
};
