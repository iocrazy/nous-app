// components/TasksPanel.tsx

import React, { useState, useEffect, useCallback } from 'react';
import {
  ListTodo,
  Clock,
  Download,
  CheckCircle,
  XCircle,
  RefreshCw,
  Trash2,
  AlertCircle,
  Server,
  HardDrive,
  Filter,
  RotateCcw,
} from 'lucide-react';
import {
  getTaskManagerTasks,
  getTaskManagerStats,
  retryFailedTask,
  retryAllFailedTasks,
  deleteTaskManagerTask,
  cleanupCompletedTasks,
  formatBytes,
  formatRelativeTime,
  TaskManagerItem,
  TaskManagerStats,
} from '../services/taskService';

export const TasksPanel: React.FC = () => {
  const [tasks, setTasks] = useState<TaskManagerItem[]>([]);
  const [stats, setStats] = useState<TaskManagerStats | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [statusFilter, setStatusFilter] = useState<string>('');
  const [isRefreshing, setIsRefreshing] = useState(false);

  const fetchData = useCallback(async () => {
    try {
      setIsRefreshing(true);
      const [tasksRes, statsRes] = await Promise.all([
        getTaskManagerTasks(statusFilter || undefined),
        getTaskManagerStats(),
      ]);
      setTasks(tasksRes.items);
      setStats(statsRes);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to fetch data');
    } finally {
      setLoading(false);
      setIsRefreshing(false);
    }
  }, [statusFilter]);

  useEffect(() => {
    fetchData();
    const interval = setInterval(fetchData, 5000);
    return () => clearInterval(interval);
  }, [fetchData]);

  const handleRetry = async (platformId: string, force: boolean = false) => {
    try {
      await retryFailedTask(platformId, force);
      fetchData();
    } catch (err) {
      console.error('Failed to retry task:', err);
    }
  };

  const handleRetryAll = async (force: boolean = false) => {
    try {
      const result = await retryAllFailedTasks(force);
      alert(`Retried ${result.retried_count} tasks`);
      fetchData();
    } catch (err) {
      console.error('Failed to retry all tasks:', err);
    }
  };

  const handleDelete = async (platformId: string) => {
    if (!confirm('Delete this task?')) return;
    try {
      await deleteTaskManagerTask(platformId);
      fetchData();
    } catch (err) {
      console.error('Failed to delete task:', err);
    }
  };

  const handleCleanup = async () => {
    try {
      const result = await cleanupCompletedTasks(50);
      alert(`Removed ${result.removed_count} completed tasks`);
      fetchData();
    } catch (err) {
      console.error('Failed to cleanup:', err);
    }
  };

  const getStatusIcon = (status: string) => {
    switch (status) {
      case 'pending':
        return <Clock size={16} className="text-yellow-500" />;
      case 'downloading':
        return <Download size={16} className="text-blue-500 animate-pulse" />;
      case 'completed':
        return <CheckCircle size={16} className="text-green-500" />;
      case 'failed':
        return <XCircle size={16} className="text-red-500" />;
      default:
        return <AlertCircle size={16} className="text-zinc-500" />;
    }
  };

  const getStatusColor = (status: string) => {
    switch (status) {
      case 'pending':
        return 'text-yellow-500';
      case 'downloading':
        return 'text-blue-500';
      case 'completed':
        return 'text-green-500';
      case 'failed':
        return 'text-red-500';
      default:
        return 'text-zinc-500';
    }
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center py-12">
        <RefreshCw size={24} className="animate-spin text-indigo-500" />
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <div className="p-2 bg-indigo-500/10 rounded-lg">
            <ListTodo size={20} className="text-indigo-400" />
          </div>
          <div>
            <h2 className="text-lg font-semibold text-white">Download Tasks</h2>
            <p className="text-sm text-zinc-400">Monitor and manage download queue</p>
          </div>
        </div>
        <button
          onClick={() => fetchData()}
          disabled={isRefreshing}
          className="p-2 hover:bg-zinc-800 rounded-lg transition-colors"
        >
          <RefreshCw size={18} className={`text-zinc-400 ${isRefreshing ? 'animate-spin' : ''}`} />
        </button>
      </div>

      {error && (
        <div className="p-4 bg-red-500/10 border border-red-500/20 rounded-lg text-red-400">
          {error}
        </div>
      )}

      {/* Stats Cards */}
      {stats && (
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
          <div className="p-4 bg-zinc-900 rounded-lg border border-zinc-800">
            <div className="flex items-center gap-2 mb-2">
              <Clock size={16} className="text-yellow-500" />
              <span className="text-sm text-zinc-400">Pending</span>
            </div>
            <div className="text-2xl font-bold text-white">{stats.pending}</div>
          </div>
          <div className="p-4 bg-zinc-900 rounded-lg border border-zinc-800">
            <div className="flex items-center gap-2 mb-2">
              <Download size={16} className="text-blue-500" />
              <span className="text-sm text-zinc-400">Downloading</span>
            </div>
            <div className="text-2xl font-bold text-white">{stats.downloading}</div>
          </div>
          <div className="p-4 bg-zinc-900 rounded-lg border border-zinc-800">
            <div className="flex items-center gap-2 mb-2">
              <CheckCircle size={16} className="text-green-500" />
              <span className="text-sm text-zinc-400">Completed</span>
            </div>
            <div className="text-2xl font-bold text-white">{stats.completed}</div>
          </div>
          <div className="p-4 bg-zinc-900 rounded-lg border border-zinc-800">
            <div className="flex items-center gap-2 mb-2">
              <XCircle size={16} className="text-red-500" />
              <span className="text-sm text-zinc-400">Failed</span>
            </div>
            <div className="text-2xl font-bold text-white">{stats.failed}</div>
          </div>
        </div>
      )}

      {/* System Status */}
      {stats && (
        <div className="flex flex-wrap gap-4 p-4 bg-zinc-900 rounded-lg border border-zinc-800">
          <div className="flex items-center gap-2">
            <Server size={16} className={stats.worker_online ? 'text-green-500' : 'text-red-500'} />
            <span className="text-sm text-zinc-400">Celery Worker:</span>
            <span className={`text-sm font-medium ${stats.worker_online ? 'text-green-500' : 'text-red-500'}`}>
              {stats.worker_online ? 'Online' : 'Offline'}
            </span>
          </div>
          <div className="flex items-center gap-2">
            <HardDrive size={16} className="text-amber-500" />
            <span className="text-sm text-zinc-400">Storage:</span>
            <span className="text-sm font-medium text-amber-500">{stats.storage_free} Free</span>
            <span className="text-xs text-zinc-500">({stats.storage_used_percent}% used)</span>
          </div>
        </div>
      )}

      {/* Filter & Actions */}
      <div className="flex flex-wrap items-center justify-between gap-4">
        <div className="flex items-center gap-2">
          <Filter size={16} className="text-zinc-400" />
          <select
            value={statusFilter}
            onChange={(e) => setStatusFilter(e.target.value)}
            className="px-3 py-2 bg-zinc-900 border border-zinc-800 rounded-lg text-sm text-white focus:outline-none focus:border-indigo-500"
          >
            <option value="">All Status</option>
            <option value="pending">Pending</option>
            <option value="downloading">Downloading</option>
            <option value="completed">Completed</option>
            <option value="failed">Failed</option>
          </select>
        </div>
        <div className="flex items-center gap-2">
          {stats && stats.failed > 0 && (
            <button
              onClick={() => handleRetryAll(false)}
              className="px-3 py-2 bg-orange-500/10 text-orange-400 rounded-lg text-sm hover:bg-orange-500/20 transition-colors flex items-center gap-2"
            >
              <RotateCcw size={14} />
              Retry All Failed
            </button>
          )}
          {stats && stats.completed > 50 && (
            <button
              onClick={handleCleanup}
              className="px-3 py-2 bg-zinc-800 text-zinc-400 rounded-lg text-sm hover:bg-zinc-700 transition-colors flex items-center gap-2"
            >
              <Trash2 size={14} />
              Cleanup
            </button>
          )}
        </div>
      </div>

      {/* Task List */}
      <div className="bg-zinc-900 rounded-lg border border-zinc-800 overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full">
            <thead>
              <tr className="border-b border-zinc-800 text-left">
                <th className="px-4 py-3 text-sm font-medium text-zinc-400">Status</th>
                <th className="px-4 py-3 text-sm font-medium text-zinc-400">Video</th>
                <th className="px-4 py-3 text-sm font-medium text-zinc-400">Progress</th>
                <th className="px-4 py-3 text-sm font-medium text-zinc-400">Speed</th>
                <th className="px-4 py-3 text-sm font-medium text-zinc-400">Updated</th>
                <th className="px-4 py-3 text-sm font-medium text-zinc-400">Actions</th>
              </tr>
            </thead>
            <tbody>
              {tasks.length === 0 ? (
                <tr>
                  <td colSpan={6} className="px-4 py-8 text-center text-zinc-500">
                    No tasks found
                  </td>
                </tr>
              ) : (
                tasks.map((task) => (
                  <tr key={task.platform_id} className="border-b border-zinc-800/50 hover:bg-zinc-800/30">
                    <td className="px-4 py-3">
                      <div className="flex items-center gap-2">
                        {getStatusIcon(task.status)}
                        <span className={`text-sm capitalize ${getStatusColor(task.status)}`}>
                          {task.status}
                        </span>
                      </div>
                    </td>
                    <td className="px-4 py-3">
                      <div className="max-w-[200px] truncate text-sm text-white" title={task.title}>
                        {task.title}
                      </div>
                      <div className="text-xs text-zinc-500">{task.platform_id}</div>
                    </td>
                    <td className="px-4 py-3">
                      {task.status === 'downloading' ? (
                        <div className="space-y-1">
                          <div className="w-24 h-2 bg-zinc-800 rounded-full overflow-hidden">
                            <div
                              className="h-full bg-blue-500 transition-all"
                              style={{ width: `${task.percent}%` }}
                            />
                          </div>
                          <div className="text-xs text-zinc-400">
                            {task.percent}% ({formatBytes(task.downloaded)} / {formatBytes(task.total)})
                          </div>
                        </div>
                      ) : task.status === 'failed' ? (
                        <div className="text-xs text-red-400">
                          Retry {task.retry_count}/{task.max_retries}
                        </div>
                      ) : task.status === 'completed' ? (
                        <span className="text-xs text-green-400">100%</span>
                      ) : (
                        <span className="text-xs text-zinc-500">-</span>
                      )}
                    </td>
                    <td className="px-4 py-3 text-sm text-zinc-400">
                      {task.status === 'downloading' ? task.speed : '-'}
                    </td>
                    <td className="px-4 py-3 text-sm text-zinc-500">
                      {formatRelativeTime(task.updated_at)}
                    </td>
                    <td className="px-4 py-3">
                      <div className="flex items-center gap-1">
                        {task.status === 'failed' && (
                          <button
                            onClick={() => handleRetry(task.platform_id, task.retry_count >= task.max_retries)}
                            className="p-1.5 hover:bg-zinc-700 rounded transition-colors"
                            title="Retry"
                          >
                            <RotateCcw size={14} className="text-orange-400" />
                          </button>
                        )}
                        <button
                          onClick={() => handleDelete(task.platform_id)}
                          className="p-1.5 hover:bg-zinc-700 rounded transition-colors"
                          title="Delete"
                        >
                          <Trash2 size={14} className="text-zinc-400 hover:text-red-400" />
                        </button>
                      </div>
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </div>

      {/* Error Details */}
      {tasks.some((t) => t.status === 'failed' && t.error) && (
        <div className="space-y-2">
          <h3 className="text-sm font-medium text-zinc-400">Error Details</h3>
          {tasks
            .filter((t) => t.status === 'failed' && t.error)
            .slice(0, 5)
            .map((task) => (
              <div key={task.platform_id} className="p-3 bg-red-500/5 border border-red-500/20 rounded-lg">
                <div className="text-sm text-white truncate">{task.title}</div>
                <div className="text-xs text-red-400 mt-1">{task.error}</div>
              </div>
            ))}
        </div>
      )}
    </div>
  );
};

export default TasksPanel;
