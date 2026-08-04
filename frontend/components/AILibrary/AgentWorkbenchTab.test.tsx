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
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import type { AILibraryAgent } from '../../types';
import type { NeedsInputItem } from '../../services/issuesService';

const listNeedsInput = vi.fn();
vi.mock('../../services/issuesService', () => ({
  listNeedsInput: (...a: unknown[]) => listNeedsInput(...a),
}));

const getAgentStats = vi.fn(async () => ({}) as Record<string, unknown>);
const listAgentRunGroups = vi.fn(async () => ({ items: [] }) as { items: unknown[] });
vi.mock('../../services/aiLibraryService', () => ({
  aiLibraryService: {
    getAgentStats: (...a: unknown[]) => getAgentStats(...(a as [])),
    listAgentRunGroups: (...a: unknown[]) => listAgentRunGroups(...(a as [])),
  },
}));

// The children own their own fetching; these tests are about this component.
const runsSplitProps = vi.fn();
vi.mock('./AgentRunsSplit', () => ({
  AgentRunsSplit: (p: Record<string, unknown>) => {
    runsSplitProps(p);
    return <div data-testid="runs-split" />;
  },
}));
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
    listNeedsInput.mockResolvedValue({ items: [] });
    getAgentStats.mockClear();
    listAgentRunGroups.mockClear();
    listAgentRunGroups.mockResolvedValue({ items: [] });
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

describe('AgentWorkbenchTab — 最近对话左栏', () => {
  const group = (over: Record<string, unknown> = {}) => ({
    group_key: 'g1',
    conversation_id: '901',
    run_count: 12,
    prompt_tokens: 1000,
    completion_tokens: 500,
    cost_cents: 40,
    first_started_at: '2026-08-01T09:00:00Z',
    last_started_at: '2026-08-01T10:00:00Z',
    any_running: false,
    error_count: 0,
    latest_run_id: 'r1',
    latest_status: 'succeeded',
    trigger: 'chat',
    latest_output_summary: 'Second act outline, draft three',
    ...over,
  });

  beforeEach(() => {
    listNeedsInput.mockReset();
    listNeedsInput.mockResolvedValue({ items: [] });
    getAgentStats.mockClear();
    getAgentStats.mockResolvedValue({});
    listAgentRunGroups.mockClear();
    listAgentRunGroups.mockResolvedValue({ items: [] });
  });

  it('never renders a raw JSON summary as the row title', async () => {
    // A storyboard run ends with a JSON payload; the row used to print it.
    listAgentRunGroups.mockResolvedValue({
      items: [group({ latest_output_summary: '{"shots": [{"title": "wide"}]}' })],
    });
    renderTab();

    await waitFor(() => expect(screen.getByTestId('conversation-row')).toBeTruthy());
    const row = screen.getByTestId('conversation-row');
    expect(row.textContent).not.toContain('{');
    expect(row.textContent).toContain('Untitled conversation');
  });

  it('never falls back to the trigger — it names the agent, not the chat', async () => {
    listAgentRunGroups.mockResolvedValue({
      items: [group({ latest_output_summary: null, trigger: 'script_ai' })],
    });
    renderTab();

    await waitFor(() => expect(screen.getByTestId('conversation-row')).toBeTruthy());
    expect(screen.getByTestId('conversation-row').textContent).not.toContain('script_ai');
  });

  it('keeps both columns shrinkable so a long title cannot push one off-screen', async () => {
    // The regression: grid/flex items default to min-width:auto, so one long
    // unbreakable string grew the left column past its track and the right
    // column (waiting / routines / this week) vanished behind a horizontal
    // scrollbar. `truncate` alone does not fix that — the box has to be
    // allowed to be narrower than its content.
    listAgentRunGroups.mockResolvedValue({
      items: [group({ latest_output_summary: 'A'.repeat(400) })],
    });
    const { container } = renderTab();

    await waitFor(() => expect(screen.getByTestId('week-stats')).toBeTruthy());

    const grid = container.querySelector('.grid')!;
    for (const child of Array.from(grid.children)) {
      expect(child.className).toContain('min-w-0');
    }
    // The right column must still be rendered, not merely present-but-pushed.
    expect(screen.getByTestId('week-stats')).toBeTruthy();
  });

  it('lists a conversation by its summary, not its run id', async () => {
    listAgentRunGroups.mockResolvedValue({ items: [group()] });
    renderTab();

    await waitFor(() => expect(screen.getByTestId('conversation-row')).toBeTruthy());
    expect(screen.getByText('Second act outline, draft three')).toBeTruthy();
  });

  it('falls back to a neutral label when a run produced no summary', async () => {
    // This used to fall back to `trigger`, which printed a bare `routine` /
    // `script_ai` — a word that describes how the run started, not what the
    // conversation was about.
    listAgentRunGroups.mockResolvedValue({
      items: [group({ latest_output_summary: null, trigger: 'routine' })],
    });
    renderTab();

    await waitFor(() => expect(screen.getByTestId('conversation-row')).toBeTruthy());
    expect(screen.queryByText('routine')).toBeNull();
    expect(screen.getByTestId('conversation-row').textContent).toContain('Untitled conversation');
  });

  it('shows an empty state rather than an endless spinner', async () => {
    renderTab();
    await waitFor(() => expect(listAgentRunGroups).toHaveBeenCalled());
    expect(screen.queryByTestId('conversation-row')).toBeNull();
    expect(screen.getByText('No conversations yet')).toBeTruthy();
  });

  it('degrades to the empty state when the feed fails', async () => {
    listAgentRunGroups.mockRejectedValue(new Error('boom'));
    const spy = vi.spyOn(console, 'error').mockImplementation(() => {});
    renderTab();

    await waitFor(() => expect(spy).toHaveBeenCalled());
    expect(screen.getByText('No conversations yet')).toBeTruthy();
    spy.mockRestore();
  });

  it('opens the clicked conversation, not just the runs list', async () => {
    // The row used to dump the user on the runs view's default selection —
    // the NEWEST conversation — so clicking the third row showed the first.
    listAgentRunGroups.mockResolvedValue({ items: [group({ group_key: 'g9' })] });
    runsSplitProps.mockClear();
    renderTab();

    await waitFor(() => expect(screen.getByTestId('conversation-row')).toBeTruthy());
    fireEvent.click(screen.getByTestId('conversation-row'));

    const props = runsSplitProps.mock.calls.at(-1)![0] as {
      initialGroup?: { group_key: string };
    };
    expect(props.initialGroup?.group_key).toBe('g9');
  });

  it('leaves the runs view on its own default when opened from the header link', async () => {
    listAgentRunGroups.mockResolvedValue({ items: [group()] });
    runsSplitProps.mockClear();
    renderTab();

    await waitFor(() => expect(screen.getByTestId('all-runs-link')).toBeTruthy());
    fireEvent.click(screen.getByTestId('all-runs-link'));

    const props = runsSplitProps.mock.calls.at(-1)![0] as { initialGroup?: unknown };
    expect(props.initialGroup).toBeNull();
  });

  it('swaps in the runs view from the header link, and back', async () => {
    renderTab();
    await waitFor(() => expect(screen.getByTestId('all-runs-link')).toBeTruthy());

    fireEvent.click(screen.getByTestId('all-runs-link'));
    expect(screen.getByTestId('runs-split')).toBeTruthy();
    // The workbench columns are gone while the runs view is up.
    expect(screen.queryByTestId('week-stats')).toBeNull();

    fireEvent.click(screen.getByText(/Back to overview/));
    await waitFor(() => expect(screen.getByTestId('week-stats')).toBeTruthy());
  });

  it('renders the week stats from the 7-day rollup', async () => {
    getAgentStats.mockResolvedValue({
      [AGENT_ID]: {
        runs_7d: 12,
        tokens_7d: 38000,
        cost_cents_7d: 163,
        running_count: 0,
        needs_input_count: 0,
        fault: null,
      },
    });
    renderTab();

    await waitFor(() => expect(screen.getByTestId('week-stats')).toBeTruthy());
    expect(screen.getByText('12')).toBeTruthy();
    expect(screen.getByText('38k')).toBeTruthy();
    // Spend comes from the SAME 7-day rollup as runs and tokens. It used to
    // be absent from the endpoint, so the only figure available was the
    // dashboard's 14-day total — a different window under a "this week"
    // label. Asserting it here pins the window, not just the formatting.
    expect(screen.getByText('$1.63')).toBeTruthy();
  });

  it('shows zeroes rather than blanks when this agent has no rollup row', async () => {
    getAgentStats.mockResolvedValue({ 'another-agent': { runs_7d: 5 } });
    renderTab();

    await waitFor(() => expect(screen.getByTestId('week-stats')).toBeTruthy());
    // Both chips read zero, so scope the assertion to the panel rather than
    // asking the document for a bare "0".
    expect(screen.getByTestId('week-stats').textContent).toContain('runs 0');
  });
});
