/**
 * A2 — 详情页两栏化：右栏是「进度轨道」与「关联」两张卡。
 *
 * 断言只覆盖两卡的存在与内容（run_count 首次显示、执行者、Project），
 * 不断言栅格布局本身 —— 布局用 CSS 媒体查询表达，jsdom 里没有意义。
 */

import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Routes, Route } from 'react-router-dom';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import type { UiIssue, AgentRef } from './types';
import type { Issue } from '../../services/issuesService';

// Inline defaults are the real UI copy — return them so assertions read
// like the screen does.
vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    // `t(key, 'Fallback')` echoes the real UI copy; `t(key, {vars})` echoes
    // the key so interpolated strings stay assertable.
    t: (key: string, arg2?: unknown) => (typeof arg2 === 'string' ? arg2 : key),
  }),
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
vi.mock('./blocks/PipelineRunStrip', () => ({ PipelineRunStrip: () => null }));
vi.mock('./DeliverablesZone', () => ({ DeliverablesZone: () => null }));
vi.mock('./RunPipelineMenu', () => ({ RunPipelineMenu: () => null }));
vi.mock('./IssueRelatedTab', () => ({ IssueRelatedTab: () => null }));
vi.mock('../../services/workflowService', () => ({
  fetchStageBoard: vi.fn(async () => ({ node: { brief: '', status: 'todo' } })),
}));

// issue.rollup — the cockpit + rail read it; the default is a running issue
// with one live run so the cockpit has something to draw.
const progressState: { value: Record<string, unknown> | null } = { value: null };
vi.mock('../../services/issuesService', async (importOriginal) => {
  const mod = await importOriginal<typeof import('../../services/issuesService')>();
  return {
    ...mod,
    getIssueProgress: vi.fn(async () => progressState.value),
    updateIssue: vi.fn(async () => ({})),
  };
});
const getRunViewAt = vi.fn(async (_runId: string, seq: number) => ({
  seq,
  view: { v: 1, phase: 'running', step: { done: 2, total: 7, label: 'Scene 2' }, current: { turn: 1, step: 2, model: 'm' }, retry: null, context: null, blocked: null, children: { total: 0, done: 0 }, ended: null, inbox_pending: 0, budget: null, question: null, last_answer: null, revision: seq },
  cost: { spent_cents: 0.2, by_step: [], by_model: {}, budget_cents: null, pct: null },
}));
const forkRun = vi.fn(async (_runId: string, _body: { at_seq: number; steer?: string }) => ({ run_id: null, session_id: '900', workflow_id: 'wf-f', issue_id: 1, forked_from: { run_id: 501, at_seq: 4 } }));
const RUN_EVENTS = [
  { seq: 1, event_type: 'user', payload: { content: 'go' }, created_at: '' },
  { seq: 2, event_type: 'step_start', payload: { turn: 1, step: 1 }, created_at: '' },
  { seq: 4, event_type: 'step_start', payload: { turn: 1, step: 2 }, created_at: '' },
];
vi.mock('../../services/aiLibraryService', () => ({
  aiLibraryService: {
    cancelRun: vi.fn(async () => undefined),
    getRunViewAt: (...a: [string, number]) => getRunViewAt(...a),
    getRunEvents: vi.fn(async () => ({ items: RUN_EVENTS, count: RUN_EVENTS.length, has_more: false })),
    getRunForks: vi.fn(async () => ({ items: [] })),
    forkRun: (...a: [string, { at_seq: number; steer?: string }]) => forkRun(...a),
  },
  RunForkRejectedError: class extends Error {
    code: string;
    status: number;
    constructor(code: string, status: number, message: string) { super(message); this.code = code; this.status = status; }
  },
}));

function mkProgress(over: Record<string, unknown> = {}) {
  return {
    issue_id: '1',
    status: 'in_progress',
    phase: 'running',
    paused_at: null,
    current_run: {
      id: '501',
      status: 'running',
      started_at: '2026-08-03T00:00:00Z',
      model: 'm',
      view: { v: 1, phase: 'running', step: { done: 3, total: 7, label: 'Drafting scene 3' }, current: { turn: 1, step: 4, model: 'm' }, retry: null, context: { used_pct: 62, window: 128000 }, blocked: null, children: { total: 0, done: 0 }, ended: null, inbox_pending: 1, budget: null, revision: 9 },
      cost: { spent_cents: 0.9 },
    },
    runs: [{ id: '501', status: 'running', started_at: '2026-08-03T00:00:00Z', ended_at: null, model: 'm', error_code: null, cost_cents: 0.9, ended: null, step: null }, { id: '500', status: 'completed', started_at: null, ended_at: null, model: 'm', error_code: null, cost_cents: 0.7, ended: { reason: 'completed' }, step: null }],
    sub_issues: { total: 2, done: 1, items: [] },
    inbox_pending: 1,
    budget: { budget_cents: 200, spent_cents: 160, pct: 80, state: 'warn' },
    origin: { kind: 'manual', origin_id: null },
    execution_state: { turn: 3 },
    computed_at: '2026-08-03T00:00:01Z',
    ...over,
  };
}

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
  beforeEach(() => {
    vi.clearAllMocks();
    progressState.value = mkProgress();
  });

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
  beforeEach(() => {
    vi.clearAllMocks();
    progressState.value = mkProgress({ phase: 'waiting_input', current_run: null, runs: [] });
  });

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


describe('IssueDetailView — cockpit + 区块注册表 (harness P4 T8)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    progressState.value = mkProgress();
  });

  it('draws the cockpit from the rollup through the selectors', async () => {
    const { container } = renderDetail(mkIssue());
    const cockpit = await waitFor(() => {
      const el = container.querySelector('[data-testid="issue-cockpit"]');
      expect(el).not.toBeNull();
      return el as HTMLElement;
    });
    expect(cockpit.querySelector('[data-testid="cockpit-steps"]')?.textContent).toMatch(/3\/7/);
    expect(cockpit.querySelector('[data-testid="cockpit-context"]')?.textContent).toMatch(/62%/);
    expect(cockpit.querySelector('[data-testid="cockpit-budget"]')?.textContent).toMatch(/\$1\.60.*\$2\.00/);
    expect(cockpit.querySelector('[data-testid="cockpit-runs"]')?.textContent).toMatch(/2.*turn 1.*step 4/);
    expect(cockpit.textContent).toContain('Drafting scene 3');
    expect(cockpit.querySelector('[data-testid="cockpit-cancel"]')).not.toBeNull();
    expect(cockpit.querySelector('[data-testid="cockpit-subline"]')?.textContent).toMatch(/issueDetail\.subIssuesDone/);
    // composer says what a comment does while the agent runs
    expect(screen.getByTestId('reply-hint').textContent).toMatch(/picked up before its next step/);
  });

  it('cancel goes through the run cancel endpoint with the current run id', async () => {
    const { aiLibraryService } = await import('../../services/aiLibraryService');
    const { container } = renderDetail(mkIssue());
    const btn = await waitFor(() => {
      const el = container.querySelector('[data-testid="cockpit-cancel"]');
      expect(el).not.toBeNull();
      return el as HTMLButtonElement;
    });
    btn.click();
    await waitFor(() => expect(aiLibraryService.cancelRun).toHaveBeenCalledWith('501'));
  });

  it('draws no cockpit and a reply hint when idle', async () => {
    progressState.value = mkProgress({ phase: 'idle', current_run: null, runs: [] });
    const { container } = renderDetail(mkIssue());
    await waitFor(() => expect(container.querySelector('[data-testid="detail-progress-panel"]')).not.toBeNull());
    await waitFor(() => expect(screen.getByTestId('reply-hint').textContent).toMatch(/starts the agent/));
    expect(container.querySelector('[data-testid="cockpit-cancel"]')).toBeNull();
  });

  it('blocks match by origin kind: a publish issue gets no stage brief, a project_stage mirror does', async () => {
    const { fetchStageBoard } = await import('../../services/workflowService');
    progressState.value = mkProgress({ origin: { kind: 'publish', origin_id: 'p-1' } });
    const first = renderDetail(mkIssue({ raw: { origin_kind: 'publish', origin_id: 'p-1' } as never }));
    await waitFor(() => expect(first.container.querySelector('[data-testid="detail-budget-panel"]')).not.toBeNull());
    expect(fetchStageBoard).not.toHaveBeenCalled();
    first.unmount();
    progressState.value = mkProgress({ origin: { kind: 'project_stage', origin_id: 'node-1' } });
    renderDetail(mkIssue({ raw: { origin_kind: 'project_stage', origin_id: 'node-1' } as never }));
    await waitFor(() => expect(fetchStageBoard).toHaveBeenCalledWith('7', 'node-1'));
  });

  it('shows the budget block with spend / budget and edits through PATCH', async () => {
    const { updateIssue } = await import('../../services/issuesService');
    const { container } = renderDetail(mkIssue());
    const panel = await waitFor(() => {
      const el = container.querySelector('[data-testid="detail-budget-panel"]');
      expect(el).not.toBeNull();
      return el as HTMLElement;
    });
    expect(panel.querySelector('[data-testid="budget-spent"]')?.textContent).toMatch(/\$1\.60.*\$2\.00/);
    const { fireEvent } = await import('@testing-library/react');
    fireEvent.click(panel.querySelector('button') as HTMLButtonElement); // Edit
    const input = await waitFor(() => {
      const el = panel.querySelector('[data-testid="budget-input"]');
      expect(el).not.toBeNull();
      return el as HTMLInputElement;
    });
    fireEvent.change(input, { target: { value: '500' } });
    fireEvent.submit(input.closest('form')!);
    await waitFor(() => expect(updateIssue).toHaveBeenCalledWith(1, { budget_cents: 500 }));
  });
});


describe('IssueDetailView — typed question (phase 2a)', () => {
  const MARKER = {
    prompt: 'Cold open or teaser?',
    since: '2026-09-08T00:00:00Z',
    issue_id: 1,
    question_id: 'q:9:2',
    kind: 'user',
    options: [{ label: 'Cold open', description: null }, { label: 'Teaser', description: null }],
    allow_free_text: false,
    run_id: '9',
  };

  beforeEach(() => {
    vi.clearAllMocks();
    progressState.value = mkProgress({ phase: 'waiting_input', current_run: null, runs: [] });
  });

  it('answers a typed marker with the label and answer_to', async () => {
    const { postIssueMessage } = await import('../../services/issueMessageService');
    (postIssueMessage as unknown as ReturnType<typeof vi.fn>).mockResolvedValue({ agent_dispatched: true });
    renderDetail(mkIssue({
      status: 'needs_followup',
      raw: {
        status: 'needs_followup',
        execution_state: { agent_outcome: 'needs_input', outcome_reason: 'Cold open or teaser?', awaiting_input: MARKER },
      } as never,
    }));
    const btn = await waitFor(() => screen.getByRole('button', { name: 'Teaser' }));
    fireEvent.click(btn);
    await waitFor(() =>
      expect(postIssueMessage).toHaveBeenCalledWith(expect.anything(), { body: 'Teaser', answer_to: 'q:9:2' }),
    );
    expect(document.querySelector('[data-testid="needs-input-card"] textarea')).toBeNull();
  });

  it('the cockpit shows the question from the run view while waiting for input', async () => {
    progressState.value = mkProgress({
      phase: 'waiting_input',
      current_run: {
        id: '501',
        status: 'running',
        started_at: '2026-08-03T00:00:00Z',
        model: 'm',
        view: {
          v: 1, phase: 'waiting_input', step: null, current: null, retry: null, context: null, blocked: null,
          children: { total: 0, done: 0 }, ended: null, inbox_pending: 0, budget: null, revision: 3,
          question: { id: 'budget:501', kind: 'budget', prompt: 'Budget exhausted', options: [{ label: 'Top up' }, { label: 'Wrap up' }, { label: 'Cancel' }], allow_free_text: false, asked_at: 'T' },
        },
        cost: { spent_cents: 120 },
      },
    });
    const { container } = renderDetail(mkIssue());
    const q = await waitFor(() => {
      const el = container.querySelector('[data-testid="cockpit-question"]');
      expect(el).not.toBeNull();
      return el as HTMLElement;
    });
    const card = q.querySelector('[data-testid="question-card"]') as HTMLElement;
    expect(card.getAttribute('data-question-kind')).toBe('budget');
    expect(card.textContent).toContain('Wrap up');
  });

  it('a parked issue draws ONE card: the detail card, never a second one in the cockpit', async () => {
    progressState.value = mkProgress({
      phase: 'waiting_input',
      execution_state: { agent_outcome: 'needs_input', outcome_reason: 'Cold open or teaser?', awaiting_input: MARKER },
      current_run: {
        id: '9', status: 'completed', started_at: '2026-08-03T00:00:00Z', model: 'm',
        view: {
          v: 1, phase: 'waiting_input', step: null, current: null, retry: null, context: null, blocked: null,
          children: { total: 0, done: 0 }, ended: { reason: 'awaiting_input' }, inbox_pending: 0, budget: null, revision: 3,
          question: { id: 'q:9:2', kind: 'user', prompt: 'Cold open or teaser?', options: MARKER.options, allow_free_text: false, asked_at: 'T' },
        },
        cost: { spent_cents: 1 },
      },
    });
    const { container } = renderDetail(mkIssue({
      status: 'needs_followup',
      raw: {
        status: 'needs_followup',
        execution_state: { agent_outcome: 'needs_input', outcome_reason: 'Cold open or teaser?', awaiting_input: MARKER },
      } as never,
    }));
    await waitFor(() => expect(container.querySelector('[data-testid="needs-input-card"]')).not.toBeNull());
    await waitFor(() => expect(container.querySelector('[data-testid="issue-cockpit"]')).not.toBeNull());
    expect(container.querySelectorAll('[data-testid="question-card"]').length).toBe(1);
    expect(container.querySelector('[data-testid="cockpit-question"]')).toBeNull();
  });
});


// ── harness 2b-1 §1: replay deep link ───────────────────────────────────────
describe('IssueDetailView — replay deep link (?run&seq)', () => {
  beforeEach(() => { getRunViewAt.mockClear(); });

  function renderAt(entry: string) {
    return render(
      <MemoryRouter initialEntries={[entry]}>
        <Routes>
          <Route
            path="/team/:teamId/todolist/:identifier"
            element={
              <IssueDetailView
                issue={mkIssue()}
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

  it('seeks the live run to the linked seq and freezes the cockpit as of it', async () => {
    progressState.value = mkProgress();
    renderAt('/team/9/todolist/NOUS-1?run=501&seq=4');
    await waitFor(() => expect(getRunViewAt).toHaveBeenCalledWith('501', 4));
    await waitFor(() => expect(screen.getByTestId('cockpit-asof')).toBeTruthy());
    // the frozen view (2 / 7), not the live rollup (3 / 7)
    expect(screen.getByTestId('cockpit-steps').textContent).toContain('2');
    expect((screen.getByTestId('cockpit-pause') as HTMLButtonElement).disabled).toBe(true);
  });

  it('a link to an older run (a fork origin) replays it in the detached panel, cockpit stays live with a Live exit', async () => {
    progressState.value = mkProgress();
    renderAt('/team/9/todolist/NOUS-1?run=499&seq=4');
    await waitFor(() => expect(getRunViewAt).toHaveBeenCalledWith('499', 4));
    await waitFor(() => expect(screen.getByTestId('detached-run-panel')).toBeTruthy());
    expect(document.getElementById('run-499')).not.toBeNull();
    expect(screen.getByTestId('cockpit-asof').getAttribute('data-mode')).toBe('other-run');
    // the live cockpit is not frozen by an older run's replay
    expect(screen.getByTestId('cockpit-steps').textContent).toContain('3');
    fireEvent.click(screen.getByTestId('cockpit-asof'));
    await waitFor(() => expect(screen.queryByTestId('detached-run-panel')).toBeNull());
  });
});


// ── harness 2b-1 §2: fork from the scrubber ─────────────────────────────────
const { listIssueMessages } = await import('../../services/issueMessageService');

describe('IssueDetailView — fork flow', () => {
  beforeEach(() => { forkRun.mockClear(); getRunViewAt.mockClear(); });

  it('Fork on a past step opens the dialog; confirming posts at_seq + steer and clears the replay', async () => {
    progressState.value = mkProgress();
    (listIssueMessages as unknown as ReturnType<typeof vi.fn>).mockResolvedValueOnce({
      messages: [{ id: 'm-run', issue_id: 1, kind: 'agent_run', author_user_id: null, author_agent_id: 'a1', agent_run_id: '501', content: null, body: null, created_at: '2026-08-03T00:00:00Z', meta: { status: 'running' } }],
    });
    render(
      <MemoryRouter initialEntries={['/team/9/todolist/NOUS-1?run=501&seq=4']}>
        <Routes>
          <Route path="/team/:teamId/todolist/:identifier" element={<IssueDetailView issue={mkIssue()} agents={[AGENT]} agentsById={{ a1: AGENT }} selfUserId="u1" onCreateSubIssue={vi.fn()} />} />
        </Routes>
      </MemoryRouter>,
    );
    await waitFor(() => expect(screen.getByTestId('replay-fork')).toBeTruthy());
    fireEvent.click(screen.getByTestId('replay-fork'));
    expect(screen.getByTestId('fork-dialog')).toBeTruthy();
    // the dialog title carries the scrubber's own position (this file's t mock
    // echoes the template; the wiring itself is asserted in ForkRunDialog.test)
    expect(screen.getByTestId('fork-dialog').textContent).toContain('Fork from');
    fireEvent.change(screen.getByTestId('fork-steer'), { target: { value: 'darker' } });
    fireEvent.click(screen.getByTestId('fork-confirm'));
    await waitFor(() => expect(forkRun).toHaveBeenCalledWith('501', { at_seq: 4, steer: 'darker' }));
    await waitFor(() => expect(screen.queryByTestId('fork-dialog')).toBeNull());
    await waitFor(() => expect(screen.queryByTestId('cockpit-asof')).toBeNull());
  });
});


describe('IssueDetailView — fork refusal', () => {
  it('a typed refusal stays in the dialog as copy for its code', async () => {
    const { RunForkRejectedError } = await import('../../services/aiLibraryService');
    forkRun.mockRejectedValueOnce(new RunForkRejectedError('run_live', 409, 'pause or cancel the running run first'));
    progressState.value = mkProgress();
    (listIssueMessages as unknown as ReturnType<typeof vi.fn>).mockResolvedValueOnce({
      messages: [{ id: 'm-run', issue_id: 1, kind: 'agent_run', author_user_id: null, author_agent_id: 'a1', agent_run_id: '501', content: null, body: null, created_at: '2026-08-03T00:00:00Z', meta: { status: 'running' } }],
    });
    render(
      <MemoryRouter initialEntries={['/team/9/todolist/NOUS-1?run=501&seq=4']}>
        <Routes>
          <Route path="/team/:teamId/todolist/:identifier" element={<IssueDetailView issue={mkIssue()} agents={[AGENT]} agentsById={{ a1: AGENT }} selfUserId="u1" onCreateSubIssue={vi.fn()} />} />
        </Routes>
      </MemoryRouter>,
    );
    await waitFor(() => expect(screen.getByTestId('replay-fork')).toBeTruthy());
    fireEvent.click(screen.getByTestId('replay-fork'));
    fireEvent.click(screen.getByTestId('fork-confirm'));
    await waitFor(() => expect(screen.getByTestId('fork-error')).toBeTruthy());
    // copy for the code (fallback echoed by this file's t mock), never the raw message
    expect(screen.getByTestId('fork-error').textContent).toContain('still going');
    expect(screen.getByTestId('fork-dialog')).toBeTruthy();
  });
});
