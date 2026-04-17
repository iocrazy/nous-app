// frontend/services/pointsService.ts

/**
 * Points system API service
 *
 * Provides functions for querying points balance, transactions,
 * pricing, usage stats, and quota checks.
 */

import { getAuthHeaders } from './parserService';
import { getApiUrl } from '../utils/apiConfig';
import { TeamQuota, PointTransaction, PointPricing, QuotaCheck } from '../types';

/**
 * Fetch the points balance and quota for a team.
 */
export const fetchPointsBalance = async (teamId?: string): Promise<TeamQuota> => {
  const params = new URLSearchParams();
  if (teamId) params.set('team_id', teamId);

  const query = params.toString();
  const url = `${getApiUrl()}/api/v1/points/balance${query ? `?${query}` : ''}`;

  const response = await fetch(url, {
    method: 'GET',
    headers: await getAuthHeaders(),
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
 * Fetch points transaction history with optional filters.
 */
export const fetchPointsTransactions = async (
  teamId?: string,
  limit: number = 20,
  offset: number = 0,
  type?: string,
  referenceType?: string,
  search?: string,
  days?: number,
): Promise<PointTransaction[]> => {
  const params = new URLSearchParams();
  if (teamId) params.set('team_id', teamId);
  params.set('limit', String(limit));
  params.set('offset', String(offset));
  if (type) params.set('type', type);
  if (referenceType) params.set('reference_type', referenceType);
  if (search) params.set('search', search);
  if (days !== undefined) params.set('days', String(days));

  const url = `${getApiUrl()}/api/v1/points/transactions?${params.toString()}`;

  const response = await fetch(url, {
    method: 'GET',
    headers: await getAuthHeaders(),
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
  const url = `${getApiUrl()}/api/v1/points/pricing`;

  const response = await fetch(url, {
    method: 'GET',
    headers: await getAuthHeaders(),
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
  const url = `${getApiUrl()}/api/v1/points/usage-stats${query ? `?${query}` : ''}`;

  const response = await fetch(url, {
    method: 'GET',
    headers: await getAuthHeaders(),
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

  const url = `${getApiUrl()}/api/v1/points/check?${params.toString()}`;

  const response = await fetch(url, {
    method: 'GET',
    headers: await getAuthHeaders(),
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

/**
 * Admin: adjust a team's points balance.
 */
export const adjustPoints = async (
  teamId: string,
  amount: number,
  description: string
): Promise<{ new_balance: number }> => {
  const url = `${getApiUrl()}/api/v1/points/admin/adjust`;
  const response = await fetch(url, {
    method: 'POST',
    headers: {
      ...(await getAuthHeaders()),
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({ team_id: teamId, amount, description }),
  });

  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: 'Failed to adjust points' }));
    throw new Error(error.detail || `HTTP ${response.status}`);
  }

  const result = await response.json();
  if (!result.success) {
    throw new Error(result.message || 'Failed to adjust points');
  }
  return result;
};
