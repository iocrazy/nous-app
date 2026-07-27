/**
 * WorkspaceOverview — the workspace shell's landing module (spec frame:
 * "Overview(落地页)", decision G6). A film-styled Continue card (slate stripe +
 * mono read-out, deep-links into the current episode's studio) + summary tiles
 * per module + a recent-activity line reusing the same copy as ProjectCard's
 * activity row.
 */

import { useTranslation } from 'react-i18next';
import { ArrowRight, Clock } from 'lucide-react';
import { formatRelativeTime } from '../../utils/relativeTime';
import { WorkflowSection } from '../workflow/WorkflowSection';
import type { EpisodeProgress, Project, ProjectWorkflow } from '../../types';

interface WorkspaceOverviewProps {
  project: Project;
  currentEpisode: EpisodeProgress | null;
  /** 1-based index of the current episode (for the CONTINUE · EP read-out). */
  epNumber: number | null;
  episodes: EpisodeProgress[];
  onOpenScript: () => void;
  /** The project's workflow instance (null while loading / no workflow). */
  workflow?: ProjectWorkflow | null;
  canWrite?: boolean;
  onReloadWorkflow?: () => void;
  onRequestAdvance?: (direction: 'forward' | 'back') => void;
  onOpenTodolist?: () => void;
  /** Navigate to a node's dedicated Stage Board (H3 Run now chip) — threaded
   * through to WorkflowSection → CurrentNodeCard. */
  onOpenStage?: (nodeId: string) => void;
  focusNodeId?: string | null;
}

export function WorkspaceOverview({
  project,
  currentEpisode,
  epNumber,
  episodes,
  onOpenScript,
  workflow = null,
  canWrite = true,
  onReloadWorkflow,
  onRequestAdvance,
  onOpenTodolist,
  onOpenStage,
  focusNodeId = null,
}: WorkspaceOverviewProps) {
  const { t } = useTranslation();

  const totalShotsDone = episodes.reduce((sum, e) => sum + e.shots_done, 0);
  const totalShotsTotal = episodes.reduce((sum, e) => sum + e.shots_total, 0);
  const firstEpisodeStatus = episodes[0]?.status ?? null;
  const activity = project.latest_activity ?? null;

  return (
    <div data-testid="ws-overview" className="flex flex-col gap-3 py-3">
      {workflow?.has_workflow && (
        <WorkflowSection
          projectId={project.id}
          workflow={workflow}
          canWrite={canWrite}
          onReload={onReloadWorkflow ?? (() => undefined)}
          onRequestAdvance={onRequestAdvance ?? (() => undefined)}
          onOpenTodolist={onOpenTodolist ?? (() => undefined)}
          onOpenStage={onOpenStage}
          focusNodeId={focusNodeId}
        />
      )}
      {currentEpisode && (
        <div
          data-testid="ws-continue-card"
          className="rounded-xl border border-[var(--accent-border)] bg-island p-4 flex items-center justify-between gap-4"
        >
          <div className="flex items-center gap-3 min-w-0">
            <span
              aria-hidden
              className="w-9 h-5 rounded-[4px] shrink-0"
              style={{
                background: 'repeating-linear-gradient(-45deg,#f4f1fb 0 5px,#16121f 5px 10px)',
                border: '1px solid var(--line-strong)',
              }}
            />
            <div className="min-w-0">
              <div className="font-mono text-[9px] font-bold tracking-[0.14em] text-[var(--accent-text)]">
                CONTINUE{epNumber ? ` · EP${epNumber}` : ''}
              </div>
              <div className="text-sm font-medium text-ink-100 mt-0.5 truncate">
                {currentEpisode.title}
              </div>
              <div className="font-mono text-[11px] text-ink-400 mt-1">
                {currentEpisode.scene_count} SC · SHOTS {currentEpisode.shots_done}/
                {currentEpisode.shots_total} · CUTS {currentEpisode.renders_count}
              </div>
            </div>
          </div>
          <button
            data-testid="ws-open-episode-btn"
            onClick={onOpenScript}
            className="flex items-center gap-1.5 shrink-0 rounded-lg bg-indigo-500 hover:bg-indigo-400 text-white font-semibold text-sm px-4 py-2 transition-colors"
          >
            {t('projects.workspace.overview.openEpisode')}
            <ArrowRight size={15} />
          </button>
        </div>
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
      <div className="font-mono text-[12px] font-bold text-[var(--accent-text)] mt-0.5">
        {value}
        {sub ? <span className="text-[10px] font-normal text-ink-400"> · {sub}</span> : ''}
      </div>
    </div>
  );
}

export default WorkspaceOverview;
