/**
 * A2 — 详情页两栏化：右栏是「进度轨道」与「关联」两张卡。
 *
 * 断言只覆盖两卡的存在与内容（run_count 首次显示、执行者、Project），
 * 不断言栅格布局本身 —— 布局用 CSS 媒体查询表达，jsdom 里没有意义。
 */

import { render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Routes, Route } from 'react-router-dom';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import type { UiIssue, AgentRef } from './types';
import type { Issue } from '../../services/issuesService';

// Inline defaults are the real UI copy — return them so assertions read
// like the screen does.
vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string, fallback?: string) => fallback ?? key }),
}));

vi.mock('../../services/usageService', () => ({
  usageService: {
    getIssueUsage: vi.fn(async () => ({
      issue_id: '1',
      prompt_tokens: 10000,
      completion_tokens: 4000,
      total_tokens: 14000,
      cost_cents: 20,
      run_count: 2,
    })),
  },
}));

vi.mock('../../services/issueMessageService', () => ({
  listIssueMessages: vi.fn(async () => ({ messages: [] })),
  postIssueMessage: vi.fn(async () => ({})),
  getCommentTriggerPreview: vi.fn(async () => null),
  simulateAgentRunComplete: vi.fn(),
  AgentNotDispatchedError: class extends Error {},
}));

vi.mock('../../services/issueChatSocket', () => ({
  openIssueChatSocket: vi.fn(() => Promise.reject(new Error('no ws in tests'))),
}));

vi.mock('../../supabaseClient', () => ({ getSupabaseClient: () => null }));
vi.mock('../Toast', () => ({ useToast: () => ({ addToast: vi.fn() }) }));

// Child panels do their own fetching — out of scope for this view's test.
vi.mock('./PipelineRunStrip', () => ({ PipelineRunStrip: () => null }));
vi.mock('./DeliverablesZone', () => ({ DeliverablesZone: () => null }));
vi.mock('./StageBriefMirror', () => ({ StageBriefMirror: () => null }));
vi.mock('./RunPipelineMenu', () => ({ RunPipelineMenu: () => null }));
vi.mock('./IssueRelatedTab', () => ({ IssueRelatedTab: () => null }));

const { IssueDetailView } = await import('./IssueDetailView');

const AGENT: AgentRef = { id: 'a1', slug: 'writer', name: 'Script Writer' };

function mkIssue(over: Partial<UiIssue> = {}): UiIssue {
  const raw = {
    id: 1,
    identifier: 'NOUS-1',
    title: 'Draft the pilot script',
    status: 'in_progress',
    priority: 'medium',
    project_id: 7,
    assignee_agent_id: 'a1',
    dbos_workflow_id: 'wf-1',
    origin_kind: 'manual',
    parent_id: null,
    started_at: '2026-08-03T00:00:00Z',
    execution_state: { turn: 3 },
    ...(over.raw ?? {}),
  } as unknown as Issue;
  return {
    id: 1,
    identifier: 'NOUS-1',
    title: 'Draft the pilot script',
    description: null,
    status: 'in_progress',
    priority: 'medium',
    assignee: AGENT,
    project: { id: 7, name: 'Pilot Season' },
    parent_id: null,
    created_at: '2026-08-03T00:00:00Z',
    updated_at: '2026-08-03T00:00:00Z',
    last_activity_at: '2026-08-03T00:00:00Z',
    ...over,
    raw,
  } as UiIssue;
}

function renderDetail(issue: UiIssue) {
  return render(
    <MemoryRouter initialEntries={['/team/9/todolist/NOUS-1']}>
      <Routes>
        <Route
          path="/team/:teamId/todolist/:identifier"
          element={
            <IssueDetailView
              issue={issue}
              agents={[AGENT]}
              agentsById={{ a1: AGENT }}
              selfUserId="u1"
              onCreateSubIssue={vi.fn()}
            />
          }
        />
      </Routes>
    </MemoryRouter>,
  );
}

describe('IssueDetailView — 右栏进度/关联轨道', () => {
  beforeEach(() => vi.clearAllMocks());

  it('renders the progress panel with status, run count and assignee', async () => {
    const { container } = renderDetail(mkIssue());
    const panel = await waitFor(() => {
      const el = container.querySelector('[data-testid="detail-progress-panel"]');
      expect(el).not.toBeNull();
      return el as HTMLElement;
    });
    expect(panel.textContent).toMatch(/Status/);
    expect(panel.textContent).toMatch(/Runs/);
    // run_count from usageService — never surfaced before this panel existed.
    await waitFor(() => expect(panel.textContent).toMatch(/2/));
    expect(panel.textContent).toMatch(/Script Writer/);
  });

  it('renders the links panel with the owning project', async () => {
    const { container } = renderDetail(mkIssue());
    const panel = await waitFor(() => {
      const el = container.querySelector('[data-testid="detail-links-panel"]');
      expect(el).not.toBeNull();
      return el as HTMLElement;
    });
    expect(panel.textContent).toMatch(/Pilot Season/);
  });

  it('keeps both panels mounted for an issue with no project', async () => {
    const { container } = renderDetail(mkIssue({ project: undefined }));
    await waitFor(() => {
      expect(container.querySelector('[data-testid="detail-progress-panel"]')).not.toBeNull();
      expect(container.querySelector('[data-testid="detail-links-panel"]')).not.toBeNull();
    });
    expect(screen.queryByText('Pilot Season')).toBeNull();
  });
});

describe('IssueDetailView — needs_input 提问卡挂载', () => {
  beforeEach(() => vi.clearAllMocks());

  it('mounts the question card when the agent declared needs_input', async () => {
    const { container } = renderDetail(mkIssue({
      status: 'needs_followup',
      raw: {
        status: 'needs_followup',
        execution_state: { agent_outcome: 'needs_input', outcome_reason: 'Cold open or teaser?' },
      } as never,
    }));
    const card = await waitFor(() => {
      const el = container.querySelector('[data-testid="needs-input-card"]');
      expect(el).not.toBeNull();
      return el as HTMLElement;
    });
    expect(card.textContent).toContain('Cold open or teaser?');
  });

  it('does NOT mount the card for an empty_output stall parked at the same status', async () => {
    // Nothing was asked — a question card here would invent a question.
    const { container } = renderDetail(mkIssue({
      status: 'needs_followup',
      raw: {
        status: 'needs_followup',
        execution_state: { agent_outcome: 'empty_output', outcome_reason: 'Agent produced no output' },
      } as never,
    }));
    await waitFor(() => {
      expect(container.querySelector('[data-testid="detail-progress-panel"]')).not.toBeNull();
    });
    expect(container.querySelector('[data-testid="needs-input-card"]')).toBeNull();
  });
});
