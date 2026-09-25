/**
 * Starting a dreamina login re-points the SHARED server account, so the
 * backend only lets a platform admin do it (`POST /jimeng-cli/login` →
 * AdminAuthDep). The card shows the Log in button to admins only; everyone
 * else still sees the account status.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT =
  true;

const auth = vi.hoisted(() => ({ role: null as string | null }));
const fetchMock = vi.hoisted(() => ({
  status: { ok: true, status: 200 } as { ok: boolean; status: number },
}));

vi.mock('../../contexts/AuthContext', () => ({
  useOptionalAuth: () => (auth.role === null ? null : { userProfile: { role: auth.role } }),
}));
vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}));
vi.mock('../../services/apiClient', () => ({
  apiFetch: vi.fn(async () => ({
    ok: fetchMock.status.ok,
    status: fetchMock.status.status,
    json: async () => ({ data: { available: true, logged_in: true, total_credit: 3 } }),
  })),
}));

import { JimengCliCard } from './JimengCliCard';

async function render(): Promise<HTMLDivElement> {
  const container = document.createElement('div');
  document.body.appendChild(container);
  const root = createRoot(container);
  await act(async () => {
    root.render(<JimengCliCard />);
  });
  await act(async () => {
    await new Promise((r) => setTimeout(r, 0));
  });
  return container;
}

describe('JimengCliCard login gate', () => {
  beforeEach(() => {
    auth.role = null;
    fetchMock.status = { ok: true, status: 200 };
    document.body.innerHTML = '';
  });

  it('shows Log in to a platform admin', async () => {
    auth.role = 'admin';
    const c = await render();
    expect(c.querySelector('[data-testid="jimeng-login"]')).not.toBeNull();
  });

  it.each(['user', null])('hides Log in from role %s but keeps the status', async (role) => {
    auth.role = role;
    const c = await render();
    expect(c.querySelector('[data-testid="jimeng-login"]')).toBeNull();
    expect(c.textContent).toContain('settings.localCli.jimengLoggedIn');
  });

  it('surfaces a refused status call instead of rendering nothing', async () => {
    fetchMock.status = { ok: false, status: 500 };
    const c = await render();
    expect(c.textContent).toContain('settings.localCli.errStatus');
  });
});
