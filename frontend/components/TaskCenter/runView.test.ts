import { describe, expect, it } from 'vitest';

import { budgetState, contextGauge, currentStep, endedReason, retryState, selectRunCost, selectRunView, stepProgress } from './runView';

const view = {
  v: 1,
  phase: 'running',
  step: { done: 3, total: 7, label: 'Drafting scene 3' },
  current: { turn: 1, step: 4, model: 'm' },
  retry: { attempt: 2, max: 4, delay_ms: 3000, model: 'm', at: '2026-09-05T00:00:00Z' },
  context: { used_pct: 62, window: 128000 },
  blocked: null,
  children: { total: 0, done: 0 },
  ended: null,
  inbox_pending: 1,
  budget: { pct: 82, state: 'warn', spent_cents: 82 },
  revision: 9,
};

describe('runView selectors', () => {
  it('reads every cockpit value from view', () => {
    const meta = { view, cost: { spent_cents: 0.9, by_step: [], by_model: {}, budget_cents: 100, pct: 82 } };
    const v = selectRunView(meta);
    expect(stepProgress(v)).toEqual({ done: 3, total: 7, label: 'Drafting scene 3' });
    expect(currentStep(v)).toEqual({ turn: 1, step: 4, model: 'm' });
    expect(retryState(v, Date.parse('2026-09-05T00:00:01Z'))).toEqual({ attempt: 2, max: 4, waitingSeconds: 2 });
    expect(retryState(v, Date.parse('2026-09-05T00:01:00Z'))?.waitingSeconds).toBe(0);
    expect(contextGauge(v)).toEqual({ used_pct: 62, window: 128000 });
    expect(budgetState(v)).toEqual({ pct: 82, state: 'warn', spent_cents: 82 });
    expect(endedReason(v)).toBeNull();
    expect(selectRunCost(meta)?.spent_cents).toBe(0.9);
  });

  it('is all null for a row without view (older than mig 453) or with garbage', () => {
    for (const meta of [null, undefined, {}, { todos: {} }, { view: 'x' }, { view: { v: 'nope' } }]) {
      const v = selectRunView(meta as Record<string, unknown> | null | undefined);
      expect(v).toBeNull();
      expect(stepProgress(v)).toBeNull();
      expect(retryState(v, 0)).toBeNull();
      expect(contextGauge(v)).toBeNull();
      expect(budgetState(v)).toBeNull();
      expect(endedReason(v)).toBeNull();
      expect(currentStep(v)).toBeNull();
      expect(selectRunCost(meta as Record<string, unknown> | null | undefined)).toBeNull();
    }
  });

  it('ended reason and malformed sub-objects', () => {
    const v = selectRunView({ view: { ...view, ended: { reason: 'interrupted' }, step: { done: 'x' }, budget: { pct: 50, state: 'ok' } } });
    expect(endedReason(v)).toBe('interrupted');
    expect(stepProgress(v)).toBeNull();
    expect(budgetState(v)).toBeNull(); // only warn/over colour the card
  });
});
