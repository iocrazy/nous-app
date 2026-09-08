/**
 * Context block (first card of the rail): where the issue stands, how many
 * runs it took, how long, who is on it, what it cost. Phase and run count come
 * from the rollup (derived from the runs); the usage line keeps the W3c token
 * total. (Formerly DetailProgressPanel + IssueCostLine in IssueDetailView.)
 */
import React, { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Coins } from 'lucide-react';

import { formatIssueCostLine } from '../../../pages/usagePanelHelpers';
import { usageService, type IssueUsage } from '../../../services/usageService';
import { formatElapsed } from '../formatElapsed';
import type { IssueBlock, IssueBlockProps } from '../issueBlocks';
import { IssueStatusIcon } from '../IssueStatusIcon';
import type { IssueStatus } from '../../../services/issuesService';

const TERMINAL = new Set(['done', 'cancelled']);

export const RailRow: React.FC<{ label: string; children: React.ReactNode }> = ({ label, children }) => (
  <div className="flex items-baseline justify-between gap-3 text-[13px]">
    <span className="text-[11px] uppercase tracking-wider text-ink-500 shrink-0">{label}</span>
    <span className="text-ink-300 text-right min-w-0 truncate">{children}</span>
  </div>
);

export const RailCard: React.FC<React.PropsWithChildren<{ title: string; testId?: string; aside?: React.ReactNode }>> = ({
  title,
  testId,
  aside,
  children,
}) => (
  <section data-testid={testId} className="rounded-lg border border-ink-800/80 bg-ink-900/40 px-3 py-2.5 space-y-2">
    <div className="flex items-center justify-between gap-2">
      <h2 className="text-[11px] uppercase tracking-wider text-ink-500">{title}</h2>
      {aside}
    </div>
    {children}
  </section>
);

const StatusBlockView: React.FC<IssueBlockProps> = ({ ctx }) => {
  const { t } = useTranslation();
  const issue = ctx.issue;
  const rollup = ctx.rollup;
  const status = String(issue.status ?? '');
  const live = !TERMINAL.has(status);
  const [usage, setUsage] = useState<IssueUsage | null>(null);
  const [nowMs, setNowMs] = useState(() => Date.now());

  useEffect(() => {
    let cancelled = false;
    usageService
      .getIssueUsage(String(issue.id))
      .then((u) => {
        if (!cancelled) setUsage(u);
      })
      .catch((err) => console.error('[StatusBlock] usage load failed', err));
    return () => {
      cancelled = true;
    };
  }, [issue.id, ctx.env.refreshKey, rollup?.runs.length]);

  useEffect(() => {
    if (!live) return;
    const id = setInterval(() => setNowMs(Date.now()), 1000);
    return () => clearInterval(id);
  }, [live]);

  const raw = (issue.raw ?? {}) as Record<string, unknown>;
  const startMs = raw.started_at ? new Date(String(raw.started_at)).getTime() : NaN;
  const endMs = live ? nowMs : new Date(String(issue.updated_at)).getTime();
  const elapsed = Number.isFinite(startMs) && Number.isFinite(endMs) ? Math.max(0, Math.floor((endMs - startMs) / 1000)) : null;
  const runCount = rollup ? rollup.runs.length : usage?.run_count ?? 0;
  // Phase 2a §4: WHY the issue is blocked / stalled, in full — a truncated
  // reason is the one line the user came here to read.
  const execState = (raw.execution_state ?? null) as Record<string, unknown> | null;
  const stalled = ctx.phase === 'blocked' || execState?.agent_outcome === 'empty_output';
  const reasonText = stalled
    ? ((execState?.error_message as string | null | undefined) ??
      (execState?.outcome_reason as string | null | undefined) ??
      null)
    : null;
  const reasonTone = ctx.phase === 'blocked' ? 'text-danger' : 'text-warn';
  const currentStep = rollup?.current_run?.view?.current as { turn?: number; step?: number } | undefined;

  return (
    <RailCard title={t('issueDetail.progress', 'Progress')} testId="detail-progress-panel">
      <RailRow label={t('issueDetail.status', 'Status')}>
        <span className="inline-flex items-center gap-1.5">
          <IssueStatusIcon status={status as IssueStatus} size={13} />
          {status.replace(/_/g, ' ')}
          {ctx.phase && ctx.phase !== 'idle' && <span className="text-ink-500">· {ctx.phase.replace(/_/g, ' ')}</span>}
        </span>
      </RailRow>
      <RailRow label={t('issueDetail.runs', 'Runs')}>
        <span className="tabular-nums">
          {runCount}
          {currentStep?.turn != null && (
            <span className="text-ink-500">
              {' '}
              · turn {currentStep.turn}
              {currentStep.step != null ? ` · step ${currentStep.step}` : ''}
            </span>
          )}
        </span>
      </RailRow>
      {elapsed != null && (
        <RailRow label={t('issueDetail.elapsed', 'Elapsed')}>
          <span className="tabular-nums">{formatElapsed(elapsed)}</span>
        </RailRow>
      )}
      {reasonText && (
        <div className="text-[13px]" data-testid="status-reason">
          <span className="text-[11px] uppercase tracking-wider text-ink-500">{t('issueDetail.reason', 'Reason')}</span>
          <p className={`mt-0.5 whitespace-pre-wrap break-words leading-relaxed ${reasonTone}`}>{reasonText}</p>
        </div>
      )}
      {ctx.env.assigneeName && <RailRow label={t('issueDetail.assignee', 'Assignee')}>{ctx.env.assigneeName}</RailRow>}
      {usage && usage.total_tokens > 0 && (
        <div className="mt-3 flex items-center gap-1.5 text-xs text-ink-500">
          <Coins size={13} className="text-ink-600" />
          <span>{formatIssueCostLine(usage.total_tokens, usage.cost_cents)}</span>
        </div>
      )}
    </RailCard>
  );
};

export const statusBlock: IssueBlock = {
  id: 'status',
  zone: 'context',
  order: 10,
  match: () => true,
  component: StatusBlockView,
};
