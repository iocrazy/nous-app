/**
 * StageToolGrid — row of quick-access tool buttons for the current SOP stage.
 *
 * Reads ``currentStage.tools_recommended`` (slug array), maps each slug
 * against TOOL_CATALOG and renders a navigational button per known slug.
 * Unknown slugs are skipped silently.  Clicking a button calls ``setActiveTab``
 * with the corresponding ProjectTab.
 */

import { useState, useEffect, useCallback } from 'react';
import { useTranslation } from 'react-i18next';
import { fetchCurrentStage } from '../../services/projectsService';
import { TOOL_CATALOG } from '../../features/projects/stageTools';
import type { ProjectStage, ProjectTab } from '../../types';

interface StageToolGridProps {
  projectId: string;
  setActiveTab: (tab: ProjectTab) => void;
}

export function StageToolGrid({ projectId, setActiveTab }: StageToolGridProps) {
  const { t } = useTranslation();
  const [currentStage, setCurrentStage] = useState<ProjectStage | null>(null);

  const load = useCallback(async () => {
    if (!projectId) return;
    try {
      const stage = await fetchCurrentStage(projectId);
      setCurrentStage(stage);
    } catch (err) {
      console.error('[StageToolGrid] failed to load current stage:', err);
    }
  }, [projectId]);

  useEffect(() => {
    load();
  }, [load]);

  if (!currentStage) return null;

  const tools = (currentStage.tools_recommended ?? [])
    .map((slug) => TOOL_CATALOG[slug])
    .filter(Boolean);

  if (tools.length === 0) return null;

  return (
    <div className="flex items-center gap-1">
      <span className="text-xs text-ink-400 shrink-0 mr-0.5">
        {t('projects.stages.recommendedTools')}
      </span>
      {tools.map((tool) => {
        const Icon = tool.icon;
        return (
          <button
            key={tool.slug}
            onClick={() => setActiveTab(tool.tab)}
            title={t(tool.labelKey)}
            className={[
              'flex items-center gap-1 px-2 py-0.5 rounded text-xs font-medium',
              'bg-ink-100 text-ink-600 hover:bg-ink-200 hover:text-ink-900',
              'transition-colors',
            ].join(' ')}
          >
            <Icon className="w-3 h-3" />
            {t(tool.labelKey)}
          </button>
        );
      })}
    </div>
  );
}
