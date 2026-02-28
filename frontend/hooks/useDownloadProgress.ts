// frontend/hooks/useDownloadProgress.ts

import { useState, useEffect, useCallback, useRef } from 'react';
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

  // Use refs for callbacks and stop function to avoid stale closures
  const onCompleteRef = useRef(onComplete);
  const onErrorRef = useRef(onError);
  const stopFnRef = useRef<(() => void) | null>(null);
  const isPollingRef = useRef(false);

  // Keep callback refs in sync
  onCompleteRef.current = onComplete;
  onErrorRef.current = onError;

  // Stable callback — uses refs so it never goes stale inside the polling loop
  const handleProgressUpdate = useCallback((progress: DownloadProgressResponse) => {
    setPercent(progress.percent);
    setSpeed(progress.speed);

    switch (progress.status) {
      case 'downloading':
        setStatus('downloading');
        break;
      case 'completed':
        setStatus('completed');
        setIsPolling(false);
        isPollingRef.current = false;
        onCompleteRef.current?.();
        break;
      case 'failed':
        setStatus('failed');
        setIsPolling(false);
        isPollingRef.current = false;
        onErrorRef.current?.(progress.error || 'Download failed');
        break;
      default:
        setStatus('pending');
    }
  }, []);

  const startPolling = useCallback(() => {
    if (!taskId || isPollingRef.current) return;

    setIsPolling(true);
    isPollingRef.current = true;
    setStatus('downloading');

    const stop = pollDownloadProgress(taskId, handleProgressUpdate, interval);
    stopFnRef.current = stop;
  }, [taskId, interval, handleProgressUpdate]);

  const stopPolling = useCallback(() => {
    if (stopFnRef.current) {
      stopFnRef.current();
      stopFnRef.current = null;
    }
    setIsPolling(false);
    isPollingRef.current = false;
  }, []);

  const retry = useCallback(() => {
    setStatus('retrying');
    setPercent(0);
  }, []);

  // Auto-start polling when taskId changes
  useEffect(() => {
    if (taskId && autoStart) {
      startPolling();
    }

    return () => {
      // Cleanup uses ref — always has the latest stop function
      if (stopFnRef.current) {
        stopFnRef.current();
        stopFnRef.current = null;
      }
      isPollingRef.current = false;
    };
  }, [taskId, autoStart, startPolling]);

  // Reset on taskId becoming null
  useEffect(() => {
    if (!taskId) {
      setStatus('pending');
      setPercent(0);
      setSpeed(undefined);
      setIsPolling(false);
      isPollingRef.current = false;
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
