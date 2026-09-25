/**
 * The persistent agents are shared by every user, so the backend lets only a
 * platform admin pause / resume / clear / cancel them or open their drawer
 * (the `/workforce/agents/{slug}/…` routes and task cancel → AdminAuthDep).
 * Everyone else gets the read-only board: no action buttons, no drawer.
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
  useTranslation: () => ({
    t: (_key: string, fallback?: string) => (typeof fallback === 'string' ? fallback : _key),
  }),
}));
vi.mock('../supabaseClient', () => ({
  getSupabaseClient: () => {
    const channel = { on: () => channel, subscribe: () => channel };
    return { channel: () => channel, removeChannel: async () => undefined };
  },
}));
vi.mock('../components/AILibrary/LiveRunsStrip', () => ({ LiveRunsStrip: () => null }));
vi.mock('../components/Workforce/AgentDetailDrawer', () => ({
  AgentDetailDrawer: () => <div data-testid="agent-drawer" />,
}));
vi.mock('../services/workforceService', () => ({
  workforceService: {
    getBoard: vi.fn(async () => ({
      agents: [
        {
          id: '00000000-0000-0000-0000-0000000000a1',
          slug: 'coordinator',
          name: 'Coordinator',
          icon: null,
          model: null,
          persistent: true,
          paused_reason: null,
          worker: null,
          queue: { inbox_unread: 2, inbox_reading: 0, outbox_undelivered: 0 },
          recent_runs: [],
        },
      ],
      recent_state_history: [],
    })),
  },
}));

import { WorkforcePage } from './WorkforcePage';

async function render(): Promise<HTMLDivElement> {
  const container = document.createElement('div');
  document.body.appendChild(container);
  const root = createRoot(container);
  await act(async () => {
    root.render(<WorkforcePage />);
  });
  await act(async () => {
    await new Promise((r) => setTimeout(r, 0));
  });
  return container;
}

async function openCard(c: HTMLDivElement): Promise<void> {
  const card = Array.from(c.querySelectorAll('button')).find((b) =>
    b.textContent?.includes('Coordinator'),
  );
  expect(card).toBeDefined();
  await act(async () => {
    card!.click();
  });
}

describe('WorkforcePage admin gate', () => {
  beforeEach(() => {
    auth.role = null;
    document.body.innerHTML = '';
  });

  it('gives a platform admin the actions and the drawer', async () => {
    auth.role = 'admin';
    const c = await render();
    expect(c.querySelector('[aria-label="Pause"]')).not.toBeNull();
    expect(c.querySelector('[aria-label="Clear inbox"]')).not.toBeNull();
    expect(c.querySelector('[aria-label="Cancel current task"]')).not.toBeNull();
    await openCard(c);
    expect(c.querySelector('[data-testid="agent-drawer"]')).not.toBeNull();
  });

  it.each(['user', null])('shows role %s the board without actions or drawer', async (role) => {
    auth.role = role;
    const c = await render();
    expect(c.textContent).toContain('Coordinator');
    expect(c.querySelector('[aria-label="Pause"]')).toBeNull();
    expect(c.querySelector('[aria-label="Clear inbox"]')).toBeNull();
    expect(c.querySelector('[aria-label="Cancel current task"]')).toBeNull();
    await openCard(c);
    expect(c.querySelector('[data-testid="agent-drawer"]')).toBeNull();
  });
});
