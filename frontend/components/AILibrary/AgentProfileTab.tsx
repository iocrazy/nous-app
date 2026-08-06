// frontend/components/AILibrary/AgentProfileTab.tsx
// B2 — "this agent's paperwork" (spec 2026-08-02 §B2).
//
// The cold half of the detail page: the caps you set on it (spend budget, run
// timeout, concurrency) and the version history you reach for when a prompt
// edit went wrong. Absorbs the old Versions sub-tab and the budget block that
// used to sit at the bottom of Overview.
//
// What it no longer holds is the dashboard — 14-day charts, spend breakdown,
// "used by". That is ACTUAL spend, a different question from the LIMITS here,
// and stacking them made this page read as one long thing about money that
// then turned into version history. It has its own tab now: AgentCostTab.

import React from 'react';
import { useTranslation } from 'react-i18next';
import type { AILibraryAgent } from '../../types';
import { VersionHistoryPanel } from './VersionHistoryPanel';

interface AgentProfileTabProps {
  agent: AILibraryAgent;
  slug: string;
  draft: Partial<AILibraryAgent>;
  updateDraft: <K extends keyof AILibraryAgent>(key: K, value: AILibraryAgent[K]) => void;
  catalogLocked: boolean;
  /**
   * PATCH the shared draft. Owned by AgentEditor — the same call its header
   * Save makes, surfaced here because the header button is Persona-only and
   * these fields are the one editable thing on this tab.
   */
  onSave: () => void | Promise<void>;
  saving: boolean;
  /** Refetch the agent after a rollback so the editor picks up the content. */
  onRollback: () => void;
}

export const AgentProfileTab: React.FC<AgentProfileTabProps> = ({
  agent,
  slug,
  draft,
  updateDraft,
  catalogLocked,
  onSave,
  saving,
  onRollback,
}) => {
  const { t } = useTranslation();
  return (
    <div className="space-y-6">
      <section className="space-y-4 text-sm">
        {/* "Cost & limits" until the actual-spend half moved to the Cost tab;
            what is left is only the caps, and the old title now points at a
            different tab's content. */}
        <h3 className="text-sm font-semibold text-ink-200">
          {t('aiLibrary.agents.limitsSection', 'Budget & limits')}
        </h3>
        <BudgetFields
          tokenBudget={draft.monthly_token_budget ?? null}
          costCentsBudget={draft.monthly_cost_cents_budget ?? null}
          disabled={catalogLocked}
          onTokenChange={(v) => updateDraft('monthly_token_budget', v)}
          onCostChange={(v) => updateDraft('monthly_cost_cents_budget', v)}
        />
        <RunLimitFields
          timeoutSec={draft.timeout_sec ?? null}
          maxConcurrentRuns={draft.max_concurrent_runs ?? null}
          disabled={catalogLocked}
          onTimeoutChange={(v) => updateDraft('timeout_sec', v)}
          onConcurrencyChange={(v) => updateDraft('max_concurrent_runs', v)}
        />
        <div className="pt-1 text-xs text-ink-500">
          {t('aiLibrary.agents.boundSkills')}:{' '}
          <span className="font-medium text-ink-200">{agent.skill_ids.length}</span>
        </div>

        {/* These four inputs are the only editable thing on this tab, and the
            header Save renders on Persona only — so without a button here the
            fields accept typing and quietly discard it on tab switch. Same
            shape as the Permissions tab's own Save. */}
        {!catalogLocked && (
          <div className="flex justify-end">
            <button
              type="button"
              onClick={() => void onSave()}
              disabled={saving}
              data-testid="profile-save-limits"
              className="rounded-lg border border-ink-700 bg-ink-800 px-4 py-2 text-sm font-medium text-ink-200 transition-colors hover:bg-ink-700 disabled:cursor-not-allowed disabled:opacity-50"
            >
              {saving ? t('common.saving') : t('common.save', 'Save')}
            </button>
          </div>
        )}
      </section>

      {/* Same single-heading rule as the skill editor: the panel brings its own
          header (icon + current-version badge + refresh), so a wrapper title
          here just stacks a second "Versions" on top of it. */}
      <section className="overflow-hidden rounded-lg border border-ink-800">
        <VersionHistoryPanel kind="agent" slug={slug} onRollback={onRollback} />
      </section>
    </div>
  );
};

/**
 * Run limits (mig 286, paperclip P4). timeout_sec bounds one run's tool loop
 * (checked between LLM iterations); max_concurrent_runs is a pre-flight cap
 * enforced by RunRecorder. Blank = unlimited.
 */
const RunLimitFields: React.FC<{
  timeoutSec: number | null;
  maxConcurrentRuns: number | null;
  disabled: boolean;
  onTimeoutChange: (value: number | null) => void;
  onConcurrencyChange: (value: number | null) => void;
}> = ({ timeoutSec, maxConcurrentRuns, disabled, onTimeoutChange, onConcurrencyChange }) => {
  const { t } = useTranslation();
  const parseIntOr = (raw: string, min: number): number | null => {
    if (raw === '') return null;
    const parsed = Number(raw);
    return Number.isFinite(parsed) ? Math.max(min, Math.floor(parsed)) : null;
  };
  return (
    <div className="space-y-1.5">
      <div className="text-xs font-medium uppercase tracking-wide text-ink-500">
        {t('aiLibrary.agents.limits.sectionLabel', 'Run limits')}
      </div>
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
        <div>
          <label className="block text-xs font-medium text-ink-400">
            {t('aiLibrary.agents.limits.timeoutLabel', 'Timeout (sec)')}
          </label>
          <input
            type="number"
            min={0}
            step={10}
            value={timeoutSec ?? ''}
            placeholder={t('aiLibrary.agents.budget.unlimitedPlaceholder', 'Unlimited')}
            onChange={(e) => onTimeoutChange(parseIntOr(e.target.value, 0))}
            disabled={disabled}
            className="mt-1 w-full rounded-md border border-ink-700 bg-ink-800 px-3 py-2 text-sm text-ink-100 focus:border-indigo-500 focus:outline-none disabled:opacity-60 disabled:cursor-not-allowed tabular-nums"
          />
          <p className="mt-1 text-xs text-ink-500">
            {t(
              'aiLibrary.agents.limits.timeoutHint',
              "Max wall-clock per run, checked between LLM iterations. Blank or 0 = no cap.",
            )}
          </p>
        </div>
        <div>
          <label className="block text-xs font-medium text-ink-400">
            {t('aiLibrary.agents.limits.concurrencyLabel', 'Max concurrent runs')}
          </label>
          <input
            type="number"
            min={1}
            step={1}
            value={maxConcurrentRuns ?? ''}
            placeholder={t('aiLibrary.agents.budget.unlimitedPlaceholder', 'Unlimited')}
            onChange={(e) => onConcurrencyChange(parseIntOr(e.target.value, 1))}
            disabled={disabled}
            className="mt-1 w-full rounded-md border border-ink-700 bg-ink-800 px-3 py-2 text-sm text-ink-100 focus:border-indigo-500 focus:outline-none disabled:opacity-60 disabled:cursor-not-allowed tabular-nums"
          />
          <p className="mt-1 text-xs text-ink-500">
            {t(
              'aiLibrary.agents.limits.concurrencyHint',
              "New runs are rejected while this many are already running. Blank = unlimited.",
            )}
          </p>
        </div>
      </div>
    </div>
  );
};

const BudgetFields: React.FC<{
  tokenBudget: number | null;
  costCentsBudget: number | null;
  disabled: boolean;
  onTokenChange: (value: number | null) => void;
  onCostChange: (value: number | null) => void;
}> = ({ tokenBudget, costCentsBudget, disabled, onTokenChange, onCostChange }) => {
  const { t } = useTranslation();
  // Display dollars for the cost budget to match the Runs tab's cost column,
  // but we still PATCH the column in cents. 500 cents ⇢ "5.00" displayed.
  const dollarsStr = costCentsBudget != null ? (costCentsBudget / 100).toFixed(2) : '';
  return (
    <div className="space-y-1.5">
      <div className="text-xs font-medium uppercase tracking-wide text-ink-500">
        {t('aiLibrary.agents.budget.sectionLabel', 'Monthly budget')}
      </div>
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
        <div>
          <label className="block text-xs font-medium text-ink-400">
            {t('aiLibrary.agents.budget.tokenBudgetLabel', 'Token budget')}
          </label>
          <input
            type="number"
            min={0}
            step={1000}
            value={tokenBudget ?? ''}
            placeholder={t('aiLibrary.agents.budget.unlimitedPlaceholder', 'Unlimited')}
            onChange={(e) => {
              const raw = e.target.value;
              if (raw === '') {
                onTokenChange(null);
                return;
              }
              const parsed = Number(raw);
              onTokenChange(Number.isFinite(parsed) ? Math.max(0, Math.floor(parsed)) : null);
            }}
            disabled={disabled}
            className="mt-1 w-full rounded-md border border-ink-700 bg-ink-800 px-3 py-2 text-sm text-ink-100 focus:border-indigo-500 focus:outline-none disabled:opacity-60 disabled:cursor-not-allowed tabular-nums"
          />
          <p className="mt-1 text-xs text-ink-500">
            {t(
              'aiLibrary.agents.budget.tokenBudgetHint',
              'Cap on total prompt+completion tokens this calendar month. Blank or 0 = unlimited.',
            )}
          </p>
        </div>
        <div>
          <label className="block text-xs font-medium text-ink-400">
            {t('aiLibrary.agents.budget.costBudgetLabel', 'Cost budget (USD)')}
          </label>
          <div className="mt-1 flex items-center gap-2">
            <span className="text-ink-500 text-sm">$</span>
            <input
              type="number"
              min={0}
              step={0.01}
              value={dollarsStr}
              placeholder={t('aiLibrary.agents.budget.unlimitedPlaceholder', 'Unlimited')}
              onChange={(e) => {
                const raw = e.target.value;
                if (raw === '') {
                  onCostChange(null);
                  return;
                }
                const dollars = Number(raw);
                if (!Number.isFinite(dollars)) {
                  onCostChange(null);
                  return;
                }
                // cents = round(dollars * 100) — avoid float drift like 19.99 * 100 === 1998.9999...
                onCostChange(Math.max(0, Math.round(dollars * 100)));
              }}
              disabled={disabled}
              className="flex-1 rounded-md border border-ink-700 bg-ink-800 px-3 py-2 text-sm text-ink-100 focus:border-indigo-500 focus:outline-none disabled:opacity-60 disabled:cursor-not-allowed tabular-nums"
            />
          </div>
          <p className="mt-1 text-xs text-ink-500">
            {t(
              'aiLibrary.agents.budget.costBudgetHint',
              "Hard cap on this month's spend. The sweeper pauses the agent within ~60s of crossing the cap.",
            )}
          </p>
        </div>
      </div>
    </div>
  );
};

export default AgentProfileTab;
