/**
 * StageSelector — horizontal stepper showing project SOP lifecycle stages.
 *
 * Clicking a stage pill calls PUT /projects/{id}/current_stage.
 * Write access is required; read-only users see the stepper but buttons
 * are disabled.
 */

import { useState, useEffect, useCallback } from 'react';
import { useTranslation } from 'react-i18next';
import {
  fetchStageCatalog,
  setCurrentStage,
} from '../../services/projectsService';
import type { ProjectStage } from '../../types';

interface StageSelectorProps {
  projectId: string;
  /** When false the user may view but not transition stages. */
  canWrite?: boolean;
  /** Current SOP stage, owned by the parent (fetched once per project). */
  currentStage: ProjectStage | null;
  /** Called with the new stage after a successful PUT. */
  onStageChange: (stage: ProjectStage) => void;
}

export function StageSelector({ projectId, canWrite = true, currentStage, onStageChange }: StageSelectorProps) {
  const { t } = useTranslation();
  const [catalog, setCatalog] = useState<ProjectStage[]>([]);
  const [isPending, setIsPending] = useState(false);

  const loadCatalog = useCallback(async () => {
    try {
      const stages = await fetchStageCatalog();
      setCatalog(stages);
    } catch (err) {
      console.error('[StageSelector] failed to load catalog:', err);
    }
  }, []);

  useEffect(() => {
    loadCatalog();
  }, [loadCatalog]);

  if (catalog.length === 0) return null;

  const currentIndex = catalog.findIndex((s) => s.id === currentStage?.id);

  const handleStageClick = async (stage: ProjectStage) => {
    if (!canWrite || isPending || stage.id === currentStage?.id) return;
    setIsPending(true);
    try {
      const updated = await setCurrentStage(projectId, stage.id);
      // Server returns null when the project was already on the requested
      // stage (no-op) — nothing to propagate in that case.
      if (updated) {
        onStageChange(updated);
      }
    } catch (err) {
      console.error('[StageSelector] failed to set stage:', err);
    } finally {
      setIsPending(false);
    }
  };

  return (
    <div className="flex items-center gap-1 py-0.5 overflow-x-auto">
      <span className="text-xs text-ink-400 shrink-0 mr-1">
        {t('projects.stages.title')}
      </span>

      {catalog.map((stage, idx) => {
        const isActive = stage.id === currentStage?.id;
        const isPast = currentIndex >= 0 && idx < currentIndex;
        const isDisabled = !canWrite || isPending;

        return (
          <button
            key={stage.id}
            disabled={isDisabled}
            onClick={() => handleStageClick(stage)}
            title={t(`projects.stages.${stage.slug}`, { defaultValue: stage.name })}
            className={[
              'flex items-center gap-1 px-2.5 py-0.5 rounded-full text-xs font-medium',
              'transition-colors whitespace-nowrap',
              isActive
                ? 'bg-ink-900 text-ink-50 ring-1 ring-ink-700'
                : isPast
                ? 'bg-ink-200 text-ink-600 hover:bg-ink-300'
                : 'bg-ink-100 text-ink-500 hover:bg-ink-200',
              isDisabled ? 'cursor-default opacity-60' : 'cursor-pointer',
            ].join(' ')}
          >
            <span className="w-4 h-4 rounded-full text-[10px] flex items-center justify-center shrink-0">
              {idx + 1}
            </span>
            {t(`projects.stages.${stage.slug}`, { defaultValue: stage.name })}
          </button>
        );
      })}
    </div>
  );
}
