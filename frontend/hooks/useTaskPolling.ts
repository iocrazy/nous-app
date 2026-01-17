// frontend/hooks/useTaskPolling.ts

/**
 * 任务轮询 Hook
 *
 * 提供 React 组件中使用任务轮询的便捷方式
 */

import { useState, useEffect, useCallback } from 'react';
import {
  TaskStatus,
  TaskStatusResponse,
  getTaskStatus,
  pollTaskStatus,
} from '../services/taskService';

interface UseTaskPollingOptions {
  /** 轮询间隔（毫秒） */
  interval?: number;
  /** 是否自动开始轮询 */
  autoStart?: boolean;
  /** 状态变化回调 */
  onStatusChange?: (status: TaskStatusResponse) => void;
  /** 完成回调 */
  onComplete?: (result: TaskStatusResponse) => void;
  /** 失败回调 */
  onError?: (error: Error) => void;
}

interface UseTaskPollingReturn {
  /** 当前状态 */
  status: TaskStatus | null;
  /** 完整的状态响应 */
  response: TaskStatusResponse | null;
  /** 是否正在轮询 */
  isPolling: boolean;
  /** 是否已完成 */
  isCompleted: boolean;
  /** 是否失败 */
  isFailed: boolean;
  /** 错误信息 */
  error: string | null;
  /** 任务结果 */
  result: Record<string, unknown> | null;
  /** 开始轮询 */
  startPolling: () => void;
  /** 停止轮询 */
  stopPolling: () => void;
  /** 刷新状态（单次查询） */
  refresh: () => Promise<void>;
}

/**
 * 任务轮询 Hook
 *
 * @param taskId 任务 ID（可以为 null，在任务提交后设置）
 * @param options 配置选项
 */
export const useTaskPolling = (
  taskId: string | null,
  options: UseTaskPollingOptions = {}
): UseTaskPollingReturn => {
  const {
    interval = 2000,
    autoStart = true,
    onStatusChange,
    onComplete,
    onError,
  } = options;

  const [status, setStatus] = useState<TaskStatus | null>(null);
  const [response, setResponse] = useState<TaskStatusResponse | null>(null);
  const [isPolling, setIsPolling] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [stopFn, setStopFn] = useState<(() => void) | null>(null);

  const isCompleted = status === 'SUCCESS';
  const isFailed = status === 'FAILURE' || status === 'REVOKED';
  const result = response?.result ?? null;

  // 处理状态更新
  const handleStatusUpdate = useCallback(
    (statusResponse: TaskStatusResponse) => {
      setResponse(statusResponse);
      setStatus(statusResponse.status);
      setError(statusResponse.error ?? null);

      onStatusChange?.(statusResponse);

      // 检查是否完成
      if (['SUCCESS', 'FAILURE', 'REVOKED'].includes(statusResponse.status)) {
        setIsPolling(false);

        if (statusResponse.status === 'SUCCESS') {
          onComplete?.(statusResponse);
        } else if (statusResponse.error) {
          onError?.(new Error(statusResponse.error));
        }
      }
    },
    [onStatusChange, onComplete, onError]
  );

  // 开始轮询
  const startPolling = useCallback(() => {
    if (!taskId || isPolling) return;

    setIsPolling(true);
    setError(null);

    const stop = pollTaskStatus(taskId, handleStatusUpdate, interval);
    setStopFn(() => stop);
  }, [taskId, isPolling, interval, handleStatusUpdate]);

  // 停止轮询
  const stopPolling = useCallback(() => {
    if (stopFn) {
      stopFn();
      setStopFn(null);
    }
    setIsPolling(false);
  }, [stopFn]);

  // 单次刷新
  const refresh = useCallback(async () => {
    if (!taskId) return;

    try {
      const statusResponse = await getTaskStatus(taskId);
      handleStatusUpdate(statusResponse);
    } catch (err) {
      const errorMsg = err instanceof Error ? err.message : '获取任务状态失败';
      setError(errorMsg);
      onError?.(err instanceof Error ? err : new Error(errorMsg));
    }
  }, [taskId, handleStatusUpdate, onError]);

  // 自动开始轮询
  useEffect(() => {
    if (taskId && autoStart && !isPolling && !isCompleted && !isFailed) {
      startPolling();
    }

    // 清理
    return () => {
      if (stopFn) {
        stopFn();
      }
    };
  }, [taskId, autoStart]); // eslint-disable-line react-hooks/exhaustive-deps

  // taskId 变化时重置状态
  useEffect(() => {
    if (!taskId) {
      setStatus(null);
      setResponse(null);
      setError(null);
      setIsPolling(false);
    }
  }, [taskId]);

  return {
    status,
    response,
    isPolling,
    isCompleted,
    isFailed,
    error,
    result,
    startPolling,
    stopPolling,
    refresh,
  };
};

/**
 * 多任务轮询 Hook
 */
export const useMultipleTasksPolling = (
  taskIds: string[],
  options: UseTaskPollingOptions = {}
) => {
  const [statuses, setStatuses] = useState<Map<string, TaskStatusResponse>>(new Map());
  const [isPolling, setIsPolling] = useState(false);

  const updateStatus = useCallback((taskId: string, status: TaskStatusResponse) => {
    setStatuses((prev) => new Map(prev).set(taskId, status));
    options.onStatusChange?.(status);
  }, [options]);

  // 计算汇总状态
  const completedCount = Array.from(statuses.values()).filter(
    (s) => s.status === 'SUCCESS'
  ).length;

  const failedCount = Array.from(statuses.values()).filter(
    (s) => s.status === 'FAILURE' || s.status === 'REVOKED'
  ).length;

  const pendingCount = taskIds.length - completedCount - failedCount;

  const isAllCompleted = taskIds.length > 0 && pendingCount === 0;

  return {
    statuses,
    isPolling,
    completedCount,
    failedCount,
    pendingCount,
    isAllCompleted,
    getStatus: (taskId: string) => statuses.get(taskId),
  };
};

export default useTaskPolling;
