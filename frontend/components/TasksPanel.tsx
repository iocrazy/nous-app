// components/TasksPanel.tsx — Unified task history (all task types)

import React, { useState, useMemo } from 'react';
import {
  ListTodo,
  Clock,
  Download,
  Upload,
  CheckCircle,
  XCircle,
  RefreshCw,
  Trash2,
  AlertCircle,
  Ban,
  RotateCcw,
  Brain,
  Clapperboard,
  Loader2,
} from 'lucide-react';
import {
  useTaskManager,
  TaskType,
  TaskStatus,
  UnifiedTask,
  formatSpeed,
  formatFileSize,
  taskTypeLabel,
} from '../contexts/TaskManagerContext';

// ─── Helpers ──────────────────────────────────────────

type TypeFilter = 'all' | TaskType;
type StatusFilterValue = 'all' | TaskStatus;

const TYPE_OPTIONS: { value: TypeFilter; label: string; icon: React.ReactNode; color: string }[] = [
  { value: 'all', label: 'All', icon: <ListTodo size={14} />, color: 'text-zinc-400' },
  { value: 'download', label: 'Download', icon: <Download size={14} />, color: 'text-blue-400' },
  { value: 'upload', label: 'Upload', icon: <Upload size={14} />, color: 'text-emerald-400' },
  { value: 'transcode', label: 'Transcode', icon: <Clapperboard size={14} />, color: 'text-amber-400' },
  { value: 'ai_pipeline', label: 'AI Pipeline', icon: <Brain size={14} />, color: 'text-purple-400' },
];

const STATUS_OPTIONS: { value: StatusFilterValue; label: string }[] = [
  { value: 'all', label: 'All Status' },
  { value: 'pending', label: 'Pending' },
  { value: 'processing', label: 'Processing' },
  { value: 'completed', label: 'Completed' },
  { value: 'failed', label: 'Failed' },
  { value: 'cancelled', label: 'Cancelled' },
];

function getStatusIcon(status: TaskStatus) {
  switch (status) {
    case 'pending':
      return <Clock size={14} className="text-yellow-500" />;
    case 'processing':
      return <Loader2 size={14} className="text-blue-500 animate-spin" />;
    case 'completed':
      return <CheckCircle size={14} className="text-green-500" />;
    case 'failed':
      return <XCircle size={14} className="text-red-500" />;
    case 'cancelled':
      return <Ban size={14} className="text-zinc-500" />;
    default:
      return <AlertCircle size={14} className="text-zinc-500" />;
  }
}

function getStatusColor(status: TaskStatus) {
  switch (status) {
    case 'pending': return 'text-yellow-500';
    case 'processing': return 'text-blue-500';
    case 'completed': return 'text-green-500';
    case 'failed': return 'text-red-500';
    case 'cancelled': return 'text-zinc-500';
    default: return 'text-zinc-500';
  }
}

function getTypeIcon(type: TaskType) {
  switch (type) {
    case 'download': return <Download size={14} className="text-blue-400" />;
    case 'upload': return <Upload size={14} className="text-emerald-400" />;
    case 'transcode': return <Clapperboard size={14} className="text-amber-400" />;
    case 'ai_pipeline': return <Brain size={14} className="text-purple-400" />;
    default: return <AlertCircle size={14} className="text-zinc-400" />;
  }
}

function formatRelativeTime(dateStr: string | null | undefined): string {
  if (!dateStr) return '-';
  const date = new Date(dateStr);
  const now = new Date();
  const diffMs = now.getTime() - date.getTime();
  const diffSec = Math.floor(diffMs / 1000);
  if (diffSec < 60) return 'just now';
  const diffMin = Math.floor(diffSec / 60);
  if (diffMin < 60) return `${diffMin}m ago`;
  const diffHr = Math.floor(diffMin / 60);
  if (diffHr < 24) return `${diffHr}h ago`;
  const diffDay = Math.floor(diffHr / 24);
  if (diffDay < 30) return `${diffDay}d ago`;
  return date.toLocaleDateString();
}

// ─── Component ────────────────────────────────────────

export const TasksPanel: React.FC = () => {
  const {
    tasks,
    isLoading,
    activeTasks,
    cancelTask,
    retryTask,
    deleteTask,
    clearCompleted,
    refreshTasks,
  } = useTaskManager();

  const [typeFilter, setTypeFilter] = useState<TypeFilter>('all');
  const [statusFilter, setStatusFilter] = useState<StatusFilterValue>('all');
  const [isRefreshing, setIsRefreshing] = useState(false);

  // Filtered tasks
  const filteredTasks = useMemo(() => {
    let filtered = tasks;
    if (typeFilter !== 'all') {
      filtered = filtered.filter((t) => t.task_type === typeFilter);
    }
    if (statusFilter !== 'all') {
      filtered = filtered.filter((t) => t.status === statusFilter);
    }
    return filtered;
  }, [tasks, typeFilter, statusFilter]);

  // Stats derived from all tasks
  const stats = useMemo(() => {
    const counts: Record<TaskStatus, number> = {
      pending: 0,
      processing: 0,
      completed: 0,
      failed: 0,
      cancelled: 0,
    };
    for (const t of tasks) {
      if (t.status in counts) counts[t.status]++;
    }
    return counts;
  }, [tasks]);

  const hasCompleted = stats.completed > 0 || stats.failed > 0 || stats.cancelled > 0;

  const handleRefresh = async () => {
    setIsRefreshing(true);
    await refreshTasks();
    setIsRefreshing(false);
  };

  const handleDelete = async (taskId: string) => {
    if (!confirm('Delete this task?')) return;
    await deleteTask(taskId);
  };

  const handleClearCompleted = async () => {
    await clearCompleted();
  };

  if (isLoading) {
    return (
      <div className="flex items-center justify-center py-12">
        <RefreshCw size={24} className="animate-spin text-indigo-500" />
      </div>
    );
  }

  return (
    <div className="space-y-4 md:space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <div className="p-2 bg-indigo-500/10 rounded-lg">
            <ListTodo size={20} className="text-indigo-400" />
          </div>
          <div>
            <h2 className="text-lg font-semibold text-white">Task History</h2>
            <p className="text-sm text-zinc-400">
              {activeTasks.length > 0
                ? `${activeTasks.length} active · ${tasks.length} total`
                : `${tasks.length} tasks`}
            </p>
          </div>
        </div>
        <button
          onClick={handleRefresh}
          disabled={isRefreshing}
          className="p-2 hover:bg-zinc-800 rounded-lg transition-colors"
          title="Refresh"
        >
          <RefreshCw size={18} className={`text-zinc-400 ${isRefreshing ? 'animate-spin' : ''}`} />
        </button>
      </div>

      {/* Stats Cards */}
      <div className="grid grid-cols-3 md:grid-cols-5 gap-2 md:gap-3">
        {([
          { key: 'pending', label: 'Pending', icon: <Clock size={14} className="text-yellow-500" />, color: 'text-yellow-500' },
          { key: 'processing', label: 'Processing', icon: <Loader2 size={14} className="text-blue-500" />, color: 'text-blue-500' },
          { key: 'completed', label: 'Completed', icon: <CheckCircle size={14} className="text-green-500" />, color: 'text-green-500' },
          { key: 'failed', label: 'Failed', icon: <XCircle size={14} className="text-red-500" />, color: 'text-red-500' },
          { key: 'cancelled', label: 'Cancelled', icon: <Ban size={14} className="text-zinc-500" />, color: 'text-zinc-500' },
        ] as const).map(({ key, label, icon }) => (
          <button
            key={key}
            onClick={() => setStatusFilter(statusFilter === key ? 'all' : key)}
            className={`p-2.5 md:p-3 rounded-lg border transition-colors text-left ${
              statusFilter === key
                ? 'bg-zinc-800 border-indigo-500/40'
                : 'bg-zinc-900 border-zinc-800 hover:border-zinc-700'
            }`}
          >
            <div className="flex items-center gap-1.5 mb-1">
              {icon}
              <span className="text-[11px] md:text-xs text-zinc-400">{label}</span>
            </div>
            <div className="text-lg md:text-xl font-bold text-white">{stats[key]}</div>
          </button>
        ))}
      </div>

      {/* Type Filter Pills + Actions */}
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-1 p-1 bg-zinc-900 rounded-lg border border-zinc-800">
          {TYPE_OPTIONS.map((opt) => (
            <button
              key={opt.value}
              onClick={() => setTypeFilter(opt.value)}
              className={`flex items-center gap-1.5 px-3 py-1.5 rounded-md text-xs font-medium transition-colors ${
                typeFilter === opt.value
                  ? 'bg-zinc-800 text-white'
                  : 'text-zinc-500 hover:text-zinc-300 hover:bg-zinc-800/50'
              }`}
            >
              {opt.icon}
              {opt.label}
            </button>
          ))}
        </div>
        {hasCompleted && (
          <button
            onClick={handleClearCompleted}
            className="px-3 py-1.5 bg-zinc-800 text-zinc-400 rounded-lg text-xs hover:bg-zinc-700 transition-colors flex items-center gap-1.5"
          >
            <Trash2 size={13} />
            Clear Completed
          </button>
        )}
      </div>

      {/* Task Table */}
      <div className="bg-zinc-900 rounded-lg border border-zinc-800 overflow-hidden">
        <div className="overflow-x-auto max-h-[60vh] overflow-y-auto">
          <table className="w-full">
            <thead className="sticky top-0 bg-zinc-900 z-10">
              <tr className="border-b border-zinc-800 text-left">
                <th className="px-4 py-3 text-xs font-medium text-zinc-400 w-10">Type</th>
                <th className="px-4 py-3 text-xs font-medium text-zinc-400">Status</th>
                <th className="px-4 py-3 text-xs font-medium text-zinc-400">Task</th>
                <th className="px-4 py-3 text-xs font-medium text-zinc-400">Progress</th>
                <th className="px-4 py-3 text-xs font-medium text-zinc-400">Updated</th>
                <th className="px-4 py-3 text-xs font-medium text-zinc-400 w-24">Actions</th>
              </tr>
            </thead>
            <tbody>
              {filteredTasks.length === 0 ? (
                <tr>
                  <td colSpan={6} className="px-4 py-12 text-center text-zinc-500 text-sm">
                    No tasks found
                  </td>
                </tr>
              ) : (
                filteredTasks.map((task) => (
                  <TaskRow
                    key={task.id}
                    task={task}
                    onRetry={() => retryTask(task.id)}
                    onCancel={() => cancelTask(task.id)}
                    onDelete={() => handleDelete(task.id)}
                  />
                ))
              )}
            </tbody>
          </table>
        </div>
      </div>

      {/* Error Details */}
      {filteredTasks.some((t) => t.status === 'failed' && t.error_msg) && (
        <div className="space-y-2">
          <h3 className="text-xs font-medium text-zinc-400 uppercase tracking-wider">Error Details</h3>
          {filteredTasks
            .filter((t) => t.status === 'failed' && t.error_msg)
            .slice(0, 5)
            .map((task) => (
              <div key={task.id} className="p-3 bg-red-500/5 border border-red-500/20 rounded-lg">
                <div className="flex items-center gap-2">
                  {getTypeIcon(task.task_type)}
                  <span className="text-sm text-white truncate">{task.title}</span>
                </div>
                <div className="text-xs text-red-400 mt-1">{task.error_msg}</div>
              </div>
            ))}
        </div>
      )}
    </div>
  );
};

// ─── Task Row ────────────────────────────────────────

const TaskRow: React.FC<{
  task: UnifiedTask;
  onRetry: () => void;
  onCancel: () => void;
  onDelete: () => void;
}> = ({ task, onRetry, onCancel, onDelete }) => {
  const isActive = task.status === 'pending' || task.status === 'processing';

  return (
    <tr className="border-b border-zinc-800/50 hover:bg-zinc-800/30">
      {/* Type icon */}
      <td className="px-4 py-3">
        <div title={taskTypeLabel(task.task_type)}>
          {getTypeIcon(task.task_type)}
        </div>
      </td>
      {/* Status */}
      <td className="px-4 py-3">
        <div className="flex items-center gap-1.5">
          {getStatusIcon(task.status)}
          <span className={`text-xs capitalize ${getStatusColor(task.status)}`}>
            {task.status}
          </span>
        </div>
      </td>
      {/* Task title + subtitle */}
      <td className="px-4 py-3">
        <div className="max-w-[280px]">
          <div className="text-sm text-white truncate" title={task.title}>
            {task.title}
          </div>
          {task.subtitle && (
            <div className="text-[11px] text-zinc-500 truncate">{task.subtitle}</div>
          )}
        </div>
      </td>
      {/* Progress */}
      <td className="px-4 py-3">
        {task.status === 'processing' && task.progress > 0 ? (
          <div className="space-y-1">
            <div className="w-24 h-1.5 bg-zinc-800 rounded-full overflow-hidden">
              <div
                className="h-full bg-blue-500 transition-all duration-300"
                style={{ width: `${Math.min(task.progress, 100)}%` }}
              />
            </div>
            <div className="text-[11px] text-zinc-400 flex items-center gap-2">
              <span>{Math.round(task.progress)}%</span>
              {task.speed && task.speed > 0 && (
                <span className="text-zinc-500">{formatSpeed(task.speed)}</span>
              )}
              {task.total_bytes && task.total_bytes > 0 && (
                <span className="text-zinc-600">{formatFileSize(task.total_bytes)}</span>
              )}
            </div>
          </div>
        ) : task.status === 'completed' ? (
          <span className="text-xs text-green-400/70">Done</span>
        ) : task.status === 'processing' ? (
          <span className="text-xs text-blue-400 animate-pulse">Processing...</span>
        ) : (
          <span className="text-xs text-zinc-600">-</span>
        )}
      </td>
      {/* Updated */}
      <td className="px-4 py-3 text-xs text-zinc-500 whitespace-nowrap">
        {formatRelativeTime(task.updated_at || task.created_at)}
      </td>
      {/* Actions */}
      <td className="px-4 py-3">
        <div className="flex items-center gap-0.5">
          {task.status === 'failed' && (
            <button
              onClick={onRetry}
              className="p-1.5 hover:bg-zinc-700 rounded transition-colors"
              title="Retry"
            >
              <RotateCcw size={13} className="text-orange-400" />
            </button>
          )}
          {isActive && (
            <button
              onClick={onCancel}
              className="p-1.5 hover:bg-zinc-700 rounded transition-colors"
              title="Cancel"
            >
              <Ban size={13} className="text-yellow-500" />
            </button>
          )}
          <button
            onClick={onDelete}
            className="p-1.5 hover:bg-zinc-700 rounded transition-colors"
            title="Delete"
          >
            <Trash2 size={13} className="text-zinc-500 hover:text-red-400" />
          </button>
        </div>
      </td>
    </tr>
  );
};

export default TasksPanel;
