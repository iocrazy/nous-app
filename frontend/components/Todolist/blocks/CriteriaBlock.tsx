/**
 * Context block: the issue's acceptance criteria (509) — what the completion
 * verifier judges against. Editable through PATCH /issues/{id}; a person's
 * edit stamps source='user' and locks the agent out.
 */
import React, { useState } from 'react';
import { useTranslation } from 'react-i18next';

import { updateIssue } from '../../../services/issuesService';
import type { IssueBlock, IssueBlockProps } from '../issueBlocks';
import { RailCard } from './StatusBlock';

type CriteriaPatch = { acceptance_criteria?: string; clear_acceptance_criteria?: boolean };

function rawOf(issue: Record<string, unknown>): Record<string, unknown> {
  return (issue.raw as Record<string, unknown> | undefined) ?? issue;
}

export const CriteriaBlockView: React.FC<IssueBlockProps> = ({ ctx }) => {
  const { t } = useTranslation();
  const raw = rawOf(ctx.issue);
  const criteria = typeof raw.acceptance_criteria === 'string' ? raw.acceptance_criteria : '';
  const source = raw.acceptance_criteria_source === 'agent' ? 'agent' : 'user';
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState('');
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const save = async (patch: CriteriaPatch) => {
    setSaving(true);
    setError(null);
    try {
      await updateIssue(String(ctx.issue.id), patch);
      setEditing(false);
      ctx.env.onIssueChanged?.();
    } catch (err) {
      console.error('[CriteriaBlock] criteria update failed', err);
      setError(err instanceof Error ? err.message : 'update failed');
    } finally {
      setSaving(false);
    }
  };

  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    const value = draft.trim();
    if (!value) {
      setError(t('issueDetail.criteriaRequired', 'Write at least one criterion'));
      return;
    }
    void save({ acceptance_criteria: value });
  };

  return (
    <RailCard
      title={t('issueDetail.criteria', 'Acceptance criteria')}
      testId="detail-criteria-panel"
      aside={
        !editing && (
          <button
            type="button"
            className="text-[11px] text-ink-500 hover:text-ink-300"
            onClick={() => {
              setDraft(criteria);
              setError(null);
              setEditing(true);
            }}
          >
            {t('issueDetail.editCriteria', 'Edit')}
          </button>
        )
      }
    >
      {!editing && criteria && (
        <>
          <p data-testid="criteria-text" className="whitespace-pre-wrap text-[12.5px] text-ink-200">
            {criteria}
          </p>
          {source === 'agent' && (
            <span data-testid="criteria-source" className="text-[10px] uppercase tracking-wider text-info">
              {t('issueDetail.criteriaProposedByAgent', 'Proposed by agent')}
            </span>
          )}
        </>
      )}
      {!editing && !criteria && (
        <p data-testid="criteria-empty" className="text-[12px] text-ink-600">
          {t(
            'issueDetail.criteriaNone',
            'None yet — the agent will propose criteria before it starts, or write your own.',
          )}
        </p>
      )}
      {editing && (
        <form className="space-y-2" onSubmit={submit}>
          <textarea
            data-testid="criteria-input"
            value={draft}
            maxLength={4000}
            onChange={(e) => setDraft(e.target.value)}
            rows={4}
            className="w-full rounded border border-ink-700 bg-ink-900 px-2 py-1 text-[12px] text-ink-200"
            placeholder={t('issueDetail.criteriaPlaceholder', 'e.g. Scenes 1-2 each get two shots with an image')}
          />
          <div className="flex items-center gap-3">
            <button type="submit" disabled={saving} className="text-[12px] text-ok hover:underline disabled:opacity-50">
              {t('common.save', 'Save')}
            </button>
            {criteria && (
              <button
                type="button"
                disabled={saving}
                onClick={() => void save({ clear_acceptance_criteria: true })}
                className="text-[12px] text-ink-400 hover:underline disabled:opacity-50"
              >
                {t('issueDetail.clearCriteria', 'Clear')}
              </button>
            )}
            <button type="button" onClick={() => setEditing(false)} className="text-[12px] text-ink-600 hover:underline">
              {t('common.cancel', 'Cancel')}
            </button>
          </div>
        </form>
      )}
      {error && <div className="text-[11px] text-danger">{error}</div>}
    </RailCard>
  );
};

export const criteriaBlock: IssueBlock = {
  id: 'criteria',
  zone: 'context',
  order: 15,
  match: () => true,
  component: CriteriaBlockView,
};
