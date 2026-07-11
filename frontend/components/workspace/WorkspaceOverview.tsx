/**
 * WorkspaceOverview — the workspace shell's landing module (spec frame:
 * "Overview(落地页)", decision G6). Continue card (deep-link back into the
 * current episode's studio) + the existing StageSuggestion card rendered
 * as-is + summary tiles per module + a recent-activity line reusing the
 * same copy as ProjectCard's activity row.
 */

import { useTranslation } from 'react-i18next';
import { ArrowRight, Clock } from 'lucide-react';
import { StageSuggestion } from '../project/StageSuggestion';
import { formatRelativeTime } from '../../utils/relativeTime';
import type { EpisodeProgress, Project, ProjectStage, ProjectTab } from '../../types';

const AI_SUGGEST_ENABLED =
  import.meta.env.VITE_FEATURE_PROJECT_AI_SUGGEST === 'true';

interface WorkspaceOverviewProps {
  project: Project;
  projectId: string;
  currentStage: ProjectStage | null;
  episodes: EpisodeProgress[];
  currentEpisode: EpisodeProgress | null;
  onOpenScript: () => void;
  onSuggestionNavigate: (tab: ProjectTab) => void;
}

export function WorkspaceOverview({
  project,
  projectId,
  currentStage,
  episodes,
  currentEpisode,
  onOpenScript,
  onSuggestionNavigate,
}: WorkspaceOverviewProps) {
  const { t } = useTranslation();

  const totalShotsDone = episodes.reduce((sum, e) => sum + e.shots_done, 0);
  const totalShotsTotal = episodes.reduce((sum, e) => sum + e.shots_total, 0);
  const firstEpisodeStatus = episodes[0]?.status ?? null;
  const activity = project.latest_activity ?? null;

  return (
    <div data-testid="ws-overview" className="flex flex-col gap-3 py-3">
      {currentEpisode && (
        <div
          data-testid="ws-continue-card"
          className="rounded-xl border border-[var(--accent-border)] bg-island p-4 flex items-center justify-between gap-4"
        >
          <div className="min-w-0">
            <div className="text-[11px] uppercase tracking-wider text-[var(--accent-text)] font-semibold">
              {t('projects.workspace.overview.continue')}
            </div>
            <div className="text-sm font-medium text-ink-100 mt-0.5 truncate">
              {currentEpisode.title}
            </div>
            <div className="font-mono text-[11px] text-ink-400 mt-1">
              {t('projects.workspace.overview.scriptScenes', { count: currentEpisode.scene_count })}
              {' · '}
              {t('projects.workspace.overview.shotsProgress', {
                done: currentEpisode.shots_done,
                total: currentEpisode.shots_total,
              })}
              {' · '}
              {t('projects.workspace.overview.rendersCount', { count: currentEpisode.renders_count })}
            </div>
          </div>
          <button
            data-testid="ws-open-studio-btn"
            onClick={onOpenScript}
            className="flex items-center gap-1.5 shrink-0 rounded-lg bg-indigo-500 hover:bg-indigo-400 text-white font-semibold text-sm px-4 py-2 transition-colors"
          >
            {t('projects.workspace.overview.openStudio')}
            <ArrowRight size={15} />
          </button>
        </div>
      )}

      {AI_SUGGEST_ENABLED && (
        <StageSuggestion
          projectId={projectId}
          currentStage={currentStage}
          setActiveTab={onSuggestionNavigate}
        />
      )}

      <div className="grid grid-cols-2 sm:grid-cols-4 gap-2" data-testid="ws-overview-summary">
        <SummaryTile
          testId="ws-summary-episodes"
          labelKey="projects.workspace.modules.episodes"
          value={String(episodes.length)}
          sub={
            firstEpisodeStatus
              ? t(`projects.workspace.episodeStatus.${firstEpisodeStatus}`, firstEpisodeStatus)
              : undefined
          }
        />
        <SummaryTile
          testId="ws-summary-storyboard"
          labelKey="projects.workspace.modules.storyboard"
          value={`${totalShotsDone}/${totalShotsTotal}`}
        />
        <SummaryTile
          testId="ws-summary-files"
          labelKey="projects.workspace.modules.files"
          value={String(project.file_count ?? 0)}
        />
        <SummaryTile testId="ws-summary-canvas" labelKey="projects.workspace.modules.canvas" value="—" />
      </div>

      {activity && (
        <div
          data-testid="ws-overview-activity"
          className="flex items-center gap-1.5 text-xs text-ink-400 min-w-0 px-1"
        >
          <Clock size={12} className="flex-shrink-0" />
          <span className={`truncate ${activity.stalled ? 'text-[var(--stall)]' : ''}`}>
            {activity.kind === 'file'
              ? t('projects.card.activityFile')
              : t(activity.stalled ? 'projects.card.activityStalled' : 'projects.card.activityStage', {
                  stage: activity.label,
                })}
            {activity.actor ? ` · ${activity.actor}` : ''}
            {activity.at ? ` · ${formatRelativeTime(activity.at, t)}` : ''}
          </span>
        </div>
      )}
    </div>
  );
}

function SummaryTile({
  testId,
  labelKey,
  value,
  sub,
}: {
  testId: string;
  labelKey: string;
  value: string;
  sub?: string;
}) {
  const { t } = useTranslation();
  return (
    <div data-testid={testId} className="rounded-xl border border-line bg-island p-2.5">
      <div className="text-[11.5px] font-semibold text-ink-200">{t(labelKey)}</div>
      <div className="font-mono text-[10px] text-ink-400 mt-0.5">
        {value}
        {sub ? ` · ${sub}` : ''}
      </div>
    </div>
  );
}

export default WorkspaceOverview;
