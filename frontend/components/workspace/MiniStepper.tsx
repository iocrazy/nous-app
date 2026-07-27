/**
 * MiniStepper — the compact dot-per-stage progress indicator (Phase B B2,
 * design D2). Originally extracted from the legacy StageWorkbench (retired in
 * PR-18); the workspace top bar (WorkspaceTopBar) is now its only consumer.
 *
 * Pure rendering + a single "jump to this stage" callback; the caller owns
 * the busy guard and the actual advance/back request.
 */

import { useTranslation } from 'react-i18next';
import type { ProjectStage } from '../../types';

export interface MiniStepperProps {
  catalog: ProjectStage[];
  /** Index of the current stage within `catalog`, or -1 when unknown. */
  currentIndex: number;
  /** Whether the viewer may jump to another stage (write access). */
  canWrite?: boolean;
  /** Disables all dots while a jump request is in flight. */
  advancing?: boolean;
  onJump: (stage: ProjectStage) => void;
}

export function MiniStepper({
  catalog,
  currentIndex,
  canWrite = true,
  advancing = false,
  onJump,
}: MiniStepperProps) {
  const { t } = useTranslation();

  return (
    <div
      data-testid="stage-ministep"
      role="group"
      aria-label={t('projects.workbench.stageProgress', 'Stage progress')}
      className="flex items-center"
    >
      {catalog.map((stage, i) => (
        <div key={stage.id} className="flex items-center">
          {i > 0 && (
            <span
              style={{ width: 14 }}
              className={`h-[2px] shrink-0 ${
                i - 1 < currentIndex ? 'bg-indigo-500' : 'bg-line-strong'
              }`}
            />
          )}
          <button
            type="button"
            data-testid={`ministep-dot-${stage.slug}`}
            title={t(`projects.stages.${stage.slug}`, stage.name)}
            aria-label={t(`projects.stages.${stage.slug}`, stage.name)}
            disabled={!canWrite || i === currentIndex || advancing}
            onClick={() => onJump(stage)}
            className={`w-2 h-2 rounded-full transition-colors shrink-0 disabled:cursor-default ${
              i === currentIndex
                ? 'bg-indigo-500 ring-[3px] ring-indigo-500/25'
                : i < currentIndex
                  ? 'bg-indigo-500'
                  : 'bg-line-strong'
            }`}
          />
        </div>
      ))}
    </div>
  );
}

export default MiniStepper;
