/**
 * StageWorkbench — stage-driven workbench header for the project detail page
 * (Phase B B2, design A). Replaces the two-row StageSelector + StageToolGrid
 * strip with a workbench centered on the current SOP stage: a large stage
 * card (name + guidance + one-click advance) over a row of recommended-tool
 * cards derived from ``current_stage.tools_recommended``.
 *
 * Flag-gated by VITE_FEATURE_PROJECT_WORKBENCH — the caller renders the
 * legacy strip when the flag is off. The horizontal StageSelector is kept
 * above the card so any-stage jumps stay available; the Advance button is
 * the guided happy-path to the next stage.
 */

import { useCallback, useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { ArrowRight, CheckCircle2 } from 'lucide-react';
import { fetchStageCatalog, fetchStageSuggestion, setCurrentStage } from '../../services/projectsService';
import { TOOL_CATALOG } from '../../features/projects/stageTools';
import { StageSelector } from './StageSelector';
import { StageSuggestion } from './StageSuggestion';
import { StageHistoryDrawer } from './StageHistoryDrawer';
import type { ProjectStage, ProjectTab, StoryboardProgress } from '../../types';

// Phase B B3 — stage-aware "next step" suggestion card. Independent flag so
// it stays dark while B2's workbench is already live.
const AI_SUGGEST_ENABLED =
  import.meta.env.VITE_FEATURE_PROJECT_AI_SUGGEST === 'true';

interface StageWorkbenchProps {
  projectId: string;
  canWrite?: boolean;
  currentStage: ProjectStage | null;
  onStageChange: (stage: ProjectStage) => void;
  setActiveTab: (tab: ProjectTab) => void;
}

export function StageWorkbench({
  projectId,
  canWrite = true,
  currentStage,
  onStageChange,
  setActiveTab,
}: StageWorkbenchProps) {
  const { t } = useTranslation();
  const [catalog, setCatalog] = useState<ProjectStage[]>([]);
  const [advancing, setAdvancing] = useState(false);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [storyboardProgress, setStoryboardProgress] = useState<StoryboardProgress | null>(null);

  useEffect(() => {
    let cancelled = false;
    fetchStageCatalog()
      .then((stages) => {
        if (!cancelled) setCatalog(stages);
      })
      .catch((err) => console.error('[StageWorkbench] failed to load catalog:', err));
    return () => {
      cancelled = true;
    };
  }, []);

  // Light data subtitle for the storyboard toolcard ("N/M frames"), matching
  // the A-mockup toolcards carrying data. Only fetched when the current
  // stage is storyboard — every other toolcard stays label-only.
  // NOTE: duplicate of StageSuggestion's fetch — acceptable, endpoint is
  // cheap+best-effort; consolidate if a third consumer appears.
  useEffect(() => {
    if (currentStage?.slug !== 'storyboard') {
      setStoryboardProgress(null);
      return;
    }
    let cancelled = false;
    fetchStageSuggestion(projectId)
      .then((s) => {
        if (!cancelled) setStoryboardProgress(s.progress ?? null);
      })
      .catch((err) => {
        console.error('[StageWorkbench] failed to load storyboard progress:', err);
        if (!cancelled) setStoryboardProgress(null);
      });
    return () => {
      cancelled = true;
    };
  }, [projectId, currentStage?.slug]);

  const currentIndex = catalog.findIndex((s) => s.id === currentStage?.id);
  const total = catalog.length;
  const nextStage =
    currentIndex >= 0 && currentIndex < total - 1 ? catalog[currentIndex + 1] : null;

  const handleAdvance = useCallback(async () => {
    if (!nextStage || advancing) return;
    setAdvancing(true);
    try {
      const updated = await setCurrentStage(projectId, nextStage.id);
      if (updated) onStageChange(updated);
    } catch (err) {
      console.error('[StageWorkbench] failed to advance stage:', err);
    } finally {
      setAdvancing(false);
    }
  }, [nextStage, advancing, projectId, onStageChange]);

  // No stage catalog / no current stage → render nothing (project predates
  // the SOP machine); the detail page still shows its content tabs.
  if (total === 0 || !currentStage) return null;

  const tools = (currentStage.tools_recommended ?? [])
    .map((slug) => TOOL_CATALOG[slug])
    .filter(Boolean);
  const descKey = `projects.stages.desc.${currentStage.slug}`;
  const description = t(descKey);

  return (
    <div className="flex flex-col gap-3 px-8 pt-3 pb-3 border-b border-ink-800">
      <StageSelector
        projectId={projectId}
        canWrite={canWrite}
        currentStage={currentStage}
        onStageChange={onStageChange}
      />

      <div className="rounded-xl border border-ink-800 bg-ink-900/40 p-4">
        <div className="flex items-start justify-between gap-4 flex-wrap">
          <div className="min-w-0">
            <div className="text-[11px] uppercase tracking-wider text-indigo-400 font-medium">
              {t('projects.workbench.currentStageOf', {
                index: currentIndex + 1,
                total,
              })}
            </div>
            <h2 className="text-lg font-semibold text-ink-100 mt-0.5">
              {t(`projects.stages.${currentStage.slug}`, currentStage.name)}
            </h2>
            {description !== descKey && (
              <p className="text-sm text-ink-400 mt-1 max-w-prose">{description}</p>
            )}
          </div>
          <div className="flex items-center gap-2 shrink-0">
            <button
              data-testid="stage-history-btn"
              onClick={() => setHistoryOpen(true)}
              className="rounded-lg border border-ink-700 hover:border-ink-500 text-ink-300 font-medium text-sm px-3 py-2 transition-colors"
            >
              {t('projects.workbench.stageHistory')}
            </button>
            {canWrite &&
              (nextStage ? (
                <button
                  onClick={handleAdvance}
                  disabled={advancing}
                  className="flex items-center gap-1.5 shrink-0 rounded-lg bg-indigo-500 hover:bg-indigo-400
                             disabled:opacity-50 text-ink-950 font-semibold text-sm px-4 py-2 transition-colors"
                >
                  {t('projects.workbench.advanceTo', {
                    stage: t(`projects.stages.${nextStage.slug}`, nextStage.name),
                  })}
                  <ArrowRight size={15} />
                </button>
              ) : (
                <span className="flex items-center gap-1.5 shrink-0 text-sm text-emerald-400 font-medium">
                  <CheckCircle2 size={15} />
                  {t('projects.workbench.finalStage')}
                </span>
              ))}
          </div>
        </div>

        {tools.length > 0 && (
          <div className="mt-4">
            <div className="text-[11px] uppercase tracking-wider text-ink-500 mb-2">
              {t('projects.workbench.recommendedTools')}
            </div>
            <div className="grid grid-cols-[repeat(auto-fill,minmax(160px,1fr))] gap-2">
              {tools.map((tool) => {
                const Icon = tool.icon;
                const frames =
                  tool.slug === 'storyboard' && storyboardProgress
                    ? t('projects.workbench.toolFrames', {
                        done: storyboardProgress.done,
                        total: storyboardProgress.total,
                      })
                    : null;
                return (
                  <button
                    key={tool.slug}
                    onClick={() => setActiveTab(tool.tab)}
                    className="flex items-center gap-2.5 rounded-lg border border-ink-800 bg-ink-900/60
                               hover:border-indigo-500 hover:bg-ink-800/60 px-3 py-2.5 text-left transition-colors"
                  >
                    <span className="grid place-items-center w-7 h-7 rounded-lg bg-indigo-500/15 text-indigo-400 shrink-0">
                      <Icon className="w-3.5 h-3.5" />
                    </span>
                    <span className="min-w-0">
                      <span className="text-sm font-medium text-ink-200 truncate block">
                        {t(tool.labelKey)}
                      </span>
                      {frames && (
                        <span className="text-[11px] text-ink-500 truncate block">{frames}</span>
                      )}
                    </span>
                  </button>
                );
              })}
            </div>
          </div>
        )}
      </div>

      {AI_SUGGEST_ENABLED && (
        <StageSuggestion
          projectId={projectId}
          currentStage={currentStage}
          setActiveTab={setActiveTab}
        />
      )}

      <StageHistoryDrawer
        projectId={projectId}
        open={historyOpen}
        onClose={() => setHistoryOpen(false)}
      />
    </div>
  );
}

export default StageWorkbench;
