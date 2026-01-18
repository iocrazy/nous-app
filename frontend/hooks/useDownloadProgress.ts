// frontend/hooks/useDownloadProgress.ts

import { useState, useEffect, useCallback } from 'react';
import {
  DownloadProgressResponse,
  pollDownloadProgress
} from '../services/taskService';
import { DownloadStatus } from '../components/DownloadProgress/types';

interface UseDownloadProgressOptions {
  interval?: number;
  autoStart?: boolean;
  onComplete?: () => void;
  onError?: (error: string) => void;
}

interface UseDownloadProgressReturn {
  status: DownloadStatus;
  percent: number;
  speed: string | undefined;
  isPolling: boolean;
  startPolling: () => void;
  stopPolling: () => void;
  retry: () => void;
}

export const useDownloadProgress = (
  taskId: string | null,
  options: UseDownloadProgressOptions = {}
): UseDownloadProgressReturn => {
  const {
    interval = 1000,
    autoStart = true,
    onComplete,
    onError,
  } = options;

  const [status, setStatus] = useState<DownloadStatus>('pending');
  const [percent, setPercent] = useState(0);
  const [speed, setSpeed] = useState<string | undefined>();
  const [isPolling, setIsPolling] = useState(false);
  const [stopFn, setStopFn] = useState<(() => void) | null>(null);

  const handleProgressUpdate = useCallback((progress: DownloadProgressResponse) => {
    setPercent(progress.percent);
    setSpeed(progress.speed);

    // Map backend status to frontend status
    switch (progress.status) {
      case 'downloading':
        setStatus('downloading');
        break;
      case 'completed':
        setStatus('completed');
        setIsPolling(false);
        onComplete?.();
        break;
      case 'failed':
        setStatus('failed');
        setIsPolling(false);
        onError?.(progress.error || 'Download failed');
        break;
      default:
        setStatus('pending');
    }
  }, [onComplete, onError]);

  const startPolling = useCallback(() => {
    if (!taskId || isPolling) return;

    setIsPolling(true);
    setStatus('downloading');

    const stop = pollDownloadProgress(taskId, handleProgressUpdate, interval);
    setStopFn(() => stop);
  }, [taskId, isPolling, interval, handleProgressUpdate]);

  const stopPolling = useCallback(() => {
    if (stopFn) {
      stopFn();
      setStopFn(null);
    }
    setIsPolling(false);
  }, [stopFn]);

  const retry = useCallback(() => {
    setStatus('retrying');
    setPercent(0);
    // The actual retry logic would trigger a new download task
    // This is handled by the parent component
  }, []);

  // Auto-start polling
  useEffect(() => {
    if (taskId && autoStart && !isPolling && status !== 'completed' && status !== 'failed') {
      startPolling();
    }

    return () => {
      if (stopFn) {
        stopFn();
      }
    };
  }, [taskId, autoStart]); // eslint-disable-line

  // Reset on taskId change
  useEffect(() => {
    if (!taskId) {
      setStatus('pending');
      setPercent(0);
      setSpeed(undefined);
      setIsPolling(false);
    }
  }, [taskId]);

  return {
    status,
    percent,
    speed,
    isPolling,
    startPolling,
    stopPolling,
    retry,
  };
};

export default useDownloadProgress;
