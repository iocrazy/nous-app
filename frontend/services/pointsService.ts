// frontend/services/pointsService.ts

/**
 * Points system API service
 *
 * Provides functions for querying points balance, transactions,
 * pricing, usage stats, and quota checks.
 */

import { apiClient } from './apiClient';
import { TeamQuota, PointTransaction, PointPricing, QuotaCheck } from '../types';

interface Envelope<T> {
  success: boolean;
  message?: string;
  data?: T;
  // Some endpoints use specific field names instead of data:
  transactions?: T;
  pricing?: T;
}

function unwrap<T>(result: Envelope<T>, field: keyof Envelope<T> = 'data'): T {
  if (!result.success) {
    throw new Error(result.message || 'Request failed');
  }
  const value = result[field];
  if (value === undefined) {
    throw new Error(`Missing field "${String(field)}" in response`);
  }
  return value as T;
}

/**
 * Fetch the points balance and quota for a team.
 */
export const fetchPointsBalance = async (teamId?: string): Promise<TeamQuota> => {
  const result = await apiClient.get<Envelope<TeamQuota>>(
    '/api/v1/points/balance',
    { query: { team_id: teamId } },
  );
  return unwrap(result);
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
  const result = await apiClient.get<Envelope<PointTransaction[]>>(
    '/api/v1/points/transactions',
    {
      query: {
        team_id: teamId,
        limit,
        offset,
        type,
        reference_type: referenceType,
        search,
        days,
      },
    },
  );
  return unwrap(result, 'transactions');
};

/**
 * Fetch the pricing table for all action types.
 */
export const fetchPointsPricing = async (): Promise<PointPricing[]> => {
  const result = await apiClient.get<Envelope<PointPricing[]>>(
    '/api/v1/points/pricing',
  );
  return unwrap(result, 'pricing');
};

/**
 * Fetch usage statistics (consumption breakdown, trends, etc.).
 */
export const fetchUsageStats = async (teamId?: string): Promise<unknown> => {
  const result = await apiClient.get<Envelope<unknown>>(
    '/api/v1/points/usage-stats',
    { query: { team_id: teamId } },
  );
  return unwrap(result);
};

/**
 * Check whether a team has enough quota for a given action.
 */
export const checkQuota = async (
  actionType: string,
  count: number = 1,
  teamId?: string,
): Promise<QuotaCheck> => {
  const result = await apiClient.get<Envelope<QuotaCheck>>(
    '/api/v1/points/check',
    {
      query: { action_type: actionType, count, team_id: teamId },
    },
  );
  return unwrap(result);
};

/**
 * Admin: adjust a team's points balance.
 */
export const adjustPoints = async (
  teamId: string,
  amount: number,
  description: string,
): Promise<{ new_balance: number; success: boolean }> => {
  const result = await apiClient.post<{
    success: boolean;
    message?: string;
    new_balance: number;
  }>('/api/v1/points/admin/adjust', {
    team_id: teamId,
    amount,
    description,
  });

  if (!result.success) {
    throw new Error(result.message || 'Failed to adjust points');
  }
  return { new_balance: result.new_balance, success: true };
};
