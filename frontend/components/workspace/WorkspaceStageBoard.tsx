/**
 * WorkspaceStageBoard — the Stage Board workspace module (M2 PR-F F3).
 *
 * Opened from the sidebar's dynamic Stages block (never a fixed menu item —
 * see workspaceModules.ts), it is the single-node counterpart to the
 * Overview's `CurrentNodeCard`: a read-only header (name/status/owner/
 * schedule/suggest-agent chip), a Tasks section mirroring the node's mirror
 * issue + sub-issues, and a Deliverables section. Unlike the node card this
 * renders for ANY node the sidebar links to, not just the current one — so
 * the "Complete stage" action bar only appears when the node is in the
 * workflow's active group (current node, or a parallel-group sibling of it);
 * every other node renders no action bar at all (#1400: server owns the
 * advance predicate, this component only ever renders what `onRequestAdvance`
 * → the shared preview/confirm gate rules on).
 */

import React, { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { ArrowRight, Bot, ExternalLink, FileCheck2, User } from 'lucide-react';
import { Loading } from '../common/Loading';
import { fetchStageBoard } from '../../services/workflowService';
import {
  isNodeInActiveGroup,
  isNodeOverdue,
  NODE_STATUS_CONFIG,
  NODE_STATUS_LABEL,
} from '../workflow/nodeStatus';
import { DeliverablesZone } from '../Todolist/DeliverablesZone';
import type { ProjectWorkflow, StageBoardData, StageBoardIssueRef } from '../../types';

interface WorkspaceStageBoardProps {
  projectId: string;
  projectName: string;
  nodeId: string;
  /** The project's shared workflow instance — used only to decide whether
   * THIS node is in the active group (current node ± parallel-group
   * siblings); the node's own detail data comes from the board fetch. */
  workflow: ProjectWorkflow | null;
  canWrite: boolean;
  onRequestAdvance: (direction: 'forward' | 'back') => void;
  onOpenTodolist: () => void;
}

/** One read-only issue row (mirror issue or one of its sub-issues). */
const IssueRow: React.FC<{ issue: StageBoardIssueRef; indent?: boolean }> = ({ issue, indent }) => (
  <div
    data-testid={`stage-board-issue-${issue.id}`}
    className={`flex items-center gap-2 rounded-md border border-ink-800 bg-ink-900/40 px-3 py-2 text-[13px] ${
      indent ? 'ml-4' : ''
    }`}
  >
    <span className="shrink-0 rounded-full bg-ink-800 px-2 py-0.5 text-[10px] uppercase text-ink-400">
      {issue.status}
    </span>
    <span className="shrink-0 font-mono text-[11px] uppercase text-ink-500">
      {issue.identifier ?? '—'}
    </span>
    <span className="min-w-0 flex-1 truncate text-ink-200">{issue.title ?? '—'}</span>
  </div>
);

export const WorkspaceStageBoard: React.FC<WorkspaceStageBoardProps> = ({
  projectId,
  projectName,
  nodeId,
  workflow,
  canWrite,
  onRequestAdvance,
  onOpenTodolist,
}) => {
  const { t } = useTranslation();
  const [board, setBoard] = useState<StageBoardData | null>(null);
  const [loading, setLoading] = useState(true);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setBoard(null);
    setFailed(false);
    setLoading(true);
    fetchStageBoard(projectId, nodeId)
      .then((data) => {
        if (!cancelled) setBoard(data);
      })
      .catch((err) => {
        console.error('[WorkspaceStageBoard] failed to load board', err);
        if (!cancelled) setFailed(true);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [projectId, nodeId]);

  if (loading) {
    return (
      <div data-testid="stage-board-loading" className="grid place-items-center py-16">
        <Loading center />
      </div>
    );
  }

  if (failed || !board) {
    return (
      <div data-testid="stage-board-error" className="py-16 text-center text-[13px] text-ink-500 italic">
        {t('common.error')}
      </div>
    );
  }

  const { node, issue, files } = board;
  const meta = NODE_STATUS_CONFIG[node.status];
  const overdue = isNodeOverdue(node);

  const currentNode = workflow?.nodes.find((n) => n.id === workflow.current_node_id) ?? null;
  const isActiveGroup = isNodeInActiveGroup(node, currentNode);

  const hasFolder = Boolean(issue && node.folder_id);

  return (
    <div data-testid="workspace-stage-board" className="flex flex-col gap-4 py-3">
      {/* ── Node header ──────────────────────────────────────────────────── */}
      <div
        data-testid="stage-board-header"
        className="rounded-xl border border-line bg-island p-4"
      >
        <div className="flex flex-wrap items-center gap-2">
          <span className={`h-2.5 w-2.5 rounded-full ${meta.dot}`} aria-hidden />
          <h2 className="text-sm font-semibold text-ink-100">{node.name}</h2>
          <span className={`text-[11px] font-medium ${meta.text}`}>
            {NODE_STATUS_LABEL[node.status]}
          </span>
          {node.events.suggest_agent_run && node.owner_agent_id && (
            <button
              type="button"
              onClick={onOpenTodolist}
              data-testid="stage-board-suggest-chip"
              className="ml-auto inline-flex items-center gap-1 rounded-full bg-amber-500/12 px-2.5 py-1 text-[11px] font-medium text-amber-400 transition hover:bg-amber-500/20"
            >
              <Bot size={12} />
              {t('projects.workflow.suggestAgentRun', { agentName: node.owner_agent_id })}
            </button>
          )}
        </div>

        <div className="mt-3 flex flex-wrap items-center gap-4 text-[12.5px] text-ink-400">
          <span data-testid="stage-board-owner" className="inline-flex items-center gap-1.5">
            <span className="uppercase tracking-wider text-[10px] text-ink-600">
              {t('projects.workflow.owner')}
            </span>
            {node.owner_agent_id ? (
              <span className="inline-flex items-center gap-1 text-ink-200">
                <Bot size={12} /> {node.owner_agent_id}
              </span>
            ) : node.owner_user_id ? (
              <span className="inline-flex items-center gap-1 text-ink-200">
                <User size={12} /> {node.owner_user_id}
              </span>
            ) : (
              <span className="text-ink-500">{t('projects.workflow.unassigned')}</span>
            )}
          </span>

          {(node.planned_start || node.planned_due) && (
            <span data-testid="stage-board-schedule" className={overdue ? 'text-rose-400' : undefined}>
              <span className="uppercase tracking-wider text-[10px] text-ink-600 mr-1.5">
                {t('projects.workflow.schedule')}
              </span>
              {node.planned_start ?? '—'} → {node.planned_due ?? '—'}
            </span>
          )}

          {overdue && (
            <span
              data-testid="stage-board-overdue"
              className="inline-flex items-center gap-1 rounded-full bg-rose-500/15 px-2 py-0.5 text-[10px] font-medium uppercase tracking-wide text-rose-300"
            >
              {t('projects.workflow.overdue')}
            </span>
          )}
        </div>
      </div>

      {/* ── Tasks — mirror issue + sub-issues, all read-only ────────────── */}
      <div data-testid="stage-board-tasks" className="rounded-xl border border-line bg-island p-4">
        <h3 className="mb-2 text-[11px] uppercase tracking-wider text-ink-500">
          {t('projects.workflow.stageBoard.tasks')}
        </h3>
        {issue ? (
          <div className="flex flex-col gap-1.5">
            <IssueRow issue={issue} />
            {issue.sub_issues.map((sub) => (
              <IssueRow key={sub.id} issue={sub} indent />
            ))}
          </div>
        ) : (
          <div data-testid="stage-board-no-issue" className="text-[13px] text-ink-600 italic">
            {t('projects.workflow.stageBoard.noIssue')}
          </div>
        )}
        <button
          type="button"
          onClick={onOpenTodolist}
          data-testid="stage-board-open-todolist"
          className="mt-3 inline-flex items-center gap-1.5 rounded-md border border-line px-2.5 py-1.5 text-[12.5px] text-ink-300 transition hover:border-line-strong hover:text-ink-100"
        >
          <ExternalLink size={13} /> {t('projects.workflow.openInTodolist')}
        </button>
      </div>

      {/* ── Deliverables ─────────────────────────────────────────────────── */}
      <div data-testid="stage-board-deliverables" className="rounded-xl border border-line bg-island p-4">
        {hasFolder && issue ? (
          <DeliverablesZone
            projectId={projectId}
            issueId={Number(issue.id)}
            isStageMirror
            projectName={projectName}
            stageName={node.name}
          />
        ) : (
          <>
            <h3 className="mb-2 flex items-center gap-2 text-[11px] uppercase tracking-wider text-ink-500">
              <FileCheck2 size={13} />
              {t('projects.workflow.stageBoard.deliverables')}
            </h3>
            {files.length === 0 ? (
              <div data-testid="stage-board-files-empty" className="text-[13px] text-ink-600 italic">
                {t('projects.workflow.deliverables.empty')}
              </div>
            ) : (
              <ul data-testid="stage-board-files-list" className="flex flex-col gap-1.5">
                {files.map((f) => (
                  <li
                    key={f.id}
                    data-testid="stage-board-file-row"
                    className="flex items-center gap-2 rounded-md border border-ink-800 bg-ink-900/40 px-3 py-2 text-[13px] text-ink-200"
                  >
                    <FileCheck2 size={14} className="shrink-0 text-emerald-400" />
                    <span className="min-w-0 flex-1 truncate">{f.filename ?? '—'}</span>
                    {f.source_issue_identifier && (
                      <span className="shrink-0 text-[11px] text-ink-500">
                        {t('projects.workflow.deliverables.fromIssue', { id: f.source_issue_identifier })}
                      </span>
                    )}
                  </li>
                ))}
              </ul>
            )}
          </>
        )}
      </div>

      {/* ── Action bar — active group only (#1400 server-ruled gate) ────── */}
      {isActiveGroup && canWrite && (
        <div data-testid="stage-board-actions" className="flex items-center justify-end">
          <button
            type="button"
            onClick={() => onRequestAdvance('forward')}
            data-testid="stage-board-complete"
            className="inline-flex items-center gap-1.5 rounded-md border px-3 py-1.5 text-[12.5px] transition"
            style={{
              background: 'var(--accent-soft)',
              color: 'var(--accent-text)',
              borderColor: 'var(--accent-border)',
            }}
          >
            {t('projects.workflow.completeStage')} <ArrowRight size={13} />
          </button>
        </div>
      )}
    </div>
  );
};

export default WorkspaceStageBoard;
