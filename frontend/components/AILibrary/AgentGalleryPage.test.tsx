/**
 * AgentGalleryPage (B1) — the roster view.
 *   1. Agents render under their own department section.
 *   2. A faulted agent shows the actionable reason line, not just a badge.
 *   3. The primary button follows the status.
 *   4. Filters narrow the roster.
 *   5. A failed stats request degrades loudly, not into fake zeros.
 */
import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import type { AILibraryAgent } from '../../types';
import type { AgentStatsItem } from '../../services/aiLibraryService';

const listAgents = vi.fn();
const getAgentStats = vi.fn();
const listSkills = vi.fn();

vi.mock('../../services/aiLibraryService', () => ({
  aiLibraryService: {
    listAgents: (...a: unknown[]) => listAgents(...a),
    getAgentStats: (...a: unknown[]) => getAgentStats(...a),
    listSkills: (...a: unknown[]) => listSkills(...a),
  },
}));

const runningAgentIds = { current: new Set<string>() };
vi.mock('../../hooks/useAgentRuns', () => ({
  useAgentRuns: () => ({ runningAgentIds: runningAgentIds.current, loaded: true }),
}));

vi.mock('../../contexts/AuthContext', () => ({
  useAuth: () => ({ currentUserId: 'u1' }),
}));

const requestChat = vi.fn();
vi.mock('../../stores/globalChatStore', () => ({
  useGlobalChatStore: (sel: (s: unknown) => unknown) => sel({ requestChat }),
}));

const navigateSpy = vi.fn();
vi.mock('react-router-dom', async (importOriginal) => {
  const actual = await importOriginal<typeof import('react-router-dom')>();
  return { ...actual, useNavigate: () => navigateSpy };
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

import { AgentGalleryPage } from './AgentGalleryPage';

const agent = (over: Partial<AILibraryAgent>): AILibraryAgent =>
  ({
    id: over.slug!,
    name: over.slug!,
    model: 'qwen-max',
    temperature: 0.7,
    max_tokens: 4096,
    is_system_preset: true,
    enabled: true,
    skill_ids: [],
    created_at: '',
    updated_at: '',
    ...over,
  }) as AILibraryAgent;

const stat = (over: Partial<AgentStatsItem> = {}): AgentStatsItem => ({
  runs_7d: 0,
  tokens_7d: 0,
  cost_cents_7d: 0,
  running_count: 0,
  needs_input_count: 0,
  fault: null,
  interrupted_reason: null,
  ...over,
});

const AGENTS = [
  agent({ slug: 'script_ai', agent_group: 'writing' }),
  agent({ slug: 'portrait', agent_group: 'art' }),
  agent({ slug: 'translate', agent_group: 'tools' }),
];

function renderGallery() {
  return render(
    <MemoryRouter>
      <AgentGalleryPage />
    </MemoryRouter>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  runningAgentIds.current = new Set();
  listAgents.mockResolvedValue(AGENTS);
  getAgentStats.mockResolvedValue({});
  listSkills.mockResolvedValue([]);
});

describe('AgentGalleryPage', () => {
  it('renders one section per department', async () => {
    renderGallery();
    await waitFor(() => expect(screen.getByTestId('group-section-writing')).toBeTruthy());
    expect(screen.getByTestId('group-section-art')).toBeTruthy();
    expect(screen.getByTestId('group-section-tools')).toBeTruthy();
    expect(screen.getAllByTestId('agent-card')).toHaveLength(3);
  });

  it('buckets an ungrouped agent into tools rather than dropping it', async () => {
    listAgents.mockResolvedValue([agent({ slug: 'legacy', agent_group: null })]);
    renderGallery();
    await waitFor(() => expect(screen.getByTestId('group-section-tools')).toBeTruthy());
    expect(screen.getAllByTestId('agent-card')).toHaveLength(1);
  });

  it('shows the actionable reason line on a faulted agent', async () => {
    getAgentStats.mockResolvedValue({
      script_ai: stat({
        fault: { kind: 'budget', detail: 'Monthly budget exceeded — raise it' },
      }),
    });
    renderGallery();
    await waitFor(() =>
      expect(screen.getByTestId('fault-warnline').textContent).toContain(
        'Monthly budget exceeded — raise it',
      ),
    );
  });

  it('drives the primary button from the status', async () => {
    getAgentStats.mockResolvedValue({
      script_ai: stat({ running_count: 1 }),
      portrait: stat({ needs_input_count: 2 }),
      translate: stat(),
    });
    renderGallery();
    await waitFor(() => expect(screen.getAllByTestId('agent-card')).toHaveLength(3));

    const actionFor = (slug: string) =>
      screen
        .getByText(slug)
        .closest('[data-testid="agent-card"]')!
        .querySelector('[data-testid="primary-action"]')!
        .getAttribute('data-action');

    expect(actionFor('script_ai')).toBe('viewRuns');
    expect(actionFor('portrait')).toBe('goReply');
    expect(actionFor('translate')).toBe('chat');
  });

  it('opens a chat instead of navigating for an idle agent', async () => {
    renderGallery();
    await waitFor(() => expect(screen.getAllByTestId('agent-card')).toHaveLength(3));
    const card = screen.getByText('translate').closest('[data-testid="agent-card"]')!;
    fireEvent.click(card.querySelector('[data-testid="primary-action"]')!);
    expect(requestChat).toHaveBeenCalledWith('translate');
  });

  it('announces the running agent in a banner', async () => {
    runningAgentIds.current = new Set(['script_ai']);
    renderGallery();
    await waitFor(() =>
      expect(screen.getByTestId('live-banner').textContent).toContain('script_ai'),
    );
  });

  it('filters by department', async () => {
    renderGallery();
    await waitFor(() => expect(screen.getAllByTestId('agent-card')).toHaveLength(3));
    fireEvent.click(screen.getByTestId('group-chip-art'));
    expect(screen.getAllByTestId('agent-card')).toHaveLength(1);
    expect(screen.queryByTestId('group-section-writing')).toBeNull();
  });

  it('filters by search text', async () => {
    renderGallery();
    await waitFor(() => expect(screen.getAllByTestId('agent-card')).toHaveLength(3));
    fireEvent.change(screen.getByLabelText('Search agents...'), {
      target: { value: 'trans' },
    });
    expect(screen.getAllByTestId('agent-card')).toHaveLength(1);
  });

  it('narrows to faulted agents only', async () => {
    getAgentStats.mockResolvedValue({
      portrait: stat({ fault: { kind: 'dead_runs', detail: 'Provider unreachable' } }),
    });
    renderGallery();
    await waitFor(() => expect(screen.getAllByTestId('agent-card')).toHaveLength(3));
    fireEvent.click(screen.getByText(/Faults only/));
    expect(screen.getAllByTestId('agent-card')).toHaveLength(1);
    expect(screen.getByText('portrait')).toBeTruthy();
  });

  it('shows a restart-interrupted agent neutrally, not as a fault', async () => {
    // 2026-08-19 report: a day of deploys left the Analyze card with a red
    // "Fault" badge reading "Backend restarted while this run was in
    // flight...", and users concluded the agent was broken. The whole point
    // of the fix is that this renders as information, not alarm — so assert
    // the badge is NOT the fault badge and the danger warn-line is absent.
    getAgentStats.mockResolvedValue({
      script_ai: stat({ interrupted_reason: 'restart' }),
    });
    renderGallery();
    await waitFor(() => expect(screen.getAllByTestId('agent-card')).toHaveLength(3));

    const card = screen.getByText('script_ai').closest('[data-testid="agent-card"]')!;
    const badge = card.querySelector('[data-testid="status-badge"]')!;
    expect(badge.getAttribute('data-status')).toBe('interrupted');
    expect(badge.textContent).toContain('Last run interrupted');
    // "not red" is the actual promise to the user, and data-status alone
    // would still pass if the badge were styled with the danger tokens.
    expect(badge.className).not.toMatch(/danger/);
    expect(card.querySelector('[data-testid="fault-warnline"]')).toBeNull();
    expect(card.querySelector('[data-testid="interrupted-note"]')!.textContent).toContain(
      'service restarted',
    );
  });

  it('keeps an interrupted agent out of the faults-only filter', async () => {
    // The filter and the counter are what a user checks to answer "how many
    // of my agents are broken?" — a routine deploy must not inflate it.
    getAgentStats.mockResolvedValue({
      script_ai: stat({ interrupted_reason: 'restart' }),
      portrait: stat({ fault: { kind: 'dead_runs', detail: 'Provider unreachable' } }),
    });
    renderGallery();
    await waitFor(() => expect(screen.getAllByTestId('agent-card')).toHaveLength(3));

    fireEvent.click(screen.getByText(/Faults only/));
    expect(screen.getAllByTestId('agent-card')).toHaveLength(1);
    expect(screen.getByText('portrait')).toBeTruthy();
  });

  it('says so when weekly stats fail instead of showing zeros as fact', async () => {
    // Silent degradation here would read as "every agent did nothing this
    // week" — indistinguishable from real data.
    getAgentStats.mockRejectedValue(new Error('boom'));
    renderGallery();
    await waitFor(() =>
      expect(screen.getByText(/Weekly stats unavailable/)).toBeTruthy(),
    );
    expect(screen.getAllByTestId('agent-card')).toHaveLength(3);
  });

  it('surfaces a failed agent load with a retry', async () => {
    listAgents.mockRejectedValue(new Error('nope'));
    renderGallery();
    await waitFor(() => expect(screen.getByText('Retry')).toBeTruthy());
  });

  // 2026-08-05 用户回归报告："无法点击 agent 进入详情页"。此前只有卡片里
  // 名称文字是可点按钮，卡片空白处无反应——与设计稿"点卡片进入"不符。
  describe('card click navigates to detail', () => {
    it('clicking the card body opens the agent editor', async () => {
      renderGallery();
      await waitFor(() => expect(screen.getAllByTestId('agent-card')).toHaveLength(3));
      const card = screen
        .getByText('translate')
        .closest('[data-testid="agent-card"]')! as HTMLElement;
      fireEvent.click(card);
      expect(navigateSpy).toHaveBeenCalledWith('/ai-library/agents/translate');
    });

    it('inner action buttons do NOT double-fire card navigation', async () => {
      renderGallery();
      await waitFor(() => expect(screen.getAllByTestId('agent-card')).toHaveLength(3));
      const primary = screen
        .getByText('translate')
        .closest('[data-testid="agent-card"]')!
        .querySelector('[data-testid="primary-action"]')! as HTMLElement;
      fireEvent.click(primary); // idle → chat
      expect(requestChat).toHaveBeenCalledWith('translate');
      expect(navigateSpy).not.toHaveBeenCalled();
    });
  });
});
