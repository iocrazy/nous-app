/**
 * Phase 2a §2 — the cockpit's target-level pause / resume. Typed outcome:
 * success re-reads the issue (onIssueChanged), failure lands on the cockpit.
 */
import { render, screen, cleanup, fireEvent, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { CockpitBlockView } from './CockpitBlock';
import { IssueControlError } from '../../../services/issuesService';
import type { IssueBlockContext } from '../issueBlocks';
import type { IssueProgress } from '../../../services/issuesService';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    // fallback template + interpolation, so as-of numbers are assertable
    t: (key: string, fallback?: unknown, vars?: Record<string, unknown>) => {
      const tpl = typeof fallback === 'string' ? fallback : key;
      const v = (typeof fallback === 'object' && fallback ? fallback : vars) as Record<string, unknown> | undefined;
      return tpl.replace(/\{\{(\w+)\}\}/g, (_, n) => String(v?.[n] ?? `{{${n}}}`));
    },
  }),
}));
const pauseIssue = vi.fn();
const resumeIssue = vi.fn();
vi.mock('../../../services/issuesService', async (importOriginal) => {
  const mod = await importOriginal<typeof import('../../../services/issuesService')>();
  return { ...mod, pauseIssue: (...a: unknown[]) => pauseIssue(...a), resumeIssue: (...a: unknown[]) => resumeIssue(...a) };
});
vi.mock('../../../services/aiLibraryService', () => ({ aiLibraryService: { cancelRun: vi.fn(async () => ({})) } }));

afterEach(cleanup);
beforeEach(() => {
  pauseIssue.mockReset().mockResolvedValue({ issue_id: '5', paused_at: '2026-09-08T00:00:00Z', run_id: 'r1' });
  resumeIssue.mockReset().mockResolvedValue({ issue_id: '5', dispatched: true, reason: 'dispatched', workflow_id: 'wf', run_id: null });
});

function rollup(phase: IssueProgress['phase'], withRun = true): IssueProgress {
  return {
    issue_id: '5',
    status: 'in_progress',
    phase,
    paused_at: phase === 'paused' ? '2026-09-08T00:00:00Z' : null,
    current_run: withRun ? { id: 'r1', status: 'running', started_at: null, model: null, view: {}, cost: {} } : null,
    runs: [],
    sub_issues: { total: 0, done: 0, items: [] },
    inbox_pending: 0,
    budget: { budget_cents: null, spent_cents: 0, pct: null, state: 'ok' },
    efficiency: { runs: 0, steps: 0, tool_calls: 0, tool_errors: 0, deliverables: 0, avg_run_ms: null, cost_per_deliverable_cents: null, turn_end_reasons: {} },
    origin: { kind: 'manual' },
    execution_state: {},
    computed_at: '2026-09-08T00:00:00Z',
  };
}

function ctx(phase: IssueProgress['phase'], onIssueChanged = vi.fn(), withRun = true): IssueBlockContext {
  return { issue: { id: '5', status: 'in_progress' }, rollup: rollup(phase, withRun), originKind: null, phase, env: { onIssueChanged } };
}

describe('CockpitBlockView — pause / resume (phase 2a §2)', () => {
  it('offers Pause while running and calls pauseIssue with the issue id', async () => {
    const onIssueChanged = vi.fn();
    render(<CockpitBlockView ctx={ctx('running', onIssueChanged)} />);
    expect(screen.queryByTestId('cockpit-resume')).toBeNull();
    fireEvent.click(screen.getByTestId('cockpit-pause'));
    // The id exactly as the host gave it ('5'), with no Number() round trip:
    // the assertion used to pin that conversion, and the intent has not changed.
    await waitFor(() => expect(pauseIssue).toHaveBeenCalledWith('5'));
    await waitFor(() => expect(onIssueChanged).toHaveBeenCalled());
    expect(screen.queryByTestId('cockpit-control-error')).toBeNull();
  });

  it('offers Resume while paused (no Pause) and calls resumeIssue', async () => {
    render(<CockpitBlockView ctx={ctx('paused', vi.fn(), false)} />);
    expect(screen.queryByTestId('cockpit-pause')).toBeNull();
    fireEvent.click(screen.getByTestId('cockpit-resume'));
    await waitFor(() => expect(resumeIssue).toHaveBeenCalledWith('5'));
  });

  it('shows the failure on the cockpit as copy for the code, never the raw body', async () => {
    // The service's documented rejection: code from detail.code, message = server text.
    resumeIssue.mockRejectedValueOnce(new IssueControlError('run_state_unavailable', 503, 'run state unavailable'));
    render(<CockpitBlockView ctx={ctx('paused', vi.fn(), false)} />);
    fireEvent.click(screen.getByTestId('cockpit-resume'));
    const err = await screen.findByTestId('cockpit-control-error');
    expect(err.textContent).toBe('Run state unavailable — try again in a moment');
  });

  it('says generic for a network failure instead of leaking the stack', async () => {
    pauseIssue.mockRejectedValueOnce(new TypeError('Failed to fetch'));
    render(<CockpitBlockView ctx={ctx('running')} />);
    fireEvent.click(screen.getByTestId('cockpit-pause'));
    const err = await screen.findByTestId('cockpit-control-error');
    expect(err.textContent).toBe('Could not update the issue');
  });

  it('draws no controls when the issue is idle', () => {
    render(<CockpitBlockView ctx={ctx('idle', vi.fn(), false)} />);
    expect(screen.queryByTestId('cockpit-pause')).toBeNull();
    expect(screen.queryByTestId('cockpit-resume')).toBeNull();
  });
});

// ── harness 2b-1 §1: replay "as of step N" ──────────────────────────────────
import { ReplayContext } from '../replayContext';

describe('CockpitBlockView — replay as-of (harness 2b-1 §1)', () => {
  // step.done (todo progress 7/12) deliberately differs from current.step (5):
  // the badge must show the STEP COORDINATE, not the todo count.
  const frozenView = { v: 1, phase: 'running', step: { done: 7, total: 12, label: 'Scene 7' }, current: { turn: 2, step: 5, model: 'm' }, retry: null, context: null, blocked: null, children: { total: 0, done: 0 }, ended: null, inbox_pending: 0, budget: null, question: null, last_answer: null, revision: 30 } as never;
  const frozenCost = { spent_cents: 250, by_step: [], by_model: {}, budget_cents: null, pct: null } as never;

  it('reads the frozen view + cost, shows the as-of coordinates and disables the controls', () => {
    const seek = vi.fn();
    const c = ctx('running');
    c.rollup!.budget = { budget_cents: 1000, spent_cents: 900, pct: 90, state: 'warn' };
    render(
      <ReplayContext.Provider value={{ runId: 'r1', seq: 30, view: frozenView, cost: frozenCost, loading: false, seek, seekRun: vi.fn() }}>
        <CockpitBlockView ctx={c} />
      </ReplayContext.Provider>,
    );
    expect(screen.getByTestId('cockpit-steps').textContent).toContain('7');
    expect(screen.getByTestId('cockpit-steps').textContent).toContain('12');
    expect(screen.getByTestId('cockpit-asof').textContent).toContain('as of turn 2 · step 5');
    // spend as of that step ($2.50), not the live issue spend ($9.00); cap stays
    expect(screen.getByTestId('cockpit-budget').textContent).toContain('$2.50');
    expect(screen.getByTestId('cockpit-budget').textContent).not.toContain('$9.00');
    expect(screen.getByTestId('cockpit-budget').textContent).toContain('$10.00');
    expect((screen.getByTestId('cockpit-pause') as HTMLButtonElement).disabled).toBe(true);
    expect((screen.getByTestId('cockpit-cancel') as HTMLButtonElement).disabled).toBe(true);
    // the badge is the way back to Live even when the scrubber is out of view
    fireEvent.click(screen.getByTestId('cockpit-asof'));
    expect(seek).toHaveBeenCalledWith(null);
  });

  it('a frozen view without a cost shows — for spend, never the live number', () => {
    const c = ctx('running');
    c.rollup!.budget = { budget_cents: 1000, spent_cents: 900, pct: 90, state: 'warn' };
    render(
      <ReplayContext.Provider value={{ runId: 'r1', seq: 30, view: frozenView, cost: null, loading: false, seek: vi.fn(), seekRun: vi.fn() }}>
        <CockpitBlockView ctx={c} />
      </ReplayContext.Provider>,
    );
    expect(screen.getByTestId('cockpit-budget').textContent).toContain('—');
    expect(screen.getByTestId('cockpit-budget').textContent).not.toContain('$9.00');
  });

  it('a replay of some other run does not freeze the live panel (but keeps the Live exit)', () => {
    render(
      <ReplayContext.Provider value={{ runId: 'r-other', seq: 30, view: frozenView, cost: null, loading: false, seek: vi.fn(), seekRun: vi.fn() }}>
        <CockpitBlockView ctx={ctx('running')} />
      </ReplayContext.Provider>,
    );
    expect(screen.getByTestId('cockpit-asof').getAttribute('data-mode')).toBe('other-run');
    expect(screen.getByTestId('cockpit-budget').textContent).not.toContain('$2.50');
    expect((screen.getByTestId('cockpit-pause') as HTMLButtonElement).disabled).toBe(false);
  });

  it('seq null (Live) is not frozen even when attached', () => {
    render(
      <ReplayContext.Provider value={{ runId: 'r1', seq: null, view: null, cost: null, loading: false, seek: vi.fn(), seekRun: vi.fn() }}>
        <CockpitBlockView ctx={ctx('running')} />
      </ReplayContext.Provider>,
    );
    expect(screen.queryByTestId('cockpit-asof')).toBeNull();
  });
});

// ── harness 2b-1 §2: forked-from chip ──────────────────────────────────────
describe('CockpitBlockView — fork chip (harness 2b-1 §2)', () => {
  it('a forked run shows where it came from; clicking scrubs the original run to that seq', () => {
    const seekRun = vi.fn();
    const c = ctx('running');
    c.rollup!.current_run!.view = { v: 1, phase: 'running', step: null, current: { turn: 1, step: 1, model: 'm' }, retry: null, context: null, blocked: null, children: { total: 0, done: 0 }, ended: null, inbox_pending: 0, budget: null, fork: { of_run_id: 310819108761481, at_seq: 4 }, revision: 3 };
    render(
      <ReplayContext.Provider value={{ runId: 'r1', seq: null, view: null, cost: null, loading: false, seek: vi.fn(), seekRun }}>
        <CockpitBlockView ctx={c} />
      </ReplayContext.Provider>,
    );
    const chip = screen.getByTestId('cockpit-fork-chip');
    expect(chip.textContent).toContain('Forked from run #761481 @ seq 4');
    fireEvent.click(chip);
    // the origin row / detached panel scrolls itself into view once attached
    expect(seekRun).toHaveBeenCalledWith('310819108761481', 4);
  });

  it('replaying some other run (fork origin) keeps a way back to Live on the cockpit', () => {
    const seek = vi.fn();
    render(
      <ReplayContext.Provider value={{ runId: '310819108761481', seq: 4, view: null, cost: null, loading: false, seek, seekRun: vi.fn() }}>
        <CockpitBlockView ctx={ctx('running')} />
      </ReplayContext.Provider>,
    );
    const btn = screen.getByTestId('cockpit-asof');
    expect(btn.getAttribute('data-mode')).toBe('other-run');
    expect(btn.textContent).toContain('Replaying run #761481');
    // not frozen: the live controls stay usable
    expect((screen.getByTestId('cockpit-pause') as HTMLButtonElement).disabled).toBe(false);
    fireEvent.click(btn);
    expect(seek).toHaveBeenCalledWith(null);
  });

  it('no chip on a run that is not a fork', () => {
    render(<CockpitBlockView ctx={ctx('running')} />);
    expect(screen.queryByTestId('cockpit-fork-chip')).toBeNull();
  });
});


// ── harness 2b-1 §3: Tools cell ─────────────────────────────────────────────
describe('CockpitBlockView — tools timed out (harness 2b-1 §3)', () => {
  const base = { v: 1, phase: 'running', step: null, current: { turn: 1, step: 2, model: 'm' }, retry: null, context: null, blocked: null, children: { total: 0, done: 0 }, ended: null, inbox_pending: 0, budget: null, revision: 5 };
  it('shows the cell with the count and the last tool once something timed out', () => {
    const c = ctx('running');
    c.rollup!.current_run!.view = { ...base, tools: { timed_out: 1, last_timed_out: 'ResourceFetch' } };
    render(<CockpitBlockView ctx={c} />);
    const cell = screen.getByTestId('cockpit-tools');
    expect(cell.textContent).toContain('1 timed out');
    expect(cell.textContent).toContain('ResourceFetch');
  });
  it('no cell while nothing has timed out', () => {
    const c = ctx('running');
    c.rollup!.current_run!.view = { ...base, tools: { timed_out: 0, last_timed_out: null } };
    render(<CockpitBlockView ctx={c} />);
    expect(screen.queryByTestId('cockpit-tools')).toBeNull();
  });
});

// ── harness 2b-2 §5: Sub-agents cell + wake-up subline ──────────────────────
describe('CockpitBlockView — sub-agents and wake-ups (harness 2b-2 §5)', () => {
  const base = { v: 1, phase: 'running', step: null, current: { turn: 1, step: 2, model: 'm' }, retry: null, context: null, blocked: null, children: { total: 0, done: 0, running: 0, async_pending: 0, last: null }, ended: null, inbox_pending: 0, budget: null, revision: 5 };

  it('counts done against total and calls out the ones still in the background', () => {
    const c = ctx('running');
    c.rollup!.current_run!.view = { ...base, children: { total: 2, done: 1, running: 0, async_pending: 1, last: { child_run_id: '9', subagent_type: 'librarian', status: 'completed' } } };
    render(<CockpitBlockView ctx={c} />);
    const cell = screen.getByTestId('cockpit-children');
    expect(cell.textContent).toContain('1');
    expect(cell.textContent).toContain('/2');
    expect(cell.textContent).toContain('1 in background');
  });

  it('no cell when the run dispatched no sub-agent', () => {
    const c = ctx('running');
    c.rollup!.current_run!.view = { ...base };
    render(<CockpitBlockView ctx={c} />);
    expect(screen.queryByTestId('cockpit-children')).toBeNull();
  });

  it('the subline says when the next wake-up fires', () => {
    const c = ctx('running');
    c.rollup!.current_run!.view = { ...base, wakeups: [{ schedule_id: 'sc-1', fire_at: '2026-09-11T01:00:00Z', note: 'check the render' }] };
    render(<CockpitBlockView ctx={c} />);
    expect(screen.getByTestId('cockpit-wakeup').textContent).toContain('Wakes at');
  });

  it('no wake-up text when nothing is armed', () => {
    const c = ctx('running');
    c.rollup!.current_run!.view = { ...base };
    render(<CockpitBlockView ctx={c} />);
    expect(screen.queryByTestId('cockpit-wakeup')).toBeNull();
  });
});

// ── harness 3a §5: Outputs cell ─────────────────────────────────────────────
describe('CockpitBlockView — outputs (harness 3a §5)', () => {
  const base = { v: 1, phase: 'running', step: null, current: { turn: 1, step: 2, model: 'm' }, retry: null, context: null, blocked: null, children: { total: 0, done: 0, running: 0, async_pending: 0, last: null }, ended: null, inbox_pending: 0, budget: null, revision: 5 };

  it('counts what the run registered and says how many were revisions', () => {
    const c = ctx('running');
    c.rollup!.current_run!.view = {
      ...base,
      outputs: { total: 3, revised: 1, last: { kind: 'generated_media', ref_id: '77', version: 2, title: 'Shot #1' }, seen: [] },
    };
    render(<CockpitBlockView ctx={c} />);
    const cell = screen.getByTestId('cockpit-outputs');
    expect(cell.textContent).toContain('3');
    expect(cell.textContent).toContain('1 revised');
  });

  it('no cell when the run registered nothing, and none at all for a run older than the registry', () => {
    const c = ctx('running');
    c.rollup!.current_run!.view = { ...base, outputs: { total: 0, revised: 0, last: null, seen: [] } };
    render(<CockpitBlockView ctx={c} />);
    expect(screen.queryByTestId('cockpit-outputs')).toBeNull();
    cleanup();
    const old = ctx('running');
    old.rollup!.current_run!.view = { ...base };
    render(<CockpitBlockView ctx={old} />);
    expect(screen.queryByTestId('cockpit-outputs')).toBeNull();
    // the four fixed cells still read — an absent key must not blank the cockpit
    expect(screen.getByTestId('cockpit-steps')).toBeTruthy();
  });

  it('drops the revised note when nothing was replaced', () => {
    const c = ctx('running');
    c.rollup!.current_run!.view = { ...base, outputs: { total: 2, revised: 0, last: null, seen: [] } };
    render(<CockpitBlockView ctx={c} />);
    expect(screen.getByTestId('cockpit-outputs').textContent).not.toContain('revised');
  });

  it('widens the grid to seven columns when all three conditional cells are on', () => {
    const c = ctx('running');
    c.rollup!.current_run!.view = {
      ...base,
      tools: { timed_out: 1, last_timed_out: 'ResourceFetch' },
      children: { total: 1, done: 1, running: 0, async_pending: 0, last: null },
      outputs: { total: 1, revised: 0, last: null, seen: [] },
    };
    const { container } = render(<CockpitBlockView ctx={c} />);
    // Tailwind never scans a template string, so the literal has to be there.
    expect(container.querySelector('.sm\\:grid-cols-7')).toBeTruthy();
  });
});

// ── 3c §3.3: efficiency cells ───────────────────────────────────────────────
describe('CockpitBlockView — efficiency cells (3c §3.3)', () => {
  // `current_run.view` 是后端折好的 run view：`selectRunView` 要求 `v` + `phase`，
  // 少了它整块 view 读成 null、Outputs 格根本不渲染。照真实 wire 形状写。
  const VIEW = { v: 1, phase: 'running', step: null, current: { turn: 1, step: 2, model: 'm' }, retry: null, context: null, blocked: null, children: { total: 0, done: 0, running: 0, async_pending: 0, last: null }, ended: null, inbox_pending: 0, budget: null, revision: 5 };

  function withEfficiency(
    eff: Partial<IssueProgress['efficiency']>,
    runOutputs: Record<string, unknown> | null = { total: 4, revised: 1, last: null, seen: [] },
  ): IssueBlockContext {
    const base = ctx('running');
    const r = base.rollup as IssueProgress;
    return { ...base, rollup: { ...r, efficiency: { ...r.efficiency, ...eff },
      current_run: r.current_run
        ? { ...r.current_run, view: runOutputs ? { ...VIEW, outputs: runOutputs } : { ...VIEW } }
        : null } };
  }

  it('prices each output in its own cell, labelled as the whole-issue figure', () => {
    render(<CockpitBlockView ctx={withEfficiency({ deliverables: 4, cost_per_deliverable_cents: 5 })} />);
    const cell = screen.getByTestId('cockpit-cost-per-output');
    expect(cell).toHaveTextContent('/ output');
    // 口径写在格子上：计数是全议题所有 run 的，不是当前这条 run 的。
    expect(cell.getAttribute('title')).toBe('Across all runs of this issue');
  });

  it('shows nothing rather than \u00a20.00 when no output has been priced', () => {
    render(<CockpitBlockView ctx={withEfficiency({ deliverables: 0, cost_per_deliverable_cents: null })} />);
    expect(screen.queryByTestId('cockpit-cost-per-output')).toBeNull();
  });

  it('prices outputs off the whole issue even when the live run registered none', () => {
    // deliverables 是全议题口径；把成本挂在当前 run 的 outputs 上，会让「上一条
    // run 产出了 3 件、这条刚开始」的常见时刻整格消失。
    render(<CockpitBlockView ctx={withEfficiency({ deliverables: 3, cost_per_deliverable_cents: 7 }, null)} />);
    expect(screen.queryByTestId('cockpit-outputs')).toBeNull();
    expect(screen.getByTestId('cockpit-cost-per-output')).toHaveTextContent('/ output');
  });

  it('survives a response from a backend that predates the efficiency key', () => {
    // 前端（Cloudflare Pages）与后端（gpupc）两条部署链独立触发，前端常先上。
    // 唯一的 ErrorBoundary 在 index.tsx 根部，所以驾驶舱读 undefined 不是「少两个
    // 格子」，是整站白屏。类型上这个键是必填的（mock 纪律不变），组件仍然要扛住
    // 这个兼容窗口。
    const base = ctx('running');
    const r = base.rollup as IssueProgress;
    const { efficiency: _dropped, ...withoutEfficiency } = r;
    const c = { ...base, rollup: withoutEfficiency as unknown as IssueProgress };
    render(<CockpitBlockView ctx={c} />);
    expect(screen.queryByTestId('cockpit-cost-per-output')).toBeNull();
    expect(screen.queryByTestId('cockpit-tool-errors')).toBeNull();
    // 面板其余部分照常
    expect(screen.getByTestId('cockpit-steps')).toBeTruthy();
    expect(screen.getByTestId('cockpit-budget')).toBeTruthy();
    expect(screen.getByTestId('cockpit-runs')).toBeTruthy();
  });

  it('hides both efficiency cells while replaying a frozen step', () => {
    // 它们是**全议题、当下**的数，跟「as of 某一步」不是同一个时刻 —— 与冻结时
    // 花费只显示那一步的 spend 同一语义。
    const frozenView = { ...VIEW, outputs: { total: 4, revised: 1, last: null, seen: [] } } as never;
    render(
      <ReplayContext.Provider value={{ runId: 'r1', seq: 30, view: frozenView, cost: null, loading: false, seek: vi.fn(), seekRun: vi.fn() }}>
        <CockpitBlockView ctx={withEfficiency({ deliverables: 4, cost_per_deliverable_cents: 5, tool_calls: 20, tool_errors: 3 })} />
      </ReplayContext.Provider>,
    );
    expect(screen.queryByTestId('cockpit-cost-per-output')).toBeNull();
    expect(screen.queryByTestId('cockpit-tool-errors')).toBeNull();
    // 冻结的是效率账，不是整块面板
    expect(screen.getByTestId('cockpit-steps')).toBeTruthy();
  });

  it('surfaces tool errors as their own cell, and hides it when nothing failed', () => {
    const { unmount } = render(<CockpitBlockView ctx={withEfficiency({ tool_calls: 20, tool_errors: 3 })} />);
    expect(screen.getByTestId('cockpit-tool-errors')).toHaveTextContent('3 errors');
    expect(screen.getByTestId('cockpit-tool-errors')).toHaveTextContent('20 calls');
    unmount();
    render(<CockpitBlockView ctx={withEfficiency({ tool_calls: 20, tool_errors: 0 })} />);
    expect(screen.queryByTestId('cockpit-tool-errors')).toBeNull();
  });
});


// ── FH2 T6: a fallback context window is marked, not silently trusted ───────
describe('CockpitBlockView — context window source (FH2 T6)', () => {
  const base = { v: 1, phase: 'running', step: null, current: { turn: 1, step: 2, model: 'm' }, retry: null, blocked: null, children: { total: 0, done: 0 }, ended: null, inbox_pending: 0, budget: null, revision: 5 };
  it('prefixes ~ and explains the default window when nobody configured one', () => {
    const c = ctx('running');
    c.rollup!.current_run!.view = { ...base, context: { used_pct: 41, window: 28000, window_source: 'fallback' } };
    render(<CockpitBlockView ctx={c} />);
    const cell = screen.getByTestId('cockpit-context');
    expect(cell.textContent).toContain('~41%');
    expect(cell.getAttribute('title')).toBe(
      'No window configured for this model — gauge uses the default (28,000 tokens). Set it in Admin → AI Models.',
    );
  });
  it.each(['catalog', 'builtin'])('a %s window reads plain, with no hint', (source) => {
    const c = ctx('running');
    c.rollup!.current_run!.view = { ...base, context: { used_pct: 41, window: 131072, window_source: source } };
    render(<CockpitBlockView ctx={c} />);
    const cell = screen.getByTestId('cockpit-context');
    expect(cell.textContent).not.toContain('~');
    expect(cell.textContent).toContain('41%');
    expect(cell.getAttribute('title')).toBeNull();
  });
});
