// frontend/services/pointsService.ts

/**
 * Points system API service
 *
 * Provides functions for querying points balance, transactions,
 * pricing, usage stats, and quota checks.
 */

import { getAuthHeaders } from './parserService';
import { TeamQuota, PointTransaction, PointPricing, QuotaCheck } from '../types';

const API_BASE = 'VITE_API_URL' in import.meta.env ? (import.meta.env.VITE_API_URL || '') : 'http://localhost:8080';

/**
 * Fetch the points balance and quota for a team.
 */
export const fetchPointsBalance = async (teamId?: string): Promise<TeamQuota> => {
  const params = new URLSearchParams();
  if (teamId) params.set('team_id', teamId);

  const query = params.toString();
  const url = `${API_BASE}/api/v1/points/balance${query ? `?${query}` : ''}`;

  const response = await fetch(url, {
    method: 'GET',
    headers: getAuthHeaders(),
  });

  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: 'Failed to fetch points balance' }));
    throw new Error(error.detail || `HTTP ${response.status}`);
  }

  const result = await response.json();
  if (!result.success) {
    throw new Error(result.message || 'Failed to fetch points balance');
  }
  return result.data;
};

/**
 * Fetch points transaction history.
 */
export const fetchPointsTransactions = async (
  teamId?: string,
  limit: number = 20,
  offset: number = 0,
  type?: string
): Promise<PointTransaction[]> => {
  const params = new URLSearchParams();
  if (teamId) params.set('team_id', teamId);
  params.set('limit', String(limit));
  params.set('offset', String(offset));
  if (type) params.set('type', type);

  const url = `${API_BASE}/api/v1/points/transactions?${params.toString()}`;

  const response = await fetch(url, {
    method: 'GET',
    headers: getAuthHeaders(),
  });

  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: 'Failed to fetch transactions' }));
    throw new Error(error.detail || `HTTP ${response.status}`);
  }

  const result = await response.json();
  if (!result.success) {
    throw new Error(result.message || 'Failed to fetch transactions');
  }
  return result.transactions;
};

/**
 * Fetch the pricing table for all action types.
 */
export const fetchPointsPricing = async (): Promise<PointPricing[]> => {
  const url = `${API_BASE}/api/v1/points/pricing`;

  const response = await fetch(url, {
    method: 'GET',
    headers: getAuthHeaders(),
  });

  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: 'Failed to fetch pricing' }));
    throw new Error(error.detail || `HTTP ${response.status}`);
  }

  const result = await response.json();
  if (!result.success) {
    throw new Error(result.message || 'Failed to fetch pricing');
  }
  return result.pricing;
};

/**
 * Fetch usage statistics (consumption breakdown, trends, etc.).
 */
export const fetchUsageStats = async (teamId?: string): Promise<any> => {
  const params = new URLSearchParams();
  if (teamId) params.set('team_id', teamId);

  const query = params.toString();
  const url = `${API_BASE}/api/v1/points/usage-stats${query ? `?${query}` : ''}`;

  const response = await fetch(url, {
    method: 'GET',
    headers: getAuthHeaders(),
  });

  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: 'Failed to fetch usage stats' }));
    throw new Error(error.detail || `HTTP ${response.status}`);
  }

  const result = await response.json();
  if (!result.success) {
    throw new Error(result.message || 'Failed to fetch usage stats');
  }
  return result.data;
};

/**
 * Check whether a team has enough quota for a given action.
 */
export const checkQuota = async (
  actionType: string,
  count: number = 1,
  teamId?: string
): Promise<QuotaCheck> => {
  const params = new URLSearchParams();
  params.set('action_type', actionType);
  params.set('count', String(count));
  if (teamId) params.set('team_id', teamId);

  const url = `${API_BASE}/api/v1/points/check?${params.toString()}`;

  const response = await fetch(url, {
    method: 'GET',
    headers: getAuthHeaders(),
  });

  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: 'Failed to check quota' }));
    throw new Error(error.detail || `HTTP ${response.status}`);
  }

  const result = await response.json();
  if (!result.success) {
    throw new Error(result.message || 'Failed to check quota');
  }
  return result.data;
};
