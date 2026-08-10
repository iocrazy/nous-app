/**
 * "This turn wrote N shot cards" — the per-turn write summary (spec §5.4),
 * with each card rendered as 镜号 / 一句话 / 焦段 and clickable to reveal it
 * on the storyboard.
 *
 * ── The Undo button ────────────────────────────────────────────────────────
 * Shot writes now carry run attribution and a run-scoped ledger (see the
 * three run-undo commits this summary's header used to link to as future
 * work), so the affordance this component originally shipped without can
 * ship for real: `useRunUndo` resolves the run's undone state and the button
 * below fires `POST /runs/{id}/undo`. It only appears when the caller passes
 * a `runId` AND `interactive` is true — same "no dead affordance" reasoning
 * as the shot rows below: a surface with no editor mounted (the issue
 * timeline) gets plain text, not a button that can't act.
 */

import React, { useCallback } from 'react';
import { useTranslation } from 'react-i18next';
import { Aperture, PencilLine, Undo2 } from 'lucide-react';

import type { ShotCardSummary, TurnWriteSummary as WriteSummary } from './toolActivity';
import { requestShotFocus } from './shotFocusBus';
import { useRunUndo } from './useRunUndo';

export interface TurnWriteSummaryProps {
  summary: WriteSummary;
  /** Override the jump behaviour (tests, or a surface with no editor). */
  onShotClick?: (shotId: string) => void;
  /**
   * Whether clicking a card can actually reveal it. False on surfaces with no
   * editor mounted (the issue timeline), where the rows render as plain text —
   * same reasoning as the gated Undo button below: no affordance beats a
   * dead one.
   */
  interactive?: boolean;
  /** The agent_runs id that wrote this turn's cards. Undo needs it to call
   *  POST /runs/{id}/undo; omit it (e.g. no run backed this summary) and the
   *  button never renders. */
  runId?: string | null;
  className?: string;
}

function ShotFields({ shot }: { shot: ShotCardSummary }): React.ReactElement {
  const { t } = useTranslation();
  return (
    <>
      <span className="shrink-0 font-mono font-medium text-agent">
        {shot.shotLabel}
      </span>
      <span className="min-w-0 flex-1 truncate text-ink-300">
        {shot.description ?? t('agentActivity.shotNoDescription', 'No description')}
      </span>
      {shot.focalLength && (
        <span className="inline-flex shrink-0 items-center gap-1 text-ink-500">
          <Aperture size={10} aria-hidden />
          {shot.focalLength}
        </span>
      )}
    </>
  );
}

const ROW_CLASS = 'flex w-full items-baseline gap-2 rounded-md px-2 py-1 text-left text-[11px]';

function ShotRow({
  shot,
  onClick,
  interactive,
}: {
  shot: ShotCardSummary;
  onClick: (shotId: string) => void;
  interactive: boolean;
}): React.ReactElement {
  const { t } = useTranslation();
  const handle = useCallback(() => onClick(shot.shotId), [onClick, shot.shotId]);

  if (!interactive) {
    return (
      <div data-testid="turn-write-shot" data-shot-id={shot.shotId} className={ROW_CLASS}>
        <ShotFields shot={shot} />
      </div>
    );
  }
  return (
    <button
      type="button"
      onClick={handle}
      data-testid="turn-write-shot"
      data-shot-id={shot.shotId}
      title={t('agentActivity.revealShot', 'Show this shot on the storyboard')}
      className={`${ROW_CLASS} transition-colors hover:bg-agent-soft`}
    >
      <ShotFields shot={shot} />
    </button>
  );
}

export function TurnWriteSummary({
  summary,
  onShotClick,
  interactive = true,
  runId,
  className,
}: TurnWriteSummaryProps): React.ReactElement | null {
  const { t } = useTranslation();
  const handleShotClick = useCallback(
    (shotId: string) => {
      if (onShotClick) onShotClick(shotId);
      else requestShotFocus(shotId);
    },
    [onShotClick],
  );

  // Called unconditionally (Rules of Hooks) even though the component can
  // still bail to null below — `enabled` folds runId/interactive so the hook
  // itself resolves straight to 'hidden' rather than us skipping the call.
  const { state: undoState, report: undoReport, undo } = useRunUndo(
    runId,
    interactive && Boolean(runId),
  );

  const shotCount = summary.shots.length;
  if (shotCount === 0 && summary.otherWriteCount === 0) return null;

  return (
    <div
      className={`rounded-lg border border-agent-line bg-agent-soft px-2 py-1.5 ${className ?? ''}`}
      data-testid="turn-write-summary"
      data-shot-count={shotCount}
    >
      <div className="mb-1 flex items-center justify-between gap-1.5 px-1">
        <div className="flex items-center gap-1.5 text-[11px] font-medium text-agent">
          <PencilLine size={11} aria-hidden />
          {shotCount > 0
            ? t('agentActivity.wroteShots', { count: shotCount })
            : t('agentActivity.wroteEdits', { count: summary.otherWriteCount })}
        </div>
        {undoState === 'ready' || undoState === 'busy' ? (
          <button
            type="button"
            onClick={() => void undo()}
            disabled={undoState === 'busy'}
            data-testid="turn-undo-button"
            className="inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-[10px] text-ink-500 transition-colors hover:bg-agent-soft hover:text-agent disabled:opacity-50"
          >
            <Undo2 size={10} aria-hidden />
            {t('agentActivity.undo', 'Undo')}
          </button>
        ) : undoState === 'undone' ? (
          <span
            data-testid="turn-undo-button"
            aria-disabled="true"
            className="inline-flex items-center gap-1 px-1.5 py-0.5 text-[10px] text-ink-500 opacity-60"
          >
            <Undo2 size={10} aria-hidden />
            {t('agentActivity.undone', 'Undone')}
          </span>
        ) : null}
      </div>
      {summary.shots.map((shot) => (
        <ShotRow
          key={shot.shotId}
          shot={shot}
          onClick={handleShotClick}
          interactive={interactive}
        />
      ))}
      {shotCount > 0 && summary.otherWriteCount > 0 && (
        <p className="px-2 pt-0.5 text-[10px] text-ink-500">
          {t('agentActivity.alsoEdits', { count: summary.otherWriteCount })}
        </p>
      )}
      {undoReport && (
        <div
          data-testid="turn-undo-report"
          className="mt-1 border-t border-agent-line px-2 pt-1 text-[10px] text-ink-500"
        >
          <p>
            {t('agentActivity.undoSummary', {
              deleted: undoReport.shots_deleted,
              reverted: undoReport.shots_reverted,
              elements: undoReport.scene_elements_reverted,
            })}
          </p>
          {undoReport.skipped.map((item, i) => (
            <p key={`${item.kind}-${item.id}-${i}`}>
              {t(`agentActivity.undoKind.${item.kind}`)} {item.id} ·{' '}
              {t(`agentActivity.undoReason.${item.reason}`)}
            </p>
          ))}
        </div>
      )}
    </div>
  );
}
