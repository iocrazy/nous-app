// frontend/services/paymentService.ts

/**
 * Payment API service
 *
 * Provides functions for fetching point packages, creating payment orders,
 * polling order status, and listing order history.
 */

import { apiClient } from './apiClient';
import type {
  PaymentOrder,
  PaymentOrderResponse,
  PaymentOrderStatus,
  PaymentOrderStatusResponse,
  PaymentOrdersResponse,
  PaymentPackagesResponse,
  PointPackage,
} from '../types/api';

/** Every `/payment/*` success body is `{ success: true, data }`; failures are
 *  HTTP errors that `apiClient` throws. The `success` check is a guard, and a
 *  non-standard `message` on such a body is surfaced when present. */
function unwrap<T>(result: { success: boolean; data: T }): T {
  if (!result.success) {
    const message = (result as { message?: string }).message;
    throw new Error(message || 'Request failed');
  }
  return result.data;
}

/**
 * Fetch all available point packages for purchase.
 */
export const fetchPackages = async (): Promise<PointPackage[]> => {
  const result = await apiClient.get<PaymentPackagesResponse>(
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
  const result = await apiClient.post<PaymentOrderResponse>(
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
): Promise<PaymentOrderStatus> => {
  const result = await apiClient.get<PaymentOrderStatusResponse>(
    `/api/v1/payment/order/${orderId}/status`,
  );
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
  const result = await apiClient.get<PaymentOrdersResponse>(
    '/api/v1/payment/orders',
    { query: { team_id: teamId, limit, offset } },
  );
  return unwrap(result);
};
