/**
 * TaskCenter — the Linear-style task LOG / history view.
 *
 * Reusable component embedding the full toolbar + list/kanban + detail
 * drawer pipeline. Rendered by Settings → Tasks (per-user log) and may
 * be reused elsewhere (e.g., a dedicated /tasks-log route in the future).
 *
 * For "task DISPATCH + conversation" UX (paperclip-style), see TodolistPage
 * (still pending its rewrite as of A7).
 */

import React, { useMemo, useState } from 'react';
import { ListTodo } from 'lucide-react';
import { useTaskManager, type TaskStatus, type TaskType, type UnifiedTask } from '../../contexts/TaskManagerContext';
import {
  filterTasks, groupTasks, sortTasks,
  type GroupBy, type SortBy, type TaskFilter,
} from '../../utils/taskDisplay';
import { TaskToolbar, type ViewMode } from './TaskToolbar';
import { TaskListView } from './TaskListView';
import { TaskKanbanView } from './TaskKanbanView';

interface TaskCenterProps {
  /** When true, the component fills its parent container without breaking
   * out of page padding (suitable for Settings modal embedding). When
   * false (default), uses the edge-to-edge full-height layout. */
  embedded?: boolean;
}

export const TaskCenter: React.FC<TaskCenterProps> = ({ embedded = false }) => {
  const { tasks, isLoading, refreshTasks } = useTaskManager();

  const [search, setSearch] = useState('');
  const [statusFilter, setStatusFilter] = useState<Set<TaskStatus>>(new Set());
  const [typeFilter, setTypeFilter] = useState<Set<TaskType>>(new Set());
  // Default 'none' (no grouping, pure time-sorted list). Users opt in
  // to grouping via the toolbar's Layers picker — paperclip-style.
  const [groupBy, setGroupBy] = useState<GroupBy>('none');
  const [sortBy, setSortBy] = useState<SortBy>('created_desc');
  const [viewMode, setViewMode] = useState<ViewMode>('list');
  // Set of task ids whose inline detail panel is open. Multi-expand
  // intentionally — admin Arco's NotionTable behaves the same way.
  const [expandedIds, setExpandedIds] = useState<Set<string>>(new Set());
  const [refreshing, setRefreshing] = useState(false);

  const toggleExpand = (task: UnifiedTask) => {
    setExpandedIds((prev) => {
      const next = new Set(prev);
      if (next.has(task.id)) next.delete(task.id);
      else next.add(task.id);
      return next;
    });
  };

  const toggleStatus = (s: TaskStatus) => {
    setStatusFilter((prev) => {
      const next = new Set(prev);
      if (next.has(s)) next.delete(s);
      else next.add(s);
      return next;
    });
  };

  const toggleType = (t: TaskType) => {
    setTypeFilter((prev) => {
      const next = new Set(prev);
      if (next.has(t)) next.delete(t);
      else next.add(t);
      return next;
    });
  };

  const filterSpec = useMemo<TaskFilter>(
    () => ({ search, statuses: statusFilter, types: typeFilter }),
    [search, statusFilter, typeFilter],
  );

  const filtered = useMemo(() => filterTasks(tasks, filterSpec), [tasks, filterSpec]);
  const sorted = useMemo(() => sortTasks(filtered, sortBy), [filtered, sortBy]);
  const groups = useMemo(() => groupTasks(sorted, groupBy), [sorted, groupBy]);

  const handleRefresh = async () => {
    setRefreshing(true);
    try {
      await refreshTasks();
    } finally {
      setRefreshing(false);
    }
  };

  if (isLoading && tasks.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center h-64 text-zinc-500">
        <ListTodo size={36} className="mb-3 text-zinc-700 animate-pulse" />
        <p className="text-sm">Loading tasks…</p>
      </div>
    );
  }

  const containerClass = embedded
    ? 'flex flex-col h-full bg-zinc-950 border border-zinc-800/80 rounded-md overflow-hidden'
    : 'flex flex-col h-[calc(100vh-5rem)] -mx-4 sm:-mx-8 -mb-28 sm:-mb-8 bg-zinc-950 border-t border-zinc-800/80';

  return (
    <div className={containerClass}>
      <TaskToolbar
        search={search}
        onSearchChange={setSearch}
        statusFilter={statusFilter}
        onToggleStatus={toggleStatus}
        typeFilter={typeFilter}
        onToggleType={toggleType}
        groupBy={groupBy}
        onGroupByChange={setGroupBy}
        sortBy={sortBy}
        onSortByChange={setSortBy}
        viewMode={viewMode}
        onViewModeChange={setViewMode}
        onRefresh={handleRefresh}
        isRefreshing={refreshing}
        totalCount={sorted.length}
      />
      <div className="flex-1 overflow-y-auto">
        {viewMode === 'list' ? (
          <TaskListView
            groups={groups}
            expandedIds={expandedIds}
            onToggle={toggleExpand}
          />
        ) : (
          <TaskKanbanView
            groups={groups}
            selectedTaskId={null}
            onSelect={() => { /* kanban detail intentionally noop — use list view to expand */ }}
          />
        )}
      </div>
    </div>
  );
};

export default TaskCenter;
