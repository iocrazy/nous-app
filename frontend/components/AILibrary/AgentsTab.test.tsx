/**
 * AgentsTab — the agent detail surface.
 *
 * Acceptance finding: this page had no AILibraryTabs strip at all, so once
 * you opened an agent there was no visible way back to the gallery ("怎么返回
 * agent 页面"). The skill editor already had one; this makes the two
 * isomorphic, with the active tab clickable (placement="detail").
 */
import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { MemoryRouter, Routes, Route } from 'react-router-dom';

const navigate = vi.fn();
vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual<typeof import('react-router-dom')>('react-router-dom');
  return { ...actual, useNavigate: () => navigate };
});

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (_k: string, d?: string | Record<string, unknown>, o?: Record<string, unknown>) => {
      const def = typeof d === 'string' ? d : '';
      const vars = (typeof d === 'string' ? o : d) ?? {};
      return def.replace(/\{\{(\w+)\}\}/g, (_m, k) => String(vars[k] ?? ''));
    },
  }),
}));

const listAgents = vi.fn();
vi.mock('../../services/aiLibraryService', () => ({
  aiLibraryService: { listAgents: () => listAgents() },
}));

vi.mock('./AgentEditor', () => ({
  AgentEditor: ({ slug }: { slug: string }) => <div data-testid="agent-editor">{slug}</div>,
}));

import { AgentsTab } from './AgentsTab';

function renderTab(slug: string | null) {
  return render(
    <MemoryRouter initialEntries={['/team/7/ai-library/agents/script_ai']}>
      <Routes>
        <Route path="/team/:teamId/*" element={<AgentsTab slug={slug} />} />
      </Routes>
    </MemoryRouter>,
  );
}

describe('AgentsTab', () => {
  beforeEach(() => {
    navigate.mockClear();
    listAgents.mockResolvedValue([{ slug: 'script_ai' }, { slug: 'storyboard' }]);
  });

  it('shows the library tab strip above an open agent', async () => {
    renderTab('script_ai');
    await waitFor(() => expect(screen.getByTestId('agent-editor')).toBeTruthy());
    expect(screen.getByRole('button', { name: /Agents/ })).toBeTruthy();
    expect(screen.getByRole('button', { name: /Skills/ })).toBeTruthy();
  });

  it('routes back to the agent gallery from the highlighted Agents tab', async () => {
    renderTab('script_ai');
    await waitFor(() => expect(screen.getByTestId('agent-editor')).toBeTruthy());
    fireEvent.click(screen.getByRole('button', { name: /Agents/ }));
    expect(navigate).toHaveBeenCalledWith('/team/7/ai-library');
  });

  it('carries the agent count it already fetched', async () => {
    renderTab('script_ai');
    await waitFor(() =>
      expect(screen.getByRole('button', { name: /Agents\s*2/ })).toBeTruthy(),
    );
  });

  it('leaves the empty state alone', async () => {
    // No slug means the sidebar prompt, not an editor — the strip belongs to
    // the detail view only (the gallery renders its own).
    renderTab(null);
    await waitFor(() => expect(screen.getByText(/Pick an agent/)).toBeTruthy());
    expect(screen.queryByRole('button', { name: /Skills/ })).toBeNull();
  });

  it('opens no scroll container of its own — AILibraryLayout owns the page scroll', async () => {
    // This wrapper used to be `h-full overflow-y-auto`, nested inside
    // AILibraryLayout's own `flex-1 overflow-y-auto px-8`. `h-full` pinned it
    // to exactly the outer height, so the INNER box was the one that scrolled
    // and the outer never did. Two user-visible consequences, both reported
    // as separate bugs:
    //
    //   - the scrollbar rendered at the inner box's right edge, i.e. flush
    //     against the Persona tab's attributes card rather than at the page
    //     margin ("紧贴右缘的内部滚动条");
    //   - it appeared and disappeared per tab (measured: Workbench 786px of
    //     content vs 786px of box → none; Persona 887, Permissions 1233,
    //     Cost/Profile taller → one), so switching tabs jogged the whole
    //     column sideways by the scrollbar's width. That flicker is the
    //     "残影" between the Permissions and Profile tabs — it was never a
    //     stray node in the tab strip, which is a plain 5-button map.
    //
    // Asserted structurally because the symptom needs real layout to see.
    const { container } = renderTab('script_ai');
    await waitFor(() => expect(screen.getByTestId('agent-editor')).toBeTruthy());

    const scrollers = container.querySelectorAll(
      '[class*="overflow-y-auto"], [class*="overflow-auto"], [class*="overflow-y-scroll"]',
    );
    expect(scrollers).toHaveLength(0);
    expect(container.querySelector('[class*="h-full"]')).toBeNull();
  });
});
