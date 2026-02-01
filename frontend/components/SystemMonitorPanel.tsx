/**
 * SystemMonitorPanel - System monitoring in Settings
 * Shows Celery queue, workers, storage, and network status
 */

import React, { useState, useEffect, useCallback } from 'react';
import {
  RefreshCw,
  Server,
  HardDrive,
  Wifi,
  ListVideo,
  CheckCircle,
  XCircle,
  Clock,
  Activity,
  Loader2,
  AlertCircle,
} from 'lucide-react';
import { getAuthHeaders } from '../services/parserService';

// API configuration
const getApiUrl = (): string => {
  // @ts-ignore
  if (typeof import.meta !== 'undefined' && 'VITE_API_URL' in import.meta.env) {
    // @ts-ignore
    return import.meta.env.VITE_API_URL || '';
  }
  return 'http://localhost:8080';
};

interface QueueStatus {
  active: number;
  pending: number;
  scheduled: number;
  status: 'online' | 'offline' | 'degraded';
}

interface StorageStatus {
  total_bytes: number;
  used_bytes: number;
  free_bytes: number;
  percent_used: number;
  status: 'ok' | 'warning' | 'critical' | 'error' | 'unknown';
  path: string;
}

interface NetworkStatus {
  speed: string;
  status: 'idle' | 'active' | 'error';
}

interface SystemStatus {
  queue: QueueStatus;
  storage: StorageStatus;
  network: NetworkStatus;
  timestamp: number;
}

interface WorkerInfo {
  name: string;
  status: string;
  concurrency: number;
  processes: number[];
  total_tasks: Record<string, number>;
}

interface WorkerStats {
  success: boolean;
  worker_count: number;
  workers: WorkerInfo[];
}

interface ActiveTask {
  task_id: string;
  name: string;
  status: string;
  worker: string;
  args: unknown[];
}

interface ActiveTasks {
  success: boolean;
  count: number;
  tasks: ActiveTask[];
}

// Format bytes to human readable
const formatBytes = (bytes: number): string => {
  if (bytes === 0) return '0 B';
  const k = 1024;
  const sizes = ['B', 'KB', 'MB', 'GB', 'TB'];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return `${parseFloat((bytes / Math.pow(k, i)).toFixed(1))} ${sizes[i]}`;
};

export const SystemMonitorPanel: React.FC = () => {
  const [systemStatus, setSystemStatus] = useState<SystemStatus | null>(null);
  const [workerStats, setWorkerStats] = useState<WorkerStats | null>(null);
  const [activeTasks, setActiveTasks] = useState<ActiveTasks | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [lastUpdate, setLastUpdate] = useState<Date | null>(null);

  const fetchData = useCallback(async () => {
    setIsLoading(true);
    setError(null);

    const apiUrl = getApiUrl();

    try {
      // Fetch all data in parallel
      const [statusRes, workersRes, tasksRes] = await Promise.all([
        fetch(`${apiUrl}/api/v1/system/status`, { headers: getAuthHeaders() }),
        fetch(`${apiUrl}/api/v1/tasks/stats/workers`, { headers: getAuthHeaders() }),
        fetch(`${apiUrl}/api/v1/tasks/`, { headers: getAuthHeaders() }),
      ]);

      if (statusRes.ok) {
        const status = await statusRes.json();
        setSystemStatus(status);
      }

      if (workersRes.ok) {
        const workers = await workersRes.json();
        setWorkerStats(workers);
      }

      if (tasksRes.ok) {
        const tasks = await tasksRes.json();
        setActiveTasks(tasks);
      }

      setLastUpdate(new Date());
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to fetch system status');
    } finally {
      setIsLoading(false);
    }
  }, []);

  // Initial fetch
  useEffect(() => {
    fetchData();
  }, [fetchData]);

  // Auto-refresh every 10 seconds
  useEffect(() => {
    const interval = setInterval(fetchData, 10000);
    return () => clearInterval(interval);
  }, [fetchData]);

  const getStatusColor = (status: string) => {
    switch (status) {
      case 'online':
      case 'ok':
      case 'active':
        return 'text-green-400';
      case 'warning':
        return 'text-yellow-400';
      case 'offline':
      case 'error':
      case 'critical':
        return 'text-red-400';
      default:
        return 'text-zinc-400';
    }
  };

  const getStatusBg = (status: string) => {
    switch (status) {
      case 'online':
      case 'ok':
      case 'active':
        return 'bg-green-500/10 border-green-500/20';
      case 'warning':
        return 'bg-yellow-500/10 border-yellow-500/20';
      case 'offline':
      case 'error':
      case 'critical':
        return 'bg-red-500/10 border-red-500/20';
      default:
        return 'bg-zinc-500/10 border-zinc-500/20';
    }
  };

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <div className="p-2 bg-emerald-500/10 rounded-lg text-emerald-400">
            <Activity size={20} />
          </div>
          <div>
            <h2 className="font-semibold text-zinc-200">System Monitor</h2>
            <p className="text-sm text-zinc-500">
              Celery workers, queue, and storage status
            </p>
          </div>
        </div>
        <div className="flex items-center gap-3">
          {lastUpdate && (
            <span className="text-xs text-zinc-500">
              Updated: {lastUpdate.toLocaleTimeString()}
            </span>
          )}
          <button
            onClick={fetchData}
            disabled={isLoading}
            className="flex items-center gap-2 px-3 py-2 rounded-lg border border-zinc-800 text-zinc-400 hover:bg-zinc-800 hover:text-zinc-200 transition-colors text-sm"
          >
            <RefreshCw size={14} className={isLoading ? 'animate-spin' : ''} />
            Refresh
          </button>
        </div>
      </div>

      {error && (
        <div className="flex items-center gap-2 p-3 bg-red-500/10 border border-red-500/20 rounded-lg text-red-400 text-sm">
          <AlertCircle size={16} />
          {error}
        </div>
      )}

      {/* Status Cards */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        {/* Queue Status */}
        <div className="p-4 bg-zinc-900/50 border border-zinc-800 rounded-xl">
          <div className="flex items-center gap-2 mb-3">
            <ListVideo size={18} className="text-indigo-400" />
            <h3 className="font-medium text-zinc-200">Queue</h3>
            {systemStatus && (
              <span className={`ml-auto px-2 py-0.5 text-xs rounded border ${getStatusBg(systemStatus.queue.status)} ${getStatusColor(systemStatus.queue.status)}`}>
                {systemStatus.queue.status}
              </span>
            )}
          </div>
          {systemStatus ? (
            <div className="space-y-2 text-sm">
              <div className="flex justify-between">
                <span className="text-zinc-500">Active Tasks</span>
                <span className="text-zinc-200 font-mono">{systemStatus.queue.active}</span>
              </div>
              <div className="flex justify-between">
                <span className="text-zinc-500">Pending</span>
                <span className="text-zinc-200 font-mono">{systemStatus.queue.pending}</span>
              </div>
              <div className="flex justify-between">
                <span className="text-zinc-500">Scheduled</span>
                <span className="text-zinc-200 font-mono">{systemStatus.queue.scheduled}</span>
              </div>
            </div>
          ) : (
            <div className="flex items-center justify-center py-4">
              <Loader2 size={20} className="animate-spin text-zinc-500" />
            </div>
          )}
        </div>

        {/* Storage Status */}
        <div className="p-4 bg-zinc-900/50 border border-zinc-800 rounded-xl">
          <div className="flex items-center gap-2 mb-3">
            <HardDrive size={18} className="text-purple-400" />
            <h3 className="font-medium text-zinc-200">Storage</h3>
            {systemStatus && (
              <span className={`ml-auto px-2 py-0.5 text-xs rounded border ${getStatusBg(systemStatus.storage.status)} ${getStatusColor(systemStatus.storage.status)}`}>
                {systemStatus.storage.status}
              </span>
            )}
          </div>
          {systemStatus ? (
            <div className="space-y-2 text-sm">
              <div className="flex justify-between">
                <span className="text-zinc-500">Used</span>
                <span className="text-zinc-200 font-mono">{formatBytes(systemStatus.storage.used_bytes)}</span>
              </div>
              <div className="flex justify-between">
                <span className="text-zinc-500">Free</span>
                <span className="text-zinc-200 font-mono">{formatBytes(systemStatus.storage.free_bytes)}</span>
              </div>
              <div className="flex justify-between">
                <span className="text-zinc-500">Total</span>
                <span className="text-zinc-200 font-mono">{formatBytes(systemStatus.storage.total_bytes)}</span>
              </div>
              {/* Progress bar */}
              <div className="mt-2">
                <div className="h-2 bg-zinc-800 rounded-full overflow-hidden">
                  <div
                    className={`h-full transition-all ${
                      systemStatus.storage.percent_used > 90 ? 'bg-red-500' :
                      systemStatus.storage.percent_used > 75 ? 'bg-yellow-500' : 'bg-purple-500'
                    }`}
                    style={{ width: `${systemStatus.storage.percent_used}%` }}
                  />
                </div>
                <div className="text-xs text-zinc-500 mt-1 text-right">
                  {systemStatus.storage.percent_used}% used
                </div>
              </div>
            </div>
          ) : (
            <div className="flex items-center justify-center py-4">
              <Loader2 size={20} className="animate-spin text-zinc-500" />
            </div>
          )}
        </div>

        {/* Network Status */}
        <div className="p-4 bg-zinc-900/50 border border-zinc-800 rounded-xl">
          <div className="flex items-center gap-2 mb-3">
            <Wifi size={18} className="text-emerald-400" />
            <h3 className="font-medium text-zinc-200">Network</h3>
            {systemStatus && (
              <span className={`ml-auto px-2 py-0.5 text-xs rounded border ${getStatusBg(systemStatus.network.status)} ${getStatusColor(systemStatus.network.status)}`}>
                {systemStatus.network.status}
              </span>
            )}
          </div>
          {systemStatus ? (
            <div className="space-y-2 text-sm">
              <div className="flex justify-between">
                <span className="text-zinc-500">Download Speed</span>
                <span className="text-zinc-200 font-mono">{systemStatus.network.speed}</span>
              </div>
              <div className="flex justify-between">
                <span className="text-zinc-500">Status</span>
                <span className={`font-medium ${getStatusColor(systemStatus.network.status)}`}>
                  {systemStatus.network.status === 'active' ? 'Downloading' : 'Idle'}
                </span>
              </div>
            </div>
          ) : (
            <div className="flex items-center justify-center py-4">
              <Loader2 size={20} className="animate-spin text-zinc-500" />
            </div>
          )}
        </div>
      </div>

      {/* Workers Section */}
      <div className="p-4 bg-zinc-900/50 border border-zinc-800 rounded-xl">
        <div className="flex items-center gap-2 mb-4">
          <Server size={18} className="text-blue-400" />
          <h3 className="font-medium text-zinc-200">Celery Workers</h3>
          {workerStats && (
            <span className="ml-auto text-sm text-zinc-500">
              {workerStats.worker_count} worker(s)
            </span>
          )}
        </div>

        {workerStats?.workers && workerStats.workers.length > 0 ? (
          <div className="space-y-3">
            {workerStats.workers.map((worker) => (
              <div key={worker.name} className="flex items-center gap-3 p-3 bg-zinc-950 rounded-lg">
                <div className={`w-2 h-2 rounded-full ${worker.status === 'online' ? 'bg-green-500' : 'bg-red-500'}`} />
                <div className="flex-1 min-w-0">
                  <div className="text-sm text-zinc-200 truncate font-mono">{worker.name}</div>
                  <div className="text-xs text-zinc-500">
                    Concurrency: {worker.concurrency} | Processes: {worker.processes?.length || 0}
                  </div>
                </div>
                <div className={`px-2 py-1 text-xs rounded ${worker.status === 'online' ? 'bg-green-500/10 text-green-400' : 'bg-red-500/10 text-red-400'}`}>
                  {worker.status}
                </div>
              </div>
            ))}
          </div>
        ) : (
          <div className="text-center py-6 text-zinc-500">
            {isLoading ? (
              <Loader2 size={20} className="animate-spin mx-auto" />
            ) : (
              <>
                <XCircle size={24} className="mx-auto mb-2 opacity-50" />
                <p className="text-sm">No workers connected</p>
              </>
            )}
          </div>
        )}
      </div>

      {/* Active Tasks Section */}
      <div className="p-4 bg-zinc-900/50 border border-zinc-800 rounded-xl">
        <div className="flex items-center gap-2 mb-4">
          <Clock size={18} className="text-amber-400" />
          <h3 className="font-medium text-zinc-200">Active Tasks</h3>
          {activeTasks && (
            <span className="ml-auto text-sm text-zinc-500">
              {activeTasks.count} task(s)
            </span>
          )}
        </div>

        {activeTasks?.tasks && activeTasks.tasks.length > 0 ? (
          <div className="space-y-2 max-h-64 overflow-y-auto">
            {activeTasks.tasks.map((task) => (
              <div key={task.task_id} className="flex items-center gap-3 p-3 bg-zinc-950 rounded-lg">
                <div className="w-2 h-2 rounded-full bg-indigo-500 animate-pulse" />
                <div className="flex-1 min-w-0">
                  <div className="text-sm text-zinc-200 truncate">
                    {task.name?.split('.').pop() || 'Unknown'}
                  </div>
                  <div className="text-xs text-zinc-500 font-mono truncate">
                    {task.task_id}
                  </div>
                </div>
                <div className="px-2 py-1 text-xs rounded bg-indigo-500/10 text-indigo-400">
                  {task.status}
                </div>
              </div>
            ))}
          </div>
        ) : (
          <div className="text-center py-6 text-zinc-500">
            {isLoading ? (
              <Loader2 size={20} className="animate-spin mx-auto" />
            ) : (
              <>
                <CheckCircle size={24} className="mx-auto mb-2 opacity-50" />
                <p className="text-sm">No active tasks</p>
              </>
            )}
          </div>
        )}
      </div>

      {/* Storage Path */}
      {systemStatus?.storage.path && (
        <div className="text-xs text-zinc-600 text-right">
          Storage path: {systemStatus.storage.path}
        </div>
      )}
    </div>
  );
};

export default SystemMonitorPanel;
