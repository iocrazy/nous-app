// frontend/services/taskService.ts

/**
 * Celery 任务服务
 *
 * 提供任务状态查询、轮询等功能
 */

import { getAuthHeaders } from './parserService';

// 空字符串表示使用相对路径（通过 Vite 代理）
const API_BASE = 'VITE_API_URL' in import.meta.env ? (import.meta.env.VITE_API_URL || '') : 'http://localhost:8080';

// 任务状态类型
export type TaskStatus = 'PENDING' | 'STARTED' | 'SUCCESS' | 'FAILURE' | 'RETRY' | 'REVOKED';

// 任务状态响应
export interface TaskStatusResponse {
  task_id: string;
  status: TaskStatus;
  result?: Record<string, unknown>;
  error?: string;
  progress?: number;
}

// 活跃任务
export interface ActiveTask {
  task_id: string;
  name: string;
  status: string;
  worker?: string;
  args?: unknown[];
  eta?: string;
}

// Worker 统计
export interface WorkerStats {
  name: string;
  status: 'online' | 'offline';
  concurrency?: number;
  processes?: number[];
  total_tasks?: Record<string, number>;
}

// 队列统计
export interface QueueStats {
  [queue: string]: number;
}

/**
 * 获取任务状态
 */
export const getTaskStatus = async (taskId: string): Promise<TaskStatusResponse> => {
  const response = await fetch(`${API_BASE}/api/v1/tasks/${taskId}`, {
    method: 'GET',
    headers: getAuthHeaders(),
  });

  if (!response.ok) {
    throw new Error(`获取任务状态失败: ${response.status}`);
  }

  return response.json();
};

/**
 * 取消任务
 */
export const cancelTask = async (taskId: string): Promise<{ success: boolean; message: string }> => {
  const response = await fetch(`${API_BASE}/api/v1/tasks/${taskId}`, {
    method: 'DELETE',
    headers: getAuthHeaders(),
  });

  if (!response.ok) {
    throw new Error(`取消任务失败: ${response.status}`);
  }

  return response.json();
};

/**
 * 获取活跃任务列表
 */
export const getActiveTasks = async (queue?: string, limit: number = 100): Promise<{
  success: boolean;
  count: number;
  tasks: ActiveTask[];
}> => {
  const params = new URLSearchParams();
  if (queue) params.append('queue', queue);
  params.append('limit', limit.toString());

  const response = await fetch(`${API_BASE}/api/v1/tasks/?${params}`, {
    method: 'GET',
    headers: getAuthHeaders(),
  });

  if (!response.ok) {
    throw new Error(`获取活跃任务失败: ${response.status}`);
  }

  return response.json();
};

/**
 * 获取 Worker 统计
 */
export const getWorkerStats = async (): Promise<{
  success: boolean;
  worker_count: number;
  workers: WorkerStats[];
}> => {
  const response = await fetch(`${API_BASE}/api/v1/tasks/stats/workers`, {
    method: 'GET',
    headers: getAuthHeaders(),
  });

  if (!response.ok) {
    throw new Error(`获取 Worker 统计失败: ${response.status}`);
  }

  return response.json();
};

/**
 * 获取队列统计
 */
export const getQueueStats = async (): Promise<{
  success: boolean;
  queues: QueueStats;
  total: number;
}> => {
  const response = await fetch(`${API_BASE}/api/v1/tasks/stats/queues`, {
    method: 'GET',
    headers: getAuthHeaders(),
  });

  if (!response.ok) {
    throw new Error(`获取队列统计失败: ${response.status}`);
  }

  return response.json();
};

/**
 * 任务轮询回调类型
 */
export type TaskPollCallback = (status: TaskStatusResponse) => void;

/**
 * 轮询任务状态
 *
 * @param taskId 任务 ID
 * @param onUpdate 状态更新回调
 * @param interval 轮询间隔（毫秒）
 * @returns 停止轮询的函数
 */
export const pollTaskStatus = (
  taskId: string,
  onUpdate: TaskPollCallback,
  interval: number = 2000
): (() => void) => {
  let isPolling = true;

  const poll = async () => {
    if (!isPolling) return;

    try {
      const status = await getTaskStatus(taskId);
      onUpdate(status);

      // 如果任务完成或失败，停止轮询
      if (['SUCCESS', 'FAILURE', 'REVOKED'].includes(status.status)) {
        isPolling = false;
        return;
      }

      // 继续轮询
      if (isPolling) {
        setTimeout(poll, interval);
      }
    } catch (error) {
      console.error('轮询任务状态失败:', error);
      // 出错时继续轮询
      if (isPolling) {
        setTimeout(poll, interval * 2); // 出错时延长间隔
      }
    }
  };

  // 立即开始轮询
  poll();

  // 返回停止函数
  return () => {
    isPolling = false;
  };
};

/**
 * 批量轮询多个任务
 *
 * @param taskIds 任务 ID 列表
 * @param onUpdate 状态更新回调（单个任务）
 * @param onComplete 全部完成回调
 * @param interval 轮询间隔
 * @returns 停止轮询的函数
 */
export const pollMultipleTasks = (
  taskIds: string[],
  onUpdate: (taskId: string, status: TaskStatusResponse) => void,
  onComplete?: (results: Map<string, TaskStatusResponse>) => void,
  interval: number = 2000
): (() => void) => {
  const results = new Map<string, TaskStatusResponse>();
  const stopFunctions: (() => void)[] = [];

  taskIds.forEach((taskId) => {
    const stop = pollTaskStatus(
      taskId,
      (status) => {
        results.set(taskId, status);
        onUpdate(taskId, status);

        // 检查是否全部完成
        if (results.size === taskIds.length) {
          const allDone = Array.from(results.values()).every(
            (s) => ['SUCCESS', 'FAILURE', 'REVOKED'].includes(s.status)
          );

          if (allDone && onComplete) {
            onComplete(results);
          }
        }
      },
      interval
    );

    stopFunctions.push(stop);
  });

  // 返回停止所有轮询的函数
  return () => {
    stopFunctions.forEach((stop) => stop());
  };
};

/**
 * 获取任务状态的显示文本
 */
export const getTaskStatusText = (status: TaskStatus): string => {
  const statusMap: Record<TaskStatus, string> = {
    PENDING: '排队中',
    STARTED: '执行中',
    SUCCESS: '已完成',
    FAILURE: '失败',
    RETRY: '重试中',
    REVOKED: '已取消',
  };
  return statusMap[status] || status;
};

/**
 * 获取任务状态的颜色类
 */
export const getTaskStatusColor = (status: TaskStatus): string => {
  const colorMap: Record<TaskStatus, string> = {
    PENDING: 'text-yellow-500',
    STARTED: 'text-blue-500',
    SUCCESS: 'text-green-500',
    FAILURE: 'text-red-500',
    RETRY: 'text-orange-500',
    REVOKED: 'text-gray-500',
  };
  return colorMap[status] || 'text-gray-500';
};

// Download progress response
export interface DownloadProgressResponse {
  task_id: string;
  status: 'pending' | 'downloading' | 'completed' | 'failed';
  percent: number;
  downloaded?: number;
  total?: number;
  speed?: string;
  error?: string;
}

/**
 * Get download progress for a task
 */
export const getDownloadProgress = async (taskId: string): Promise<DownloadProgressResponse> => {
  const response = await fetch(`${API_BASE}/api/v1/tasks/${taskId}/progress`, {
    method: 'GET',
    headers: getAuthHeaders(),
  });

  if (!response.ok) {
    throw new Error(`Failed to get download progress: ${response.status}`);
  }

  return response.json();
};

/**
 * Poll download progress
 */
export const pollDownloadProgress = (
  taskId: string,
  onUpdate: (progress: DownloadProgressResponse) => void,
  interval: number = 1000
): (() => void) => {
  let isPolling = true;

  const poll = async () => {
    if (!isPolling) return;

    try {
      const progress = await getDownloadProgress(taskId);
      onUpdate(progress);

      // Stop polling if completed or failed
      if (['completed', 'failed'].includes(progress.status)) {
        isPolling = false;
        return;
      }

      if (isPolling) {
        setTimeout(poll, interval);
      }
    } catch (error) {
      console.error('Failed to poll download progress:', error);
      if (isPolling) {
        setTimeout(poll, interval * 2);
      }
    }
  };

  poll();

  return () => {
    isPolling = false;
  };
};

// ========== Task Manager Types ==========

export interface TaskManagerItem {
  platform_id: string;
  title: string;
  status: 'pending' | 'downloading' | 'completed' | 'failed';
  percent: number;
  downloaded: number;
  total: number;
  speed: string;
  retry_count: number;
  max_retries: number;
  error: string | null;
  started_at: string;
  updated_at: string;
}

export interface TaskManagerStats {
  pending: number;
  downloading: number;
  completed: number;
  failed: number;
  worker_online: boolean;
  storage_free: string;
  storage_used_percent: number;
}

export interface TaskManagerListResponse {
  items: TaskManagerItem[];
  total: number;
}

// ========== Task Manager API ==========

/**
 * Get task list from TaskManager
 */
export const getTaskManagerTasks = async (
  status?: string,
  limit: number = 50,
  offset: number = 0
): Promise<TaskManagerListResponse> => {
  const params = new URLSearchParams();
  if (status) params.append('status', status);
  params.append('limit', String(limit));
  params.append('offset', String(offset));

  const response = await fetch(`${API_BASE}/api/v1/download-tasks?${params}`, {
    method: 'GET',
    headers: getAuthHeaders(),
  });

  if (!response.ok) {
    throw new Error(`Failed to fetch tasks: ${response.statusText}`);
  }

  return response.json();
};

/**
 * Get task statistics from TaskManager
 */
export const getTaskManagerStats = async (): Promise<TaskManagerStats> => {
  const response = await fetch(`${API_BASE}/api/v1/download-tasks/stats`, {
    method: 'GET',
    headers: getAuthHeaders(),
  });

  if (!response.ok) {
    throw new Error(`Failed to fetch task stats: ${response.statusText}`);
  }

  return response.json();
};

/**
 * Retry a failed task
 */
export const retryFailedTask = async (platformId: string, force: boolean = false): Promise<{
  success: boolean;
  message: string;
  platform_id?: string;
}> => {
  const response = await fetch(`${API_BASE}/api/v1/download-tasks/${platformId}/retry?force=${force}`, {
    method: 'POST',
    headers: getAuthHeaders(),
  });

  if (!response.ok) {
    throw new Error(`Failed to retry task: ${response.statusText}`);
  }

  return response.json();
};

/**
 * Retry all failed tasks
 */
export const retryAllFailedTasks = async (force: boolean = false): Promise<{
  success: boolean;
  message: string;
  retried_count: number;
  failed_ids: string[];
}> => {
  const response = await fetch(`${API_BASE}/api/v1/download-tasks/retry-all?force=${force}`, {
    method: 'POST',
    headers: getAuthHeaders(),
  });

  if (!response.ok) {
    throw new Error(`Failed to retry all tasks: ${response.statusText}`);
  }

  return response.json();
};

/**
 * Delete a task from TaskManager
 */
export const deleteTaskManagerTask = async (platformId: string): Promise<{
  success: boolean;
  message: string;
}> => {
  const response = await fetch(`${API_BASE}/api/v1/download-tasks/${platformId}`, {
    method: 'DELETE',
    headers: getAuthHeaders(),
  });

  if (!response.ok) {
    throw new Error(`Failed to delete task: ${response.statusText}`);
  }

  return response.json();
};

/**
 * Cleanup completed tasks
 */
export const cleanupCompletedTasks = async (keepRecent: number = 100): Promise<{
  success: boolean;
  removed_count: number;
}> => {
  const response = await fetch(`${API_BASE}/api/v1/download-tasks/cleanup?keep_recent=${keepRecent}`, {
    method: 'POST',
    headers: getAuthHeaders(),
  });

  if (!response.ok) {
    throw new Error(`Failed to cleanup tasks: ${response.statusText}`);
  }

  return response.json();
};

/**
 * Format bytes to human readable string
 */
export const formatBytes = (bytes: number): string => {
  if (bytes === 0) return '0 B';

  const k = 1024;
  const sizes = ['B', 'KB', 'MB', 'GB', 'TB'];
  const i = Math.floor(Math.log(bytes) / Math.log(k));

  return parseFloat((bytes / Math.pow(k, i)).toFixed(1)) + ' ' + sizes[i];
};

/**
 * Format relative time
 */
export const formatRelativeTime = (dateString: string): string => {
  const date = new Date(dateString);
  const now = new Date();
  const diffMs = now.getTime() - date.getTime();
  const diffMins = Math.floor(diffMs / 60000);

  if (diffMins < 1) return 'Just now';
  if (diffMins < 60) return `${diffMins}m ago`;

  const diffHours = Math.floor(diffMins / 60);
  if (diffHours < 24) return `${diffHours}h ago`;

  const diffDays = Math.floor(diffHours / 24);
  return `${diffDays}d ago`;
};
