// frontend/services/systemService.ts

/**
 * System status monitoring service
 */

import { getAuthHeaders } from './parserService';

const getApiUrl = (): string => {
  if (typeof import.meta !== 'undefined' && 'VITE_API_URL' in import.meta.env) {
    return (import.meta.env.VITE_API_URL || '').trim();
  }
  return 'http://localhost:8080';
};

export interface QueueStatus {
  active: number;
  pending: number;
  scheduled: number;
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
  const apiUrl = getApiUrl();
  const response = await fetch(`${apiUrl}/api/v1/system/status`, {
    method: 'GET',
    headers: await getAuthHeaders(),
  });

  if (!response.ok) {
    throw new Error(`Failed to fetch system status: ${response.status}`);
  }

  return response.json();
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
 * Get queue display text
 */
export function getQueueDisplay(queue: QueueStatus): string {
  if (queue.status === 'offline') {
    return 'Offline';
  }
  if (queue.status === 'outdated') {
    return 'Outdated';
  }
  const total = queue.active + queue.pending;
  if (total === 0) {
    return 'Idle';
  }
  return `${queue.active} Active`;
}
