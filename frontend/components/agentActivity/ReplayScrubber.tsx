/**
 * Replay scrubber (harness 2b-1 §1): one tick per step boundary of a run.
 * Clicking / ←→ seeks the trajectory and the cockpit to "as of that step";
 * Live returns to the present. Read-only — a fork is the one action it can
 * offer (Task 6), and only when the caller passes `onFork`.
 */
import React, { useCallback } from 'react';
import { useTranslation } from 'react-i18next';
import { GitFork, Radio } from 'lucide-react';
import type { Tick } from './replayTicks';

export interface ReplayScrubberProps {
  ticks: Tick[];
  /** Current position; null = Live. */
  seq: number | null;
  isRunning: boolean;
  onSeek: (seq: number | null) => void;
  onFork?: (seq: number, label: string) => void;
  loading?: boolean;
}

export const ReplayScrubber: React.FC<ReplayScrubberProps> = ({ ticks, seq, isRunning, onSeek, onFork, loading }) => {
  const { t } = useTranslation();
  const steps = ticks.filter((k) => k.kind === 'step');
  const idx = seq == null ? -1 : ticks.findIndex((k) => k.seq === seq);
  const stepIdx = seq == null ? -1 : steps.findIndex((k) => k.seq === seq);
  const current = idx >= 0 ? ticks[idx] : null;

  const onKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      if (ticks.length === 0) return;
      if (e.key === 'ArrowLeft') {
        e.preventDefault();
        const next = idx === -1 ? ticks.length - 1 : Math.max(0, idx - 1);
        onSeek(ticks[next].seq);
      } else if (e.key === 'ArrowRight') {
        e.preventDefault();
        if (idx === -1) return;
        if (idx >= ticks.length - 1) onSeek(null);
        else onSeek(ticks[idx + 1].seq);
      } else if (e.key === 'Home') {
        e.preventDefault();
        onSeek(ticks[0].seq);
      } else if (e.key === 'End') {
        e.preventDefault();
        onSeek(null);
      }
    },
    [ticks, idx, onSeek],
  );

  if (ticks.length === 0) return null;
  const live = seq == null;
  const position = live
    ? t('replay.live', 'Live')
    : current?.kind === 'turn_end'
      ? t('replay.turnEnd', 'turn end')
      : t('replay.stepOf', 'turn {{turn}} · step {{step}} ({{n}}/{{total}})', {
          turn: current?.turn ?? '?',
          step: current?.step ?? '?',
          n: stepIdx + 1,
          total: steps.length,
        });

  return (
    <div
      data-testid="replay-scrubber"
      role="group"
      aria-label={`${t('replay.scrubber', 'Replay')}: ${position}`}
      data-seq={seq ?? ''}
      tabIndex={0}
      onKeyDown={onKeyDown}
      className={`flex items-center gap-2 rounded-md border px-2 py-1 text-[12px] ${live ? 'border-ink-800 bg-ink-900/40 text-ink-400' : 'border-info-line bg-info-soft text-info'}`}
    >
      <div className="flex items-center gap-1 min-w-0 overflow-x-auto" data-testid="replay-ticks">
        {ticks.map((k) => {
          const isCur = k.seq === seq;
          return (
            <button
              key={k.seq}
              type="button"
              data-testid="replay-tick"
              data-kind={k.kind}
              data-seq={k.seq}
              data-current={isCur ? 'true' : 'false'}
              title={k.kind === 'turn_end' ? t('replay.turnEnd', 'turn end') : `${k.turn ?? '?'}.${k.step ?? '?'}`}
              onClick={() => onSeek(k.seq)}
              className={`h-3 rounded-sm border ${k.kind === 'turn_end' ? 'w-3 rounded-full' : 'w-2'} ${
                isCur ? 'bg-info border-info' : 'bg-ink-800 border-ink-700 hover:border-info-line'
              }`}
            />
          );
        })}
      </div>
      <span data-testid="replay-position" className="tabular-nums whitespace-nowrap">
        {loading ? t('replay.loading', 'Loading…') : position}
      </span>
      {!live && (
        <span className="inline-flex items-center gap-1 ml-auto">
          {onFork && current?.kind === 'step' && (
            <button
              type="button"
              data-testid="replay-fork"
              onClick={() => onFork(seq as number, position)}
              className="inline-flex items-center gap-1 rounded border border-info-line px-1.5 py-0.5 hover:brightness-110"
            >
              <GitFork size={11} /> {t('replay.fork', 'Fork from step {{n}}', { n: stepIdx + 1 })}
            </button>
          )}
          <button
            type="button"
            data-testid="replay-live"
            data-on="false"
            onClick={() => onSeek(null)}
            className="inline-flex items-center gap-1 rounded border border-ink-700 px-1.5 py-0.5 text-ink-300 hover:border-info-line hover:text-info"
          >
            <Radio size={11} /> {t('replay.live', 'Live')}
          </button>
        </span>
      )}
      {live && (
        <span data-testid="replay-live" data-on="true" className="ml-auto inline-flex items-center gap-1 text-ink-500">
          {isRunning && <span className="h-1.5 w-1.5 rounded-full bg-agent animate-pulse" />}
          <Radio size={11} /> {t('replay.live', 'Live')}
        </span>
      )}
    </div>
  );
};
