/**
 * Cockpit (zone `cockpit`, top of the article): one glance = where the agent
 * is, how far along, how much context and budget are left, and the one control
 * set that exists today (pause / resume the issue, cancel the running run). Everything reads the rollup through
 * runView.ts selectors; nothing here touches metadata_json's shape.
 */
import React, { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { GitFork, Pause, Play, Square } from 'lucide-react';

import { aiLibraryService } from '../../../services/aiLibraryService';
import { pauseIssue, resumeIssue } from '../../../services/issuesService';
import {
  budgetState,
  contextGauge,
  currentStep,
  retryState,
  selectRunView,
  stepProgress,
  toolsState,
} from '../../TaskCenter/runView';
import { formatElapsed } from '../formatElapsed';
import { controlErrorText } from '../issueControlErrors';
import type { IssueBlock, IssueBlockProps } from '../issueBlocks';
import { QuestionCard } from '../QuestionCard';
import { isReplaying, useReplay } from '../replayContext';
import { questionFromMarker, questionFromRunView } from '../questionTypes';
import { formatCents } from './BudgetBlock';

const PHASE_TONE: Record<string, string> = {
  running: 'text-ok bg-ok-soft border-ok-line',
  waiting_input: 'text-warn bg-warn-soft border-warn-line',
  paused: 'text-info bg-info-soft border-info-line',
  blocked: 'text-danger bg-danger-soft border-danger-line',
  done: 'text-ink-400 bg-ink-900 border-ink-800',
  idle: 'text-ink-400 bg-ink-900 border-ink-800',
};

const Cell: React.FC<React.PropsWithChildren<{ label: string; testId: string; bar?: { pct: number; tone: string } }>> = ({
  label,
  testId,
  bar,
  children,
}) => (
  <div className="rounded-md border border-ink-800/80 bg-ink-900/40 px-3 py-2 min-w-0" data-testid={testId}>
    <div className="text-[10px] uppercase tracking-wider text-ink-500">{label}</div>
    <div className="mt-0.5 text-[15px] text-ink-100 tabular-nums truncate">{children}</div>
    {bar && (
      <div className="mt-1.5 h-1 rounded-full bg-ink-800 overflow-hidden">
        <div className={`h-full ${bar.tone}`} style={{ width: `${Math.max(0, Math.min(100, bar.pct))}%` }} />
      </div>
    )}
  </div>
);

export const CockpitBlockView: React.FC<IssueBlockProps> = ({ ctx }) => {
  const { t } = useTranslation();
  const rollup = ctx.rollup;
  const [cancelling, setCancelling] = useState(false);
  const [pausing, setPausing] = useState(false);
  const [resuming, setResuming] = useState(false);
  const [controlError, setControlError] = useState<string | null>(null);
  // Replay (harness 2b-1 §1): viewing the live run as of a past step — the
  // four cells read the folded view of that moment and controls are off
  // (you cannot pause the past).
  const replay = useReplay();
  const frozen = !!rollup && isReplaying(replay, rollup.current_run?.id);
  if (!rollup) return null;

  const phase = rollup.phase;
  const liveView = selectRunView(rollup.current_run ? { view: rollup.current_run.view } : null);
  const view = frozen ? replay?.view ?? null : liveView;
  const asOf = frozen ? replay?.view?.current ?? null : null;
  // Spend as of that step comes from the frozen run cost; the cap stays the
  // issue's. Without a frozen cost the cell reads "—", never the live number.
  const frozenSpent = frozen ? replay?.cost?.spent_cents ?? null : null;
  // Phase 2b-1 §2: a forked run says where it branched from; clicking scrubs
  // the ORIGINAL run (same timeline) to that seq.
  const forkedFrom = liveView?.fork ?? null;
  // The origin row (or the detached panel) scrolls itself into view once it
  // is attached; nothing here depends on the DOM.
  const showFork = (of: number, seq: number) => replay?.seekRun(String(of), seq);
  // Replaying some OTHER run of this issue (fork origin): the panel is not
  // frozen, but the way back to Live must still be one click away.
  const replayingOther = !!replay && replay.seq != null && !frozen;
  const step = stepProgress(view);
  const gauge = contextGauge(view);
  const cur = currentStep(view);
  const retry = retryState(view, Date.now());
  const runBudget = budgetState(view);
  // Phase 2b-1 §3: a fifth cell only when a tool actually timed out.
  const tools = toolsState(view);
  const budget = rollup.budget;
  // Phase 2a: the parked typed question from the live run view — and ONLY
  // while the issue marker is absent. A parked issue (marker present) draws
  // its card in NeedsInputCard below; a second, always-enabled copy here
  // would answer twice (the second POST is a 409).
  // Always the LIVE view: a question folded at some past step is not open
  // now, and the one open now must stay answerable while scrubbing.
  const question =
    phase === 'waiting_input' &&
    ctx.env.onAnswerQuestion &&
    !questionFromMarker(rollup.execution_state)
      ? questionFromRunView(liveView)
      : null;
  const startedMs = rollup.current_run?.started_at ? Date.parse(rollup.current_run.started_at) : NaN;
  const elapsed = Number.isFinite(startedMs) ? Math.max(0, Math.floor((Date.now() - startedMs) / 1000)) : null;

  // Target-level pause / resume (phase 2a §2). Both re-read the issue +
  // rollup afterwards; a failure is shown on the cockpit (typed path, never a
  // silent no-op).
  const issueId = Number(ctx.issue.id);
  const pause = async () => {
    if (pausing) return;
    setPausing(true);
    setControlError(null);
    try {
      await pauseIssue(issueId);
      ctx.env.onIssueChanged?.();
    } catch (err) {
      console.error('[CockpitBlock] pause failed', err);
      setControlError(controlErrorText(err, t));
    } finally {
      setPausing(false);
    }
  };
  const resume = async () => {
    if (resuming) return;
    setResuming(true);
    setControlError(null);
    try {
      await resumeIssue(issueId);
      ctx.env.onIssueChanged?.();
    } catch (err) {
      console.error('[CockpitBlock] resume failed', err);
      setControlError(controlErrorText(err, t));
    } finally {
      setResuming(false);
    }
  };

  const cancel = async () => {
    if (!rollup.current_run || cancelling) return;
    setCancelling(true);
    try {
      await aiLibraryService.cancelRun(rollup.current_run.id);
      ctx.env.onIssueChanged?.();
    } catch (err) {
      console.error('[CockpitBlock] cancel failed', err);
    } finally {
      setCancelling(false);
    }
  };

  const budgetTone = budget.state === 'over' ? 'bg-danger' : budget.state === 'warn' || runBudget?.state === 'warn' ? 'bg-warn' : 'bg-ok';

  return (
    <section className="mt-5 rounded-lg border border-ink-800/80 bg-ink-950/60 p-3 space-y-3" data-testid="issue-cockpit">
      <div className="flex items-center gap-3 min-w-0">
        <span className={`inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5 text-[12px] font-medium ${PHASE_TONE[phase] ?? PHASE_TONE.idle}`} data-testid="cockpit-phase">
          {phase === 'running' && <span className="h-1.5 w-1.5 rounded-full bg-ok animate-pulse" />}
          {t(`issueDetail.phase.${phase}`, phase.replace(/_/g, ' '))}
        </span>
        {forkedFrom && (
          <button
            type="button"
            data-testid="cockpit-fork-chip"
            onClick={() => showFork(forkedFrom.of_run_id, forkedFrom.at_seq)}
            className="inline-flex items-center gap-1 rounded-full border border-info-line bg-info-soft px-2 py-0.5 text-[11px] text-info hover:brightness-110"
            title={t('fork.chipSeq', 'Forked from run #{{run}} @ seq {{seq}}', { run: String(forkedFrom.of_run_id).slice(-6), seq: forkedFrom.at_seq })}
          >
            <GitFork size={11} />
            {t('fork.chipSeq', 'Forked from run #{{run}} @ seq {{seq}}', { run: String(forkedFrom.of_run_id).slice(-6), seq: forkedFrom.at_seq })}
          </button>
        )}
        {step?.label && (
          <span className="text-[13px] text-ink-300 truncate">
            <span className="text-ink-500">{t('issueDetail.now', 'Now')}: </span>
            <span className="text-ink-100">{step.label}</span>
          </span>
        )}
        {(phase === 'running' || phase === 'paused') && (
          <span className="ml-auto inline-flex items-center gap-1.5">
            {phase === 'running' && (
              <button
                type="button"
                onClick={() => void pause()}
                disabled={pausing || frozen}
                data-testid="cockpit-pause"
                className="inline-flex items-center gap-1 rounded border border-ink-700 px-2 py-0.5 text-[12px] text-ink-300 hover:border-info-line hover:text-info disabled:opacity-50"
              >
                <Pause size={11} /> {pausing ? t('issueDetail.pausing', 'Pausing…') : t('issueDetail.pause', 'Pause')}
              </button>
            )}
            {phase === 'paused' && (
              <button
                type="button"
                onClick={() => void resume()}
                disabled={resuming || frozen}
                data-testid="cockpit-resume"
                className="inline-flex items-center gap-1 rounded border border-info-line bg-info-soft px-2 py-0.5 text-[12px] text-info hover:brightness-110 disabled:opacity-50"
              >
                <Play size={11} /> {resuming ? t('issueDetail.resuming', 'Resuming…') : t('issueDetail.resume', 'Resume')}
              </button>
            )}
            {rollup.current_run && (
              <button
                type="button"
                onClick={() => void cancel()}
                disabled={cancelling || frozen}
                data-testid="cockpit-cancel"
                className="inline-flex items-center gap-1 rounded border border-ink-700 px-2 py-0.5 text-[12px] text-ink-300 hover:border-danger-line hover:text-danger disabled:opacity-50"
              >
                <Square size={11} /> {cancelling ? t('issueDetail.cancelling', 'Cancelling…') : t('common.cancel', 'Cancel')}
              </button>
            )}
          </span>
        )}
      </div>
      {controlError && (
        <p data-testid="cockpit-control-error" className="text-[12px] text-danger break-words">
          {controlError}
        </p>
      )}

      {question && ctx.env.onAnswerQuestion && (
        <div data-testid="cockpit-question">
          <QuestionCard question={question} onAnswer={ctx.env.onAnswerQuestion} />
        </div>
      )}

      {replay && (frozen || replayingOther) && (
        <div className="flex items-center justify-end">
          <button
            type="button"
            data-testid="cockpit-asof"
            data-mode={frozen ? 'frozen' : 'other-run'}
            onClick={() => replay.seek(null)}
            title={t('replay.backToLive', 'Back to live')}
            className="rounded border border-info-line bg-info-soft px-1.5 py-0.5 text-[11px] text-info hover:brightness-110"
          >
            {frozen
              ? t('replay.asOf', 'as of turn {{turn}} · step {{step}}', { turn: asOf?.turn ?? '?', step: asOf?.step ?? '?' })
              : t('replay.viewingRun', 'Replaying run #{{run}}', { run: (replay.runId ?? '').slice(-6) })}
            {' · '}
            {t('replay.live', 'Live')}
          </button>
        </div>
      )}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
        <Cell label={t('issueDetail.steps', 'Steps')} testId="cockpit-steps" bar={step ? { pct: (step.done / Math.max(1, step.total)) * 100, tone: 'bg-agent' } : undefined}>
          {step ? (
            <>
              {step.done}
              <span className="text-ink-500 text-[12px]">/{step.total}</span>
            </>
          ) : (
            <span className="text-ink-600 text-[12px]">—</span>
          )}
        </Cell>
        <Cell label={t('issueDetail.context', 'Context')} testId="cockpit-context" bar={gauge ? { pct: gauge.used_pct, tone: gauge.used_pct >= 85 ? 'bg-warn' : 'bg-info' } : undefined}>
          {gauge ? (
            <>
              {Math.round(gauge.used_pct)}
              <span className="text-ink-500 text-[12px]">%</span>
            </>
          ) : (
            <span className="text-ink-600 text-[12px]">—</span>
          )}
        </Cell>
        <Cell
          label={t('issueDetail.budget', 'Budget')}
          testId="cockpit-budget"
          bar={
            budget.budget_cents != null
              ? { pct: frozen ? (frozenSpent != null ? (frozenSpent / Math.max(1, budget.budget_cents)) * 100 : 0) : budget.pct ?? 0, tone: budgetTone }
              : undefined
          }
        >
          {formatCents(frozen ? frozenSpent : budget.spent_cents)}
          <span className="text-ink-500 text-[12px]"> / {budget.budget_cents != null ? formatCents(budget.budget_cents) : '∞'}</span>
        </Cell>
        <Cell label={t('issueDetail.runs', 'Runs')} testId="cockpit-runs">
          {rollup.runs.length}
          {cur?.step != null && (
            <span className="text-ink-500 text-[12px]">
              {' '}
              · turn {cur.turn ?? 1} · step {cur.step}
            </span>
          )}
        </Cell>
        {tools && tools.timed_out > 0 && (
          <Cell label={t('issueDetail.tools', 'Tools')} testId="cockpit-tools">
            <span className="text-danger">{t('issueDetail.toolsTimedOut', '{{count}} timed out', { count: tools.timed_out })}</span>
            {tools.last_timed_out && <div className="text-[11px] text-ink-500 truncate">{tools.last_timed_out}</div>}
          </Cell>
        )}
      </div>

      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] text-ink-500" data-testid="cockpit-subline">
        {retry && (
          <span className="text-warn">
            {retry.waitingSeconds > 0
              ? t('issueDetail.retryWaiting', { attempt: retry.attempt, max: retry.max, s: retry.waitingSeconds.toFixed(1) })
              : t('issueDetail.retried', { attempt: retry.attempt, max: retry.max })}
          </span>
        )}
        {rollup.sub_issues.total > 0 && (
          <span>{t('issueDetail.subIssuesDone', { done: rollup.sub_issues.done, total: rollup.sub_issues.total })}</span>
        )}
        {rollup.inbox_pending > 0 && <span className="text-ok">{t('issueDetail.inboxPending', { count: rollup.inbox_pending })}</span>}
        {elapsed != null && <span className="tabular-nums">{formatElapsed(elapsed)}</span>}
      </div>
    </section>
  );
};

export const cockpitBlock: IssueBlock = {
  id: 'cockpit',
  zone: 'cockpit',
  order: 10,
  match: (ctx) => ctx.rollup !== null,
  component: CockpitBlockView,
};
