// frontend/services/paymentService.ts

/**
 * Payment API service
 *
 * Provides functions for fetching point packages, creating payment orders,
 * polling order status, and listing order history.
 */

import { apiClient } from './apiClient';
import { PointPackage, PaymentOrder } from '../types';

interface Envelope<T> {
  success: boolean;
  message?: string;
  data: T;
}

function unwrap<T>(result: Envelope<T>): T {
  if (!result.success) {
    throw new Error(result.message || 'Request failed');
  }
  return result.data;
}

/**
 * Fetch all available point packages for purchase.
 */
export const fetchPackages = async (): Promise<PointPackage[]> => {
  const result = await apiClient.get<Envelope<PointPackage[]>>(
    '/api/v1/payment/packages',
  );
  return unwrap(result);
};

/**
 * Create a new payment order for a point package.
 */
export const createOrder = async (
  packageId: string,
  paymentMethod: 'wechat' | 'alipay',
  teamId: string,
): Promise<PaymentOrder> => {
  const result = await apiClient.post<Envelope<PaymentOrder>>(
    '/api/v1/payment/create-order',
    {
      package_id: packageId,
      payment_method: paymentMethod,
      team_id: teamId,
    },
  );
  return unwrap(result);
};

/**
 * Poll the status of a payment order.
 */
export const pollOrderStatus = async (
  orderId: string,
): Promise<{
  payment_status: string;
  points_amount: number;
  paid_at: string | null;
}> => {
  const result = await apiClient.get<
    Envelope<{
      payment_status: string;
      points_amount: number;
      paid_at: string | null;
    }>
  >(`/api/v1/payment/order/${orderId}/status`);
  return unwrap(result);
};

/**
 * Fetch order history for a team.
 */
export const fetchOrders = async (
  teamId?: string,
  limit: number = 20,
  offset: number = 0,
): Promise<PaymentOrder[]> => {
  const result = await apiClient.get<Envelope<PaymentOrder[]>>(
    '/api/v1/payment/orders',
    { query: { team_id: teamId, limit, offset } },
  );
  return unwrap(result);
};
