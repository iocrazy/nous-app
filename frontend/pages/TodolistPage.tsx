/**
 * TodolistPage — Linear/Paperclip-style task tracker (A7).
 *
 * Replaces the legacy DashboardPage > Tasks tab + TasksPanel.tsx as
 * the canonical task center. Routed at /team/:teamId/todolist (already
 * wired in router.tsx + sidebar nav).
 *
 * Design:
 *   - Toolbar: search + status/type filter pills + group-by + sort + view-mode toggle
 *   - Body:    list view (group sections) | kanban view (group columns)
 *   - Drawer:  right-side TaskDetailDrawer when a row is clicked
 *
 * Data: useTaskManager() — same flat task array the legacy view used,
 *        client-side group/filter/sort. WS / Realtime stays unchanged.
 */

import React, { useMemo, useState } from 'react';
import { ListTodo } from 'lucide-react';
import { useTaskManager, type TaskStatus, type TaskType, type UnifiedTask } from '../contexts/TaskManagerContext';
import {
  filterTasks, groupTasks, sortTasks,
  type GroupBy, type SortBy, type TaskFilter,
} from '../utils/taskDisplay';
import { TaskToolbar, type ViewMode } from '../components/TaskCenter/TaskToolbar';
import { TaskListView } from '../components/TaskCenter/TaskListView';
import { TaskKanbanView } from '../components/TaskCenter/TaskKanbanView';
import { TaskDetailDrawer } from '../components/TaskCenter/TaskDetailDrawer';

export function TodolistPage() {
  const { tasks, isLoading, refreshTasks } = useTaskManager();

  const [search, setSearch] = useState('');
  const [statusFilter, setStatusFilter] = useState<Set<TaskStatus>>(new Set());
  const [typeFilter, setTypeFilter] = useState<Set<TaskType>>(new Set());
  const [groupBy, setGroupBy] = useState<GroupBy>('status');
  const [sortBy, setSortBy] = useState<SortBy>('created_desc');
  const [viewMode, setViewMode] = useState<ViewMode>('list');
  const [selected, setSelected] = useState<UnifiedTask | null>(null);
  const [refreshing, setRefreshing] = useState(false);

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

  // Keep selection in sync with latest task data so the drawer reflects
  // Realtime updates without re-clicking.
  const liveSelected = useMemo(() => {
    if (!selected) return null;
    return tasks.find((t) => t.id === selected.id) ?? null;
  }, [selected, tasks]);

  if (isLoading && tasks.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center h-64 text-zinc-500">
        <ListTodo size={36} className="mb-3 text-zinc-700 animate-pulse" />
        <p className="text-sm">Loading tasks…</p>
      </div>
    );
  }

  return (
    <div className="flex flex-col h-[calc(100vh-5rem)] -mx-4 sm:-mx-8 -mb-28 sm:-mb-8 bg-zinc-950 border-t border-zinc-800/80">
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
            selectedTaskId={liveSelected?.id ?? null}
            onSelect={setSelected}
          />
        ) : (
          <TaskKanbanView
            groups={groups}
            selectedTaskId={liveSelected?.id ?? null}
            onSelect={setSelected}
          />
        )}
      </div>
      {liveSelected && (
        <TaskDetailDrawer
          task={liveSelected}
          onClose={() => setSelected(null)}
        />
      )}
    </div>
  );
}

export default TodolistPage;
