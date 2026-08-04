/**
 * AgentWorkbenchTab (B2) — the "waiting for your reply" card.
 *
 * It used to render a bare count ("2 issues waiting for your reply") because
 * the needs-input feed carried no agent dimension and no identifier. Both are
 * on the payload now, so the card shows what was actually ASKED and links
 * straight at the issue — a count tells you that you are late, the question
 * tells you whether you can answer it in ten seconds.
 */
import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import type { AILibraryAgent } from '../../types';
import type { NeedsInputItem } from '../../services/issuesService';

const listNeedsInput = vi.fn();
vi.mock('../../services/issuesService', () => ({
  listNeedsInput: (...a: unknown[]) => listNeedsInput(...a),
}));

vi.mock('../../services/aiLibraryService', () => ({
  aiLibraryService: { getAgentStats: vi.fn(async () => ({})) },
}));

// The three children own their own fetching; this test is about the card.
vi.mock('./AgentDashboardTab', () => ({ AgentDashboardTab: () => <div /> }));
vi.mock('./AgentRunsSplit', () => ({ AgentRunsSplit: () => <div /> }));
vi.mock('./AgentRoutinesTab', () => ({ AgentRoutinesTab: () => <div /> }));
vi.mock('./NewRoutineModal', () => ({ NewRoutineModal: () => <div /> }));

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (_k: string, d?: string | Record<string, unknown>, o?: Record<string, unknown>) => {
      const def = typeof d === 'string' ? d : '';
      const vars = (typeof d === 'string' ? o : d) ?? {};
      return def.replace(/\{\{(\w+)\}\}/g, (_m, k) => String(vars[k] ?? ''));
    },
  }),
}));

import { AgentWorkbenchTab } from './AgentWorkbenchTab';

const AGENT_ID = '22222222-2222-4222-8222-222222222222';

const agent = { id: AGENT_ID, slug: 'script-ai', name: 'Script AI' } as AILibraryAgent;

const item = (over: Partial<NeedsInputItem> = {}): NeedsInputItem => ({
  issue_id: '4242',
  identifier: 'MH-7',
  title: 'Second act',
  question: 'Does she stay silent or confront him?',
  project_id: null,
  team_id: '8',
  assignee_agent_id: AGENT_ID,
  asked_at: '2026-08-01T10:00:00Z',
  ...over,
});

function renderTab() {
  return render(
    <MemoryRouter>
      <AgentWorkbenchTab agent={agent} slug="script-ai" urlPrefix="/team/8" />
    </MemoryRouter>,
  );
}

describe('AgentWorkbenchTab — 等你回复卡', () => {
  beforeEach(() => {
    listNeedsInput.mockReset();
  });

  it("shows the agent's own question verbatim, with a deep link to answer it", async () => {
    listNeedsInput.mockResolvedValue({ items: [item()] });
    renderTab();

    await waitFor(() => expect(screen.getByTestId('waiting-replies')).toBeTruthy());
    expect(screen.getByText(/Does she stay silent or confront him\?/)).toBeTruthy();
    const link = screen.getByTestId('waitcard-answer-link') as HTMLAnchorElement;
    expect(link.getAttribute('href')).toBe('/team/8/todolist/MH-7');
  });

  it('ignores questions parked on a different agent', async () => {
    listNeedsInput.mockResolvedValue({
      items: [item({ assignee_agent_id: 'someone-else', question: 'Not yours' })],
    });
    renderTab();

    await waitFor(() => expect(listNeedsInput).toHaveBeenCalled());
    expect(screen.queryByTestId('waiting-replies')).toBeNull();
    expect(screen.queryByText(/Not yours/)).toBeNull();
  });

  it('renders no card at all when nothing is waiting', async () => {
    listNeedsInput.mockResolvedValue({ items: [] });
    renderTab();

    await waitFor(() => expect(listNeedsInput).toHaveBeenCalled());
    expect(screen.queryByTestId('waiting-replies')).toBeNull();
  });

  it('falls back to the issue title when the agent gave no reason', async () => {
    listNeedsInput.mockResolvedValue({ items: [item({ question: null })] });
    renderTab();

    await waitFor(() => expect(screen.getByTestId('waiting-replies')).toBeTruthy());
    expect(screen.getByText(/Second act/)).toBeTruthy();
  });

  it('degrades to no card when the feed fails, instead of blanking the page', async () => {
    listNeedsInput.mockRejectedValue(new Error('boom'));
    const spy = vi.spyOn(console, 'error').mockImplementation(() => {});
    renderTab();

    await waitFor(() => expect(spy).toHaveBeenCalled());
    expect(screen.queryByTestId('waiting-replies')).toBeNull();
    spy.mockRestore();
  });

  it('drops a row with no identifier — there is nowhere to send the user', async () => {
    listNeedsInput.mockResolvedValue({ items: [item({ identifier: null })] });
    renderTab();

    await waitFor(() => expect(listNeedsInput).toHaveBeenCalled());
    expect(screen.queryByTestId('waitcard-answer-link')).toBeNull();
  });
});
