/**
 * Context block: the completion verifier's verdict (509), next to Status.
 * Verified / Rejected (attempt n/m) / Unverified: <reason>; unmet items and
 * the agent's own FinishIssue reason ("Agent's claim") kept apart.
 */
import React, { useState } from 'react';
import { useTranslation } from 'react-i18next';

import type { IssueVerification } from '../../../services/issuesService';
import type { IssueBlock, IssueBlockContext, IssueBlockProps } from '../issueBlocks';
import { RailCard } from './StatusBlock';

function rawOf(ctx: IssueBlockContext): Record<string, unknown> {
  return (ctx.issue.raw as Record<string, unknown> | undefined) ?? ctx.issue;
}

function stateOf(ctx: IssueBlockContext): Record<string, unknown> {
  const state = rawOf(ctx).execution_state;
  return state && typeof state === 'object' ? (state as Record<string, unknown>) : {};
}

const TONE: Record<IssueVerification['verdict'], string> = {
  pass: 'text-ok',
  fail: 'text-danger',
  unverified: 'text-warn',
};

/** The row's lifted `verification` wins; older payloads fall back to
 *  execution_state.verification. An unknown verdict string reads as none. */
export function verificationOf(ctx: IssueBlockContext): IssueVerification | null {
  const v = (rawOf(ctx).verification ?? stateOf(ctx).verification) as IssueVerification | undefined | null;
  return v && typeof v === 'object' && typeof v.verdict === 'string' && v.verdict in TONE ? v : null;
}

const VerdictBlockView: React.FC<IssueBlockProps> = ({ ctx }) => {
  const { t } = useTranslation();
  const v = verificationOf(ctx);
  const [open, setOpen] = useState(false);
  if (!v) return null;
  const attempt = v.attempt ?? 1;
  const of = v.max_attempts ?? 2;
  const label =
    v.verdict === 'pass'
      ? t('issueDetail.verdictPass', 'Verified')
      : v.verdict === 'fail'
        ? t('issueDetail.verdictFail', 'Rejected (attempt {{n}}/{{m}})', { n: attempt, m: of })
        : t('issueDetail.verdictUnverified', 'Unverified: {{reason}}', { reason: v.reason ?? '' });
  const claim = stateOf(ctx).outcome_reason;
  return (
    <RailCard title={t('issueDetail.verdict', 'Completion check')} testId="detail-verdict-panel">
      <div data-testid="verdict-label" className={`text-[13px] ${TONE[v.verdict]}`}>
        {label}
      </div>
      {v.unmet && v.unmet.length > 0 && (
        <ul data-testid="verdict-unmet" className="list-disc pl-4 text-[12px] text-ink-300">
          {v.unmet.map((u, i) => (
            <li key={i}>{u.why ? `${u.criterion}: ${u.why}` : u.criterion}</li>
          ))}
        </ul>
      )}
      {typeof claim === 'string' && claim && (
        <div data-testid="verdict-claim" className="text-[11px] text-ink-500">
          {t('issueDetail.verdictAgentClaim', "Agent's claim")}: {claim}
        </div>
      )}
      {v.reason && v.verdict !== 'unverified' && (
        <button type="button" className="text-[11px] text-ink-600 hover:underline" onClick={() => setOpen(!open)}>
          {open ? t('common.hide', 'Hide') : t('issueDetail.verdictDetails', 'Details')}
        </button>
      )}
      {open && <p className="text-[11px] text-ink-500 whitespace-pre-wrap">{v.reason}</p>}
    </RailCard>
  );
};

export const verdictBlock: IssueBlock = {
  id: 'verdict',
  zone: 'context',
  order: 12,
  match: (ctx) => verificationOf(ctx) !== null,
  component: VerdictBlockView,
};
