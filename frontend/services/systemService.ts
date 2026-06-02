// frontend/services/systemService.ts

/**
 * System status monitoring service
 */

import { apiClient } from './apiClient';

export interface QueueStatus {
  active: number;
  pending: number;
  scheduled: number;
  // DBOS reports only online | offline. `outdated` / `degraded` are
  // legacy Celery-era values kept for backward-compat with stale rows
  // in system_status; safe to drop after one cache cycle (30s).
  status: 'online' | 'offline' | 'outdated' | 'degraded';
  missing_tasks?: string[];
}

export interface StorageStatus {
  total_bytes: number;
  used_bytes: number;
  free_bytes: number;
  percent_used: number;
  status: 'ok' | 'warning' | 'critical' | 'error' | 'unknown';
  path: string;
}

export interface NetworkStatus {
  speed: string;
  status: 'idle' | 'active' | 'error';
}

export interface SystemStatus {
  queue: QueueStatus;
  storage: StorageStatus;
  network: NetworkStatus;
  timestamp: number;
}

/**
 * Fetch system status (queue, storage, network)
 */
export async function getSystemStatus(): Promise<SystemStatus> {
  return apiClient.get<SystemStatus>('/api/v1/system/status');
}

export interface QueueBreakdownRow {
  task_type: string;
  running: number;
  pending: number;
  oldest_queued_age_sec: number;
}

/** Per-task_type queue depth + oldest queued age (ops/admin visibility). */
export async function getQueueBreakdown(): Promise<QueueBreakdownRow[]> {
  return apiClient.get<QueueBreakdownRow[]>('/api/v1/system/queue-breakdown');
}

/**
 * Format bytes to human readable string
 */
export function formatBytes(bytes: number): string {
  if (bytes === 0) return '0 B';
  const k = 1024;
  const sizes = ['B', 'KB', 'MB', 'GB', 'TB'];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return `${parseFloat((bytes / Math.pow(k, i)).toFixed(1))} ${sizes[i]}`;
}

/**
 * Get storage display text
 */
export function getStorageDisplay(storage: StorageStatus): string {
  if (storage.status === 'error' || storage.status === 'unknown') {
    return 'Error';
  }
  return `${formatBytes(storage.free_bytes)} Free`;
}

/**
 * Get DBOS engine display text
 */
export function getQueueDisplay(queue: QueueStatus): string {
  if (queue.status === 'offline') {
    return 'Offline';
  }
  const total = queue.active + queue.pending;
  if (total === 0) {
    return 'Idle';
  }
  return `${queue.active} Running`;
}
