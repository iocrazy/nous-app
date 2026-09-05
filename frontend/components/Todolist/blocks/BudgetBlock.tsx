/**
 * Context block: the issue budget (harness P4 §1-⑤). Shows spend / budget
 * from the rollup, colours at warn (80 %) / over (100 %), and edits
 * `issues.budget_cents` through PATCH /issues/{id} (clear = unlimited).
 */
import React, { useState } from 'react';
import { useTranslation } from 'react-i18next';

import { updateIssue } from '../../../services/issuesService';
import type { IssueBlock, IssueBlockProps } from '../issueBlocks';
import { RailCard } from './StatusBlock';

export function formatCents(c: number | null | undefined): string {
  if (c === null || c === undefined) return '—';
  return c >= 100 ? `$${(c / 100).toFixed(2)}` : `¢${c.toFixed(c < 1 ? 2 : 1)}`;
}

const TONE: Record<string, string> = {
  ok: 'bg-ok',
  warn: 'bg-warn',
  over: 'bg-danger',
};

const BudgetBlockView: React.FC<IssueBlockProps> = ({ ctx }) => {
  const { t } = useTranslation();
  const rollup = ctx.rollup;
  const budget = rollup?.budget ?? {
    budget_cents: (ctx.issue.raw as Record<string, unknown> | undefined)?.budget_cents as number | null ?? null,
    spent_cents: 0,
    pct: null,
    state: 'ok' as const,
  };
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState('');
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const save = async (patch: { budget_cents?: number; clear_budget?: boolean }) => {
    setSaving(true);
    setError(null);
    try {
      await updateIssue(Number(ctx.issue.id), patch);
      setEditing(false);
      ctx.env.onIssueChanged?.();
    } catch (err) {
      console.error('[BudgetBlock] budget update failed', err);
      setError(err instanceof Error ? err.message : 'update failed');
    } finally {
      setSaving(false);
    }
  };

  const pct = budget.pct ?? 0;
  return (
    <RailCard
      title={t('issueDetail.budget', 'Budget')}
      testId="detail-budget-panel"
      aside={
        !editing && (
          <button
            type="button"
            className="text-[11px] text-ink-500 hover:text-ink-300"
            onClick={() => {
              setDraft(budget.budget_cents != null ? String(budget.budget_cents) : '');
              setEditing(true);
            }}
          >
            {t('issueDetail.editBudget', 'Edit')}
          </button>
        )
      }
    >
      <div className="flex items-baseline justify-between text-[13px]">
        <span className="tabular-nums text-ink-200" data-testid="budget-spent">
          {formatCents(budget.spent_cents)}
          <span className="text-ink-500"> / {budget.budget_cents != null ? formatCents(budget.budget_cents) : t('issueDetail.unlimited', 'unlimited')}</span>
        </span>
        {budget.pct != null && <span className={`text-[11px] tabular-nums ${budget.state === 'ok' ? 'text-ink-500' : budget.state === 'warn' ? 'text-warn' : 'text-danger'}`}>{budget.pct}%</span>}
      </div>
      {budget.budget_cents != null && (
        <div className="h-1 rounded-full bg-ink-800 overflow-hidden">
          <div className={`h-full ${TONE[budget.state] ?? 'bg-ok'}`} style={{ width: `${Math.min(100, pct)}%` }} />
        </div>
      )}
      <div className="text-[11px] text-ink-600">{t('issueDetail.budgetRule', 'Warns at 80%, records at 100%')}</div>
      {editing && (
        <form
          className="flex items-center gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            const cents = Number(draft);
            if (!Number.isInteger(cents) || cents < 0) {
              setError(t('issueDetail.budgetInvalid', 'Enter a whole number of cents, 0 or more'));
              return;
            }
            void save({ budget_cents: cents });
          }}
        >
          <input
            data-testid="budget-input"
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            inputMode="numeric"
            placeholder="cents"
            className="w-24 rounded border border-ink-700 bg-ink-900 px-2 py-1 text-[12px] text-ink-200 tabular-nums"
          />
          <button type="submit" disabled={saving} className="text-[12px] text-ok hover:underline disabled:opacity-50">
            {t('common.save', 'Save')}
          </button>
          <button type="button" disabled={saving} onClick={() => void save({ clear_budget: true })} className="text-[12px] text-ink-400 hover:underline disabled:opacity-50">
            {t('issueDetail.unlimited', 'unlimited')}
          </button>
          <button type="button" onClick={() => setEditing(false)} className="text-[12px] text-ink-600 hover:underline">
            {t('common.cancel', 'Cancel')}
          </button>
        </form>
      )}
      {error && <div className="text-[11px] text-danger">{error}</div>}
    </RailCard>
  );
};

export const budgetBlock: IssueBlock = {
  id: 'budget',
  zone: 'context',
  order: 50,
  match: () => true,
  component: BudgetBlockView,
};
