/**
 * 3b §3.4 — the Budget block's Media row.
 *
 * Media spend is the part of a run's bill that is NOT a token charge, and it
 * is the part a person can act on (fewer regenerations, a cheaper size). The
 * row is drawn only when there IS media spend: a permanent `Media ¢0.00`
 * teaches nothing and states that images were free, which is the same
 * fabricated zero `formatOutputCost` refuses everywhere else.
 */
import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type { IssueBlockContext } from '../issueBlocks';
import { BudgetBlockView } from './BudgetBlock';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, fallback?: unknown, vars?: Record<string, unknown>) => {
      const tpl = typeof fallback === 'string' ? fallback : key;
      const v = (typeof fallback === 'object' && fallback ? fallback : vars) as Record<string, unknown> | undefined;
      return tpl.replace(/\{\{(\w+)\}\}/g, (_m, n: string) => String(v?.[n] ?? `{{${n}}}`));
    },
  }),
}));
vi.mock('../../../services/issuesService', () => ({ updateIssue: vi.fn() }));

afterEach(cleanup);

const ctx = (mediaCents: number): IssueBlockContext =>
  ({
    rollup: {
      budget: { budget_cents: null, spent_cents: 30, pct: null, state: 'ok' },
      current_run: { cost: { spent_cents: 30, media_cents: mediaCents } },
    },
    issue: { raw: {} },
    env: {},
  }) as unknown as IssueBlockContext;

describe('BudgetBlockView — media spend', () => {
  it('adds a Media row only when media spend exists', () => {
    render(<BudgetBlockView ctx={ctx(24)} />);
    expect(screen.getByTestId('budget-media').textContent).toContain('Media ¢24.00');
  });

  it('draws no Media row at zero', () => {
    render(<BudgetBlockView ctx={ctx(0)} />);
    expect(screen.queryByTestId('budget-media')).toBeNull();
  });

  it('a run view written before media was tracked still renders, without a Media row', () => {
    // `media_cents` is a 3b addition; every row older than it lacks the key.
    // The claim worth making is that the block KEEPS WORKING — the absent row
    // alone would also be what a crash-free NaN produces, so it proves nothing
    // on its own.
    const older = {
      rollup: { budget: { budget_cents: null, spent_cents: 30, pct: null, state: 'ok' }, current_run: { cost: { spent_cents: 30 } } },
      issue: { raw: {} },
      env: {},
    } as unknown as IssueBlockContext;
    const { container } = render(<BudgetBlockView ctx={older} />);
    expect(screen.getByTestId('budget-spent').textContent).toContain('¢30');
    expect(screen.queryByTestId('budget-media')).toBeNull();
    expect(container.textContent).not.toContain('NaN');
  });
});
