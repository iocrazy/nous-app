// components/TasksPanel.tsx — Unified task history (all task types) + AI Tasks tab + Flows tab (A6)

import React, { useState, useMemo, useEffect, useCallback } from 'react';
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
  Mic,
  FileText,
  Workflow,
} from 'lucide-react';
import { FlowCard } from './TaskManager/FlowCard';
import { flowService, type FlowResponse } from '../services/flowService';
import {
  useTaskManager,
  TaskType,
  TaskStatus,
  TaskPhase,
  UnifiedTask,
  formatSpeed,
  formatFileSize,
  taskTypeLabel,
  taskPhaseLabel,
  isRetryable,
  errorCodeMessage,
  getTaskCategory,
} from '../contexts/TaskManagerContext';
// ─── Helpers ──────────────────────────────────────────

type MainTab = 'history' | 'transfer' | 'ai' | 'flows';
type SubFilter = string; // 'all' | specific task_type
type StatusFilterValue = 'all' | TaskStatus;

interface TabConfig {
  value: MainTab;
  label: string;
  icon: React.ReactNode;
  activeClass: string;   // selected state
  inactiveClass: string; // unselected state
}

const MAIN_TABS: TabConfig[] = [
  {
    value: 'history',
    label: 'Task History',
    icon: <ListTodo size={15} />,
    activeClass: 'bg-zinc-700 text-white',
    inactiveClass: 'text-zinc-500 hover:text-zinc-300 hover:bg-zinc-800/50',
  },
  {
    value: 'transfer',
    label: 'Transfer',
    icon: <Download size={15} />,
    activeClass: 'bg-blue-500/15 text-blue-400 ring-1 ring-blue-500/30',
    inactiveClass: 'text-zinc-500 hover:text-blue-400/70 hover:bg-blue-500/5',
  },
  {
    value: 'ai',
    label: 'AI Tasks',
    icon: <Brain size={15} />,
    activeClass: 'bg-purple-500/15 text-purple-400 ring-1 ring-purple-500/30',
    inactiveClass: 'text-zinc-500 hover:text-purple-400/70 hover:bg-purple-500/5',
  },
  {
    value: 'flows',
    label: 'Flows',
    icon: <Workflow size={15} />,
    activeClass: 'bg-emerald-500/15 text-emerald-400 ring-1 ring-emerald-500/30',
    inactiveClass: 'text-zinc-500 hover:text-emerald-400/70 hover:bg-emerald-500/5',
  },
];

interface SubFilterOption {
  value: SubFilter;
  label: string;
  icon: React.ReactNode;
}

const TRANSFER_SUB_FILTERS: SubFilterOption[] = [
  { value: 'all', label: 'All', icon: <ListTodo size={14} /> },
  { value: 'download', label: 'Download', icon: <Download size={14} /> },
  { value: 'upload', label: 'Upload', icon: <Upload size={14} /> },
  { value: 'transcode', label: 'Transcode', icon: <Clapperboard size={14} /> },
];

const AI_SUB_FILTERS: SubFilterOption[] = [
  { value: 'all', label: 'All', icon: <Brain size={14} /> },
  { value: 'ai_extract', label: 'Extract', icon: <Mic size={14} /> },
  { value: 'ai_transcription', label: 'Transcription', icon: <FileText size={14} /> },
  { value: 'ai_summary', label: 'Summary', icon: <Brain size={14} /> },
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
    case 'ai_extract': return <Mic size={14} className="text-violet-400" />;
    case 'ai_transcription': return <FileText size={14} className="text-fuchsia-400" />;
    case 'ai_summary': return <Brain size={14} className="text-cyan-400" />;
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

// ─── Main Component ──────────────────────────────────

export const TasksPanel: React.FC = () => <UnifiedTasksView />;

// ─── Unified Tasks View ───────────────────────────────

const UnifiedTasksView: React.FC = () => {
  const {
    tasks,
    isLoading,
    cancelTask,
    retryTask,
    deleteTask,
    clearCompleted,
    refreshTasks,
  } = useTaskManager();

  const [mainTab, setMainTab] = useState<MainTab>('history');
  const [subFilter, setSubFilter] = useState<SubFilter>('all');
  const [statusFilter, setStatusFilter] = useState<StatusFilterValue>('all');
  const [isRefreshing, setIsRefreshing] = useState(false);

  // A6 Flows tab state — lazily fetched when user switches to the tab.
  const [flows, setFlows] = useState<FlowResponse[]>([]);
  const [flowsLoading, setFlowsLoading] = useState(false);
  const [flowsError, setFlowsError] = useState<string | null>(null);

  const refreshFlows = useCallback(async () => {
    setFlowsLoading(true);
    setFlowsError(null);
    try {
      const list = await flowService.list();
      setFlows(list);
    } catch (err) {
      setFlowsError(err instanceof Error ? err.message : 'Failed to load flows');
    } finally {
      setFlowsLoading(false);
    }
  }, []);

  useEffect(() => {
    if (mainTab === 'flows') {
      void refreshFlows();
    }
  }, [mainTab, refreshFlows]);

  // Reset sub-filter when switching main tab
  const handleTabChange = (tab: MainTab) => {
    setMainTab(tab);
    setSubFilter('all');
    setStatusFilter('all');
  };

  // Tasks scoped to the current main tab
  const tabTasks = useMemo(() => {
    switch (mainTab) {
      case 'transfer':
        return tasks.filter((t) => {
          const cat = getTaskCategory(t.task_type);
          return cat === 'transfer' || cat === 'processing'; // download/upload/transcode
        });
      case 'ai':
        return tasks.filter((t) => getTaskCategory(t.task_type) === 'ai');
      default:
        return tasks;
    }
  }, [tasks, mainTab]);

  // Further filtered by sub-filter and status
  const filteredTasks = useMemo(() => {
    let filtered = tabTasks;
    if (subFilter !== 'all') {
      filtered = filtered.filter((t) => t.task_type === subFilter);
    }
    if (statusFilter !== 'all') {
      filtered = filtered.filter((t) => t.status === statusFilter);
    }
    return filtered;
  }, [tabTasks, subFilter, statusFilter]);

  // Stats derived from tab-scoped tasks
  const stats = useMemo(() => {
    const counts: Record<TaskStatus, number> = {
      pending: 0,
      processing: 0,
      completed: 0,
      failed: 0,
      cancelled: 0,
    };
    for (const t of tabTasks) {
      if (t.status in counts) counts[t.status]++;
    }
    return counts;
  }, [tabTasks]);

  const activeTasks = tabTasks.filter(
    (t) => t.status === 'pending' || t.status === 'processing'
  );
  const hasCompleted = stats.completed > 0 || stats.failed > 0 || stats.cancelled > 0;

  // Sub-filter options based on current tab
  const currentSubFilters: SubFilterOption[] | null = mainTab === 'transfer'
    ? TRANSFER_SUB_FILTERS
    : mainTab === 'ai'
      ? AI_SUB_FILTERS
      : null;

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

  // A6 Flows tab: completely separate render path — flow-grouped UI with FlowCard.
  // Renders the same header (tabs + refresh) but skips status/sub-filter pills since
  // a flow's lifecycle is aggregated, not filtered by individual task status.
  if (mainTab === 'flows') {
    return (
      <div className="space-y-4 md:space-y-6">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-1 p-1 bg-zinc-900 rounded-lg border border-zinc-800">
            {MAIN_TABS.map((tab) => (
              <button
                key={tab.value}
                onClick={() => handleTabChange(tab.value)}
                className={`flex items-center gap-2 px-4 py-2 rounded-md text-sm font-medium transition-all ${
                  mainTab === tab.value ? tab.activeClass : tab.inactiveClass
                }`}
              >
                {tab.icon}
                {tab.label}
              </button>
            ))}
          </div>
          <div className="flex items-center gap-2">
            <p className="text-xs text-zinc-500">{flows.length} flows</p>
            <button
              onClick={() => { void refreshFlows(); }}
              disabled={flowsLoading}
              className="p-2 hover:bg-zinc-800 rounded-lg transition-colors"
              title="Refresh"
            >
              <RefreshCw size={18} className={`text-zinc-400 ${flowsLoading ? 'animate-spin' : ''}`} />
            </button>
          </div>
        </div>

        {flowsError && (
          <div className="text-xs text-rose-400 bg-rose-900/20 px-3 py-2 rounded">
            {flowsError}
          </div>
        )}
        {flowsLoading && flows.length === 0 ? (
          <div className="flex items-center justify-center py-12">
            <RefreshCw size={24} className="animate-spin text-emerald-500" />
          </div>
        ) : flows.length === 0 ? (
          <div className="text-sm text-zinc-500 italic px-3 py-12 text-center">
            No flows yet. Flows group related tasks (e.g. parse → download → transcribe → summary).
          </div>
        ) : (
          <div className="space-y-2">
            {flows.map((f) => (
              <FlowCard
                key={f.id}
                flow={f}
                onCancelled={() => { void refreshFlows(); }}
              />
            ))}
          </div>
        )}
      </div>
    );
  }

  return (
    <div className="space-y-4 md:space-y-6">
      {/* Header with Main Tabs */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-1 p-1 bg-zinc-900 rounded-lg border border-zinc-800">
          {MAIN_TABS.map((tab) => (
            <button
              key={tab.value}
              onClick={() => handleTabChange(tab.value)}
              className={`flex items-center gap-2 px-4 py-2 rounded-md text-sm font-medium transition-all ${
                mainTab === tab.value ? tab.activeClass : tab.inactiveClass
              }`}
            >
              {tab.icon}
              {tab.label}
            </button>
          ))}
        </div>
        <div className="flex items-center gap-2">
          <p className="text-xs text-zinc-500">
            {/* Flows tab handled by the early-return branch above; here mainTab ∈ history|transfer|ai */}
            {activeTasks.length > 0
              ? `${activeTasks.length} active · ${tabTasks.length} total`
              : `${tabTasks.length} tasks`}
          </p>
          <button
            onClick={handleRefresh}
            disabled={isRefreshing}
            className="p-2 hover:bg-zinc-800 rounded-lg transition-colors"
            title="Refresh"
          >
            <RefreshCw size={18} className={`text-zinc-400 ${isRefreshing ? 'animate-spin' : ''}`} />
          </button>
        </div>
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

      {/* Sub-filter Pills + Actions */}
      <div className="flex flex-wrap items-center justify-between gap-2">
        {currentSubFilters && (
          <div className="flex items-center gap-1 p-1 bg-zinc-900 rounded-lg border border-zinc-800">
            {currentSubFilters.map((opt) => (
              <button
                key={opt.value}
                onClick={() => setSubFilter(opt.value)}
                className={`flex items-center gap-1.5 px-3 py-1.5 rounded-md text-xs font-medium transition-colors ${
                  subFilter === opt.value
                    ? 'bg-zinc-800 text-white'
                    : 'text-zinc-500 hover:text-zinc-300 hover:bg-zinc-800/50'
                }`}
              >
                {opt.icon}
                {opt.label}
              </button>
            ))}
          </div>
        )}
        {hasCompleted && (
          <button
            onClick={handleClearCompleted}
            className={`px-3 py-1.5 bg-zinc-800 text-zinc-400 rounded-lg text-xs hover:bg-zinc-700 transition-colors flex items-center gap-1.5 ${!currentSubFilters ? 'ml-auto' : ''}`}
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
            {task.phase === 'dedup_check' ? taskPhaseLabel(task.phase) : task.status}
          </span>
          {task.phase === 'dedup_check' && (
            <span className="text-[10px] text-blue-400 animate-pulse">●</span>
          )}
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
          {task.status === 'failed' && task.error_code && (
            <div className="text-[11px] text-red-400/70 truncate">
              {errorCodeMessage(task.error_code)}
            </div>
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
          {task.status === 'failed' && isRetryable(task.error_code) && (
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
