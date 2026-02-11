// frontend/services/paymentService.ts

/**
 * Payment API service
 *
 * Provides functions for fetching point packages, creating payment orders,
 * polling order status, and listing order history.
 */

import { getAuthHeaders } from './parserService';
import { PointPackage, PaymentOrder } from '../types';

const API_BASE = 'VITE_API_URL' in import.meta.env ? (import.meta.env.VITE_API_URL || '') : 'http://localhost:8080';

/**
 * Fetch all available point packages for purchase.
 */
export const fetchPackages = async (): Promise<PointPackage[]> => {
  const url = `${API_BASE}/api/v1/payment/packages`;

  const response = await fetch(url, {
    method: 'GET',
    headers: getAuthHeaders(),
  });

  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: 'Failed to fetch packages' }));
    throw new Error(error.detail || `HTTP ${response.status}`);
  }

  const result = await response.json();
  if (!result.success) {
    throw new Error(result.message || 'Failed to fetch packages');
  }
  return result.data;
};

/**
 * Create a new payment order for a point package.
 */
export const createOrder = async (
  packageId: string,
  paymentMethod: 'wechat' | 'alipay',
  teamId: string
): Promise<PaymentOrder> => {
  const url = `${API_BASE}/api/v1/payment/orders`;

  const response = await fetch(url, {
    method: 'POST',
    headers: getAuthHeaders(),
    body: JSON.stringify({
      package_id: packageId,
      payment_method: paymentMethod,
      team_id: teamId,
    }),
  });

  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: 'Failed to create order' }));
    throw new Error(error.detail || `HTTP ${response.status}`);
  }

  const result = await response.json();
  if (!result.success) {
    throw new Error(result.message || 'Failed to create order');
  }
  return result.data;
};

/**
 * Poll the status of a payment order.
 * Returns the current status, points amount, and paid timestamp.
 */
export const pollOrderStatus = async (
  orderId: string
): Promise<{ payment_status: string; points_amount: number; paid_at: string | null }> => {
  const url = `${API_BASE}/api/v1/payment/orders/${orderId}/status`;

  const response = await fetch(url, {
    method: 'GET',
    headers: getAuthHeaders(),
  });

  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: 'Failed to poll order status' }));
    throw new Error(error.detail || `HTTP ${response.status}`);
  }

  const result = await response.json();
  if (!result.success) {
    throw new Error(result.message || 'Failed to poll order status');
  }
  return result.data;
};

/**
 * Fetch order history for a team.
 */
export const fetchOrders = async (
  teamId?: string,
  limit: number = 20,
  offset: number = 0
): Promise<PaymentOrder[]> => {
  const params = new URLSearchParams();
  if (teamId) params.set('team_id', teamId);
  params.set('limit', String(limit));
  params.set('offset', String(offset));

  const url = `${API_BASE}/api/v1/payment/orders?${params.toString()}`;

  const response = await fetch(url, {
    method: 'GET',
    headers: getAuthHeaders(),
  });

  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: 'Failed to fetch orders' }));
    throw new Error(error.detail || `HTTP ${response.status}`);
  }

  const result = await response.json();
  if (!result.success) {
    throw new Error(result.message || 'Failed to fetch orders');
  }
  return result.data;
};
