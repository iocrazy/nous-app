/**
 * ProjectsQueueView — homepage default view (PR-9, G7): a work-queue of
 * per-project "next action" rows, driven by the batch
 * `GET /api/v1/projects/suggestions` endpoint (see docs/superpowers/specs/
 * 2026-07-10-projects-workspace-final.html "主页" section for the mockup).
 *
 * Row order: stalled projects first, then one-click "generate missing
 * frames" rows, then other navigate rows, with delivery-stage nudges
 * pushed to the bottom — starred projects are pinned first within their
 * group. Rows whose `kind` is empty (nothing to suggest right now) are
 * skipped, and rows are only rendered for projects present in the `projects`
 * prop (so the queue respects the caller's current search/filter scope and
 * archived projects — already excluded by the endpoint — never leak in).
 */
import { useCallback, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { FolderOpen, Loader2, Star } from 'lucide-react';
import type { Project, ProjectSuggestionItem } from '../types';
import { generateMissingFrames } from '../services/projectsService';
import { useToast } from './Toast';
import { StageRing } from './project/StageRing';
import { formatRelativeTime } from '../utils/relativeTime';

interface ProjectsQueueViewProps {
  projects: Project[];
  suggestions: ProjectSuggestionItem[];
  onProjectSelect: (project: Project) => void;
  onRefetchSuggestions: () => void;
}

interface QueueRow {
  item: ProjectSuggestionItem;
  project: Project | null;
  index: number;
}

/** Group priority: stalled first, then one-click generate, then navigate, delivery last. */
function groupRank(item: ProjectSuggestionItem): number {
  if (item.stalled) return 0;
  if (item.action?.type === 'generate_missing_frames') return 1;
  if (item.kind === 'delivery_nav') return 3;
  return 2;
}

function dotClass(item: ProjectSuggestionItem): string {
  if (item.stalled) return 'bg-[var(--stall)]';
  if (item.action?.type === 'generate_missing_frames') return 'bg-indigo-500';
  if (item.kind === 'delivery_nav') return 'bg-ink-500';
  return 'bg-emerald-400';
}

export function ProjectsQueueView({
  projects,
  suggestions,
  onProjectSelect,
  onRefetchSuggestions,
}: ProjectsQueueViewProps) {
  const { t } = useTranslation();
  const { addToast } = useToast();
  const [busyId, setBusyId] = useState<string | null>(null);

  const projectsById = useMemo(() => {
    const map = new Map<string, Project>();
    for (const p of projects) map.set(String(p.id), p);
    return map;
  }, [projects]);

  const rows = useMemo<QueueRow[]>(() => {
    return suggestions
      .filter((item) => item.kind !== '')
      .map((item, index) => ({
        item,
        project: projectsById.get(String(item.project_id)) ?? null,
        index,
      }))
      .filter((row) => row.project !== null)
      .sort((a, b) => {
        const rankDiff = groupRank(a.item) - groupRank(b.item);
        if (rankDiff !== 0) return rankDiff;
        const starredDiff = Number(!!b.project?.is_starred) - Number(!!a.project?.is_starred);
        if (starredDiff !== 0) return starredDiff;
        return a.index - b.index;
      });
  }, [suggestions, projectsById]);

  const handleGenerate = useCallback(
    async (projectId: string) => {
      setBusyId(projectId);
      try {
        const res = await generateMissingFrames(projectId);
        addToast(t('projects.suggest.generating', { count: res.dispatched_count }), 'success');
        onRefetchSuggestions();
      } catch (err) {
        console.error('[ProjectsQueueView] generate-missing failed:', err);
        addToast(t('common.error'), 'error');
      } finally {
        setBusyId(null);
      }
    },
    [addToast, t, onRefetchSuggestions],
  );

  if (rows.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center py-20 text-center" data-testid="projects-queue-empty">
        <div className="p-4 bg-ink-800 rounded-2xl mb-4">
          <FolderOpen size={40} className="text-ink-500" />
        </div>
        <h3 className="text-lg font-medium text-ink-300 mb-2">
          {t('projects.queue.empty', 'Nothing needs your attention right now')}
        </h3>
      </div>
    );
  }

  return (
    <div
      className="border border-ink-700/50 rounded-xl overflow-hidden"
      data-testid="projects-queue-view"
    >
      {rows.map(({ item, project }) => {
        const progress = item.progress ?? undefined;
        const message = t(`projects.suggest.${item.kind}`, {
          done: progress?.done,
          total: progress?.total,
          count: item.action?.count,
          scene_count: progress?.scene_count,
        });
        const isGenerate = item.action?.type === 'generate_missing_frames';
        const busy = busyId === item.project_id;
        const activity = item.latest_activity;
        const stageName = project?.current_stage?.name ?? item.stage_slug ?? '';

        return (
          <div
            key={item.project_id}
            data-testid="queue-row"
            onClick={() => project && onProjectSelect(project)}
            className="flex items-center gap-3.5 px-4 py-3 border-t border-ink-700/50 first:border-t-0 bg-ink-800/40 hover:bg-ink-800/70 transition-colors cursor-pointer"
          >
            <span className={`w-1.5 h-1.5 rounded-full flex-shrink-0 ${dotClass(item)}`} data-testid="queue-attn-dot" />
            {project && <StageRing stage={project.current_stage} size={34} />}
            <div className="min-w-0 w-44 flex-shrink-0">
              <div className="text-sm font-medium text-ink-50 truncate flex items-center gap-1">
                {item.name}
                {project?.is_starred && <Star size={11} className="text-yellow-400 fill-yellow-400 shrink-0" />}
              </div>
              {activity && (
                <div
                  className={`text-[11px] truncate ${item.stalled ? 'text-[var(--stall)]' : 'text-ink-500'}`}
                >
                  {activity.kind === 'file'
                    ? t('projects.card.activityFile')
                    : t(item.stalled ? 'projects.card.activityStalled' : 'projects.card.activityStage', {
                        stage: activity.label,
                      })}
                  {activity.actor ? ` · ${activity.actor}` : ''}
                  {activity.at ? ` · ${formatRelativeTime(activity.at, t)}` : ''}
                </div>
              )}
            </div>
            {stageName && (
              <span className="text-xs px-2 py-0.5 rounded-full font-medium bg-[var(--accent-soft)] text-[var(--accent-text)] flex-shrink-0">
                {stageName}
              </span>
            )}
            <span className="flex-1 min-w-0 text-xs text-ink-400 truncate">{message}</span>
            {isGenerate ? (
              <button
                data-testid="queue-cta"
                disabled={busy}
                onClick={(e) => {
                  e.stopPropagation();
                  handleGenerate(item.project_id);
                }}
                className="flex items-center gap-1.5 flex-shrink-0 rounded-lg font-medium text-xs px-3 py-1.5
                           transition-colors bg-indigo-500 hover:bg-indigo-400 disabled:opacity-50 text-ink-950"
              >
                {busy ? <Loader2 size={13} className="animate-spin" /> : null}
                {item.action ? t(item.action.label_key, { count: item.action.count }) : ''}
              </button>
            ) : (
              <button
                data-testid="queue-cta"
                onClick={(e) => {
                  e.stopPropagation();
                  project && onProjectSelect(project);
                }}
                className="flex-shrink-0 rounded-lg font-medium text-xs px-3 py-1.5 border border-ink-600
                           text-ink-200 hover:bg-ink-700 transition-colors"
              >
                {item.action ? t(item.action.label_key, { count: item.action.count }) : ''}
              </button>
            )}
          </div>
        );
      })}
    </div>
  );
}

export default ProjectsQueueView;
