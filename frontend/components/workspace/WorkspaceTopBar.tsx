/**
 * WorkspaceTopBar — the project-condition strip that replaces the old
 * StageWorkbench card once the workspace shell is on (spec frame: 顶部项目
 * 条). Project identity on the left; stage progress + one-click suggestion +
 * Advance on the right, all on one row so they stay visible across every
 * module (not just the stage-tool grid the legacy strip only showed on
 * "Files").
 */

import { useTranslation } from 'react-i18next';
import { ArrowLeft, ArrowRight, Loader2 } from 'lucide-react';
import { MiniStepper } from './MiniStepper';
import type { ProjectStage, ProjectTab, StageSuggestion } from '../../types';

interface WorkspaceTopBarProps {
  projectName: string;
  onBack: () => void;
  catalog: ProjectStage[];
  currentStage: ProjectStage | null;
  currentIndex: number;
  canWrite?: boolean;
  advancing: boolean;
  onJumpStage: (stage: ProjectStage) => void;
  nextStage: ProjectStage | null;
  onAdvance: () => void;
  suggestion: StageSuggestion | null;
  suggestionBusy: boolean;
  onSuggestionGenerate: () => void;
  onSuggestionNavigate: (tab: ProjectTab) => void;
}

export function WorkspaceTopBar({
  projectName,
  onBack,
  catalog,
  currentStage,
  currentIndex,
  canWrite = true,
  advancing,
  onJumpStage,
  nextStage,
  onAdvance,
  suggestion,
  suggestionBusy,
  onSuggestionGenerate,
  onSuggestionNavigate,
}: WorkspaceTopBarProps) {
  const { t } = useTranslation();
  const action = suggestion?.action ?? null;
  const isGenerate = action?.type === 'generate_missing_frames';

  return (
    <div
      data-testid="workspace-topbar"
      className="flex items-center gap-3 px-4 py-2 border-b border-ink-800/60 flex-wrap"
    >
      <button
        data-testid="workspace-back-btn"
        onClick={onBack}
        title={t('projects.nav.backToList')}
        className="p-1 rounded hover:bg-ink-700 text-ink-400 hover:text-ink-200 transition-colors shrink-0"
      >
        <ArrowLeft size={15} />
      </button>
      <span className="w-6 h-6 rounded-md bg-indigo-500 text-ink-950 grid place-items-center text-[10px] font-bold shrink-0">
        {(projectName[0] || '?').toUpperCase()}
      </span>
      <span className="text-[13px] font-semibold text-ink-100 truncate">{projectName}</span>

      <div className="flex-1" />

      {catalog.length > 0 && currentStage && (
        <>
          <MiniStepper
            catalog={catalog}
            currentIndex={currentIndex}
            canWrite={canWrite}
            advancing={advancing}
            onJump={onJumpStage}
          />
          <span
            data-testid="workspace-stage-chip"
            className="text-[11px] text-indigo-300 bg-indigo-500/10 rounded-full px-2.5 py-1 font-medium whitespace-nowrap"
          >
            {t(`projects.stages.${currentStage.slug}`, currentStage.name)} · {currentIndex + 1}/{catalog.length}
          </span>
        </>
      )}

      {action && (
        <button
          data-testid="workspace-suggestion-cta"
          disabled={suggestionBusy}
          onClick={() =>
            isGenerate ? onSuggestionGenerate() : onSuggestionNavigate((action.tab ?? 'files') as ProjectTab)
          }
          className={`flex items-center gap-1.5 shrink-0 rounded-lg font-medium text-[12.5px] px-3 py-1.5 transition-colors ${
            isGenerate
              ? 'bg-indigo-500 hover:bg-indigo-400 disabled:opacity-50 text-ink-950'
              : 'border border-ink-700 hover:border-ink-500 text-ink-300'
          }`}
        >
          {suggestionBusy ? <Loader2 size={13} className="animate-spin" /> : null}
          {t(action.label_key, { count: action.count })}
        </button>
      )}

      {canWrite && nextStage && (
        <button
          data-testid="workspace-advance-btn"
          onClick={onAdvance}
          disabled={advancing}
          className="flex items-center gap-1.5 shrink-0 rounded-lg border border-ink-700 hover:border-ink-500 disabled:opacity-50 text-ink-300 font-medium text-[12.5px] px-3 py-1.5 transition-colors"
        >
          {t('projects.workbench.advanceTo', {
            stage: t(`projects.stages.${nextStage.slug}`, nextStage.name),
          })}
          <ArrowRight size={13} />
        </button>
      )}
    </div>
  );
}

export default WorkspaceTopBar;
