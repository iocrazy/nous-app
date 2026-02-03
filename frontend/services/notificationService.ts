/**
 * Notification Service - Backend API proxy for notification operations
 *
 * All notification operations go through the backend API instead of direct Supabase calls.
 */

import { getAuthHeaders } from './parserService';
import { Notification } from '../types';

const getApiUrl = (): string => {
  // @ts-ignore
  if (typeof import.meta !== 'undefined' && 'VITE_API_URL' in import.meta.env) {
    // @ts-ignore
    return import.meta.env.VITE_API_URL || '';
  }
  return 'http://localhost:8080';
};

export interface NotificationWithRead extends Notification {
  read: boolean;
}

/**
 * Fetch all notifications for the current user
 */
export const fetchNotifications = async (): Promise<NotificationWithRead[]> => {
  const apiUrl = getApiUrl();

  const response = await fetch(`${apiUrl}/api/v1/notifications`, {
    method: 'GET',
    headers: getAuthHeaders(),
  });

  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: 'Request failed' }));
    throw new Error(error.detail || `HTTP ${response.status}`);
  }

  const data = await response.json();
  return data.notifications || [];
};

/**
 * Mark a notification as read
 */
export const markAsRead = async (notificationId: string): Promise<void> => {
  const apiUrl = getApiUrl();

  const response = await fetch(`${apiUrl}/api/v1/notifications/${notificationId}/read`, {
    method: 'PUT',
    headers: getAuthHeaders(),
  });

  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: 'Request failed' }));
    throw new Error(error.detail || `HTTP ${response.status}`);
  }
};

/**
 * Mark all notifications as read
 */
export const markAllAsRead = async (): Promise<void> => {
  const apiUrl = getApiUrl();

  const response = await fetch(`${apiUrl}/api/v1/notifications/read-all`, {
    method: 'PUT',
    headers: getAuthHeaders(),
  });

  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: 'Request failed' }));
    throw new Error(error.detail || `HTTP ${response.status}`);
  }
};

/**
 * Get count of unread notifications
 */
export const getUnreadCount = async (): Promise<number> => {
  const apiUrl = getApiUrl();

  const response = await fetch(`${apiUrl}/api/v1/notifications/unread-count`, {
    method: 'GET',
    headers: getAuthHeaders(),
  });

  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: 'Request failed' }));
    throw new Error(error.detail || `HTTP ${response.status}`);
  }

  const data = await response.json();
  return data.unread_count || 0;
};
