/**
 * "This turn wrote N shot cards" — the per-turn write summary (spec §5.4),
 * with each card rendered as 镜号 / 一句话 / 焦段 and clickable to reveal it
 * on the storyboard.
 *
 * ── On the missing Undo button ────────────────────────────────────────────
 * The spec pairs this summary with an undo affordance. It is NOT shipped
 * here, on purpose: a real undo is not implementable against today's schema.
 * ``scoped_script_gateway.create_shot`` / ``update_shot`` INSERT and UPDATE
 * ``script_shots`` directly without writing to the ``script_ops`` ledger, and
 * ``script_shots`` has no attribution column at all — an agent-written card is
 * byte-for-byte indistinguishable from one a human or Auto-Storyboard wrote.
 * The only undo we could build today would be "delete the last N shots in the
 * scene", which would happily delete a collaborator's work.
 *
 * A button that sometimes destroys the wrong card is worse than no button, so
 * the summary ships read-only until the shot writes are given attribution and
 * a run-scoped inverse (see the A7 report for the four-item backend list).
 */

import React, { useCallback } from 'react';
import { useTranslation } from 'react-i18next';
import { Aperture, PencilLine } from 'lucide-react';

import type { ShotCardSummary, TurnWriteSummary as WriteSummary } from './toolActivity';
import { requestShotFocus } from './shotFocusBus';

export interface TurnWriteSummaryProps {
  summary: WriteSummary;
  /** Override the jump behaviour (tests, or a surface with no editor). */
  onShotClick?: (shotId: string) => void;
  /**
   * Whether clicking a card can actually reveal it. False on surfaces with no
   * editor mounted (the issue timeline), where the rows render as plain text —
   * same reasoning as the absent Undo button: no affordance beats a dead one.
   */
  interactive?: boolean;
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

  const shotCount = summary.shots.length;
  if (shotCount === 0 && summary.otherWriteCount === 0) return null;

  return (
    <div
      className={`rounded-lg border border-agent-line bg-agent-soft px-2 py-1.5 ${className ?? ''}`}
      data-testid="turn-write-summary"
      data-shot-count={shotCount}
    >
      <div className="mb-1 flex items-center gap-1.5 px-1 text-[11px] font-medium text-agent">
        <PencilLine size={11} aria-hidden />
        {shotCount > 0
          ? t('agentActivity.wroteShots', { count: shotCount })
          : t('agentActivity.wroteEdits', { count: summary.otherWriteCount })}
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
    </div>
  );
}
