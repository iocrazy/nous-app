/**
 * The Points Adjustment panel calls `POST /points/admin/adjust`, which only a
 * platform admin (`user_profiles.role === 'admin'`) may call. It used to be
 * gated on a team permission the owner wildcard satisfies, so every user saw
 * it on their personal team and every submit was refused with 403.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT =
  true;

const auth = vi.hoisted(() => ({ role: null as string | null }));

vi.mock('../contexts/AuthContext', () => ({
  useOptionalAuth: () => (auth.role === null ? null : { userProfile: { role: auth.role } }),
}));
vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string, fallback?: string) => fallback ?? key }),
}));
vi.mock('../services/pointsService', () => ({
  fetchPointsBalance: vi.fn(async () => ({
    team_id: '7300000000000000009',
    points_balance: 10,
    storage_used_bytes: 0,
    storage_limit_bytes: 1024,
    storage_used_percent: 0,
  })),
  fetchPointsPricing: vi.fn(async () => []),
  fetchUsageStats: vi.fn(async () => ({ total_consumed: 0, total_purchased: 0, by_type: {} })),
  adjustPoints: vi.fn(),
}));
vi.mock('../services/paymentService', () => ({
  fetchPackages: vi.fn(async () => []),
  fetchOrders: vi.fn(async () => []),
}));

import { BillingView } from './BillingView';

async function renderText(): Promise<string> {
  const container = document.createElement('div');
  document.body.appendChild(container);
  const root = createRoot(container);
  await act(async () => {
    root.render(<BillingView teamId="7300000000000000009" onBuyPackage={() => {}} />);
  });
  await act(async () => {
    await new Promise((r) => setTimeout(r, 0));
  });
  const text = container.textContent ?? '';
  act(() => root.unmount());
  container.remove();
  return text;
}

describe('BillingView Points Adjustment panel', () => {
  beforeEach(() => {
    auth.role = null;
  });

  it('is shown to a platform admin', async () => {
    auth.role = 'admin';
    expect(await renderText()).toContain('Points Adjustment');
  });

  it('is hidden from a regular user', async () => {
    auth.role = 'user';
    expect(await renderText()).not.toContain('Points Adjustment');
  });

  it('is hidden when signed-out state has no profile', async () => {
    expect(await renderText()).not.toContain('Points Adjustment');
  });
});
