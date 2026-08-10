/**
 * WorkspaceOverview — the workspace shell's landing module, rewritten as an
 * episode accordion (IA redesign Task 4, spec `2026-08-10-workspace-ia-redesign`).
 *
 * Replaces the old film-styled Continue card + summary-tile grid + full-project
 * workflow strip layout: one row per episode (`EpisodeSummaryRow`), the row
 * addressed by the URL (`expandedEpisodeId`, Task 1's `ep=`) expands to show
 * that episode's workflow strip and the Task 5 node-card slot
 * (`renderNodeCard`, a placeholder until that task lands). The recent-activity
 * line stays at the bottom (unchanged copy/logic).
 *
 * `workflow` scoping (binding note, read before touching the strip wiring
 * below): `ProjectWorkspace`'s `useProjectWorkflow` fetches instance nodes for
 * exactly ONE episode — whichever is `currentEpisodeId` (the server REQUIRES
 * `episode_id`, B6 PR-2 task 10 — see `workflowService.fetchProjectWorkflow`).
 * `ProjectStageNode` itself carries no `episode_id` field to filter a
 * project-wide node list by (confirmed against `types.ts`), so there is no
 * way — nor need — to slice `workflow.nodes` per row. `onExpandEpisode`
 * always routes a non-null id through `ProjectWorkspace.handleEpisodeChange`,
 * which sets `currentEpisodeId` AND writes URL `ep` in the SAME call (Task 1
 * contract), so `expandedEpisodeId` and the episode `workflow` was fetched
 * for are always the same episode whenever a row is open. The accordion body
 * below therefore renders `workflow.nodes` as-is for whichever row is open —
 * filtering by a nonexistent field would just silently break the strip.
 *
 * No-workflow / attach-CTA flow (ambiguity #1): kept reachable via the
 * existing `WorkflowSection` (unchanged import) — it owns the "Set up
 * workflow" empty state, so a No-workflow project doesn't lose that entry
 * point just because the attached-workflow case moved to the accordion. Only
 * rendered once `workflow` has resolved AND turned out to have
 * `has_workflow === false`; a still-loading `workflow === null` renders
 * nothing extra (matches the pre-rewrite behavior — `WorkflowSection` cannot
 * render with a null `workflow` prop, it's a required, non-nullable field).
 */

import type { ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import { Clock, MessageCircleQuestion } from 'lucide-react';
import { formatRelativeTime } from '../../utils/relativeTime';
import { WorkflowSection } from '../workflow/WorkflowSection';
import { WorkflowStrip } from '../workflow/WorkflowStrip';
import { EpisodeSummaryRow } from './EpisodeSummaryRow';
import type { EpisodeProgress, Project, ProjectWorkflow } from '../../types';

export interface WorkspaceOverviewProps {
  project: Project;
  episodes: EpisodeProgress[];
  /** The current episode's workflow instance (null while loading / before an
   * episode has resolved). Internally filtered to the expanded row — see the
   * file-doc "workflow scoping" note above for why no per-node filter exists. */
  workflow?: ProjectWorkflow | null;
  /** URL `ep=` (Task 1) — which episode's row is expanded. null = all collapsed. */
  expandedEpisodeId: string | null;
  /** URL `node=` — the node selected within the expanded episode's strip. */
  selectedNodeId: string | null;
  /** Row toggle: a non-null id opens that episode's row (switching the
   * workspace's current episode via `ProjectWorkspace.handleEpisodeChange`,
   * which also writes URL `ep=`); `null` collapses the currently-open row
   * without changing which episode is current (URL `ep=` stays put). */
  onExpandEpisode: (episodeId: string | null) => void;
  /** Strip node click — writes URL `node=`; does not itself navigate/route
   * (Task 5's node card owns what "selected" actually does). */
  onSelectNode: (nodeId: string) => void;
  /** Task 5's `EpisodeNodeCard` — a placeholder slot until that task lands. */
  renderNodeCard: (episodeId: string, nodeId: string | null) => ReactNode;
  /** Gates the no-workflow empty-state CTA (`WorkflowSection`'s own check) —
   * mirrors the pre-rewrite default. */
  canWrite?: boolean;
  onReloadWorkflow?: () => void;
}

export function WorkspaceOverview({
  project,
  episodes,
  workflow = null,
  expandedEpisodeId,
  selectedNodeId,
  onExpandEpisode,
  onSelectNode,
  renderNodeCard,
  canWrite = true,
  onReloadWorkflow,
}: WorkspaceOverviewProps) {
  const { t } = useTranslation();

  const activity = project.latest_activity ?? null;

  // "N episodes awaiting your answer" (B4 真数据, 2026-08-08): episodes/progress
  // now bring each episode's workflow.needs_input_count (agent-question
  // mirror-issue count). Any episode carrying a workflow signal switches on
  // real data; all missing (old backend / e2e stubs) falls back to the
  // pre-B4 planned-count approximation — same degrade strategy as
  // EpisodeSummaryRow's progress bar.
  const hasWorkflowSignal = episodes.some((e) => e.workflow != null);
  const awaitingCount = hasWorkflowSignal
    ? episodes.filter((e) => (e.workflow?.needs_input_count ?? 0) > 0).length
    : episodes.filter((e) => e.status === 'planned').length;

  return (
    <div data-testid="ws-overview" className="flex flex-col gap-3 py-3">
      {workflow && !workflow.has_workflow && (
        <WorkflowSection
          projectId={project.id}
          teamId={project.team_id ?? ''}
          episodeId={expandedEpisodeId}
          workflow={workflow}
          canWrite={canWrite}
          onReload={onReloadWorkflow ?? (() => undefined)}
          onRequestAdvance={() => undefined}
          onOpenTodolist={() => undefined}
          focusNodeId={null}
        />
      )}

      {episodes.length > 0 && (
        <div className="flex flex-col gap-1.5">
          <div className="flex items-center justify-between px-1">
            <div className="text-[11.5px] font-semibold text-ink-200">
              {t('projects.workspace.modules.episodes')}
              <span className="text-ink-400 font-normal"> · {episodes.length}</span>
            </div>
            {awaitingCount > 0 && (
              <div
                data-testid="ws-rollup-awaiting"
                className="flex items-center gap-1 text-[11px] text-[var(--warn)]"
                title={t('projects.workspace.overview.awaitingHint')}
              >
                <MessageCircleQuestion size={12} className="flex-shrink-0" />
                <span>{t('projects.workspace.overview.awaiting', { count: awaitingCount })}</span>
              </div>
            )}
          </div>
          <div className="flex flex-col gap-1.5">
            {episodes.map((ep, i) => {
              const isOpen = ep.episode_id === expandedEpisodeId;
              return (
                <EpisodeSummaryRow
                  key={ep.episode_id}
                  episode={ep}
                  epNumber={i + 1}
                  isOpen={isOpen}
                  onToggle={onExpandEpisode}
                >
                  {isOpen && (
                    <>
                      {workflow && (
                        <WorkflowStrip
                          nodes={workflow.nodes}
                          currentNodeId={workflow.current_node_id}
                          onSelectNode={(node) => onSelectNode(String(node.id))}
                        />
                      )}
                      {renderNodeCard(ep.episode_id, selectedNodeId ?? workflow?.current_node_id ?? null)}
                    </>
                  )}
                </EpisodeSummaryRow>
              );
            })}
          </div>
        </div>
      )}

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

export default WorkspaceOverview;
