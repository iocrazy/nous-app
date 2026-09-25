// frontend/services/pointsService.ts

/**
 * Points system API service
 *
 * Provides functions for querying points balance, transactions,
 * pricing, usage stats, and quota checks.
 */

import { apiClient } from './apiClient';
import type {
  AdminTeamPointsAdjustResult,
  Envelope,
  PointsBalance,
  PointsPricing,
  PointsPricingResponse,
  PointsQuotaCheck,
  PointsTransaction,
  PointsTransactionsResponse,
  PointsUsageStats,
} from '../types/api';

/**
 * Failures never reach these bodies: the backend raises, and `apiClient`
 * throws on the non-2xx `ErrorResponse`. The `success` check is kept for a
 * body that says otherwise anyway.
 */
function assertSuccess(result: { success: boolean }): void {
  if (!result.success) {
    throw new Error((result as { message?: string }).message || 'Request failed');
  }
}

/**
 * Fetch the points balance and quota for a team.
 */
export const fetchPointsBalance = async (teamId?: string): Promise<PointsBalance> => {
  const result = await apiClient.get<Envelope<PointsBalance>>(
    '/api/v1/points/balance',
    { query: { team_id: teamId } },
  );
  assertSuccess(result);
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
): Promise<PointsTransaction[]> => {
  const result = await apiClient.get<PointsTransactionsResponse>(
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
  assertSuccess(result);
  return result.transactions;
};

/**
 * Fetch the pricing table for all action types.
 */
export const fetchPointsPricing = async (): Promise<PointsPricing[]> => {
  const result = await apiClient.get<PointsPricingResponse>(
    '/api/v1/points/pricing',
  );
  assertSuccess(result);
  return result.pricing;
};

/**
 * Fetch the team's all-time consumption totals and per-type breakdown.
 * There is no per-month or per-member breakdown on this endpoint.
 */
export const fetchUsageStats = async (teamId?: string): Promise<PointsUsageStats> => {
  const result = await apiClient.get<Envelope<PointsUsageStats>>(
    '/api/v1/points/usage-stats',
    { query: { team_id: teamId } },
  );
  assertSuccess(result);
  return result.data;
};

/**
 * Check whether a team has enough quota for a given action.
 */
export const checkQuota = async (
  actionType: string,
  count: number = 1,
  teamId?: string,
): Promise<PointsQuotaCheck> => {
  const result = await apiClient.get<Envelope<PointsQuotaCheck>>(
    '/api/v1/points/check',
    {
      query: { action_type: actionType, count, team_id: teamId },
    },
  );
  assertSuccess(result);
  return result.data;
};

/**
 * Admin: adjust a team's points balance.
 */
export const adjustPoints = async (
  teamId: string,
  amount: number,
  description: string,
): Promise<{ new_balance: number; success: boolean }> => {
  const result = await apiClient.post<AdminTeamPointsAdjustResult>('/api/v1/points/admin/adjust', {
    team_id: teamId,
    amount,
    description,
  });

  if (!result.success) {
    throw new Error(result.message || 'Failed to adjust points');
  }
  return { new_balance: result.new_balance, success: true };
};
