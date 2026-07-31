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
import { ArrowRight, Bot, ExternalLink, FileCheck2, Rocket, User } from 'lucide-react';
import { Loading } from '../common/Loading';
import { fetchStageBoard, startEarlyNode, updateProjectNode } from '../../services/workflowService';
import { fetchProjectMembers } from '../../services/projectsService';
import { aiLibraryService } from '../../services/aiLibraryService';
import { dispatchIssue, getDispatchPreview, type DispatchPreview } from '../../services/issuesService';
import { ApiError } from '../../services/apiClient';
import { useOptionalToast } from '../Toast';
import {
  isNodeInActiveGroup,
  isNodeOverdue,
  NODE_STATUS_CONFIG,
  NODE_STATUS_LABEL,
  unmetDeps,
} from '../workflow/nodeStatus';
import type { AgentOption, PersonOption } from '../workflow/OwnerPicker';
import { BriefField } from '../workflow/BriefField';
import { DeliverablesZone } from '../Todolist/DeliverablesZone';
import { DispatchConfirmDialog } from '../Todolist/DispatchConfirmDialog';
import { StageNodeForm } from './StageNodeForm';
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
  // Optional, not required: this module also mounts in provider-less test
  // harnesses (WorkspaceStageBoard.test.tsx renders it bare) — a toast on
  // dispatch failure is a nice-to-have, never a hard dependency.
  const toast = useOptionalToast();
  const [board, setBoard] = useState<StageBoardData | null>(null);
  const [loading, setLoading] = useState(true);
  const [failed, setFailed] = useState(false);
  // Candidate pools for resolving owner_user_id / owner_agent_id to display
  // names — same lists CurrentNodeCard's OwnerPicker uses (#final-review E-item:
  // this component used to render the raw UUIDs straight from the board fetch).
  const [people, setPeople] = useState<PersonOption[]>([]);
  const [agents, setAgents] = useState<AgentOption[]>([]);
  // Run now (H3): the stage hook already prepared the run (metadata.run_prepared_at
  // is set) — clicking opens the SAME dispatch-confirm gate the Todolist uses
  // (DispatchConfirmDialog), prefilled with this node's mirror issue. Nothing
  // ever dispatches without the user pressing "Start working" in that dialog.
  const [dispatchOpen, setDispatchOpen] = useState(false);
  const [dispatchPreview, setDispatchPreview] = useState<DispatchPreview | null>(null);
  const [dispatching, setDispatching] = useState(false);
  const [refreshTick, setRefreshTick] = useState(0);
  // Start early (M4 Autopilot task O2/O3) — busy state for THIS board's node.
  const [startingEarly, setStartingEarly] = useState(false);

  useEffect(() => {
    let alive = true;
    (async () => {
      const [mem, ag] = await Promise.all([
        fetchProjectMembers(projectId).catch(() => []),
        aiLibraryService.listAgents().catch(() => []),
      ]);
      if (!alive) return;
      setPeople(mem.map((m) => ({ id: m.user_id, name: m.email || 'Member' })));
      setAgents(ag.map((a) => ({ id: a.id, name: a.name, slug: a.slug })));
    })();
    return () => {
      alive = false;
    };
  }, [projectId]);

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
    // `workflow` is included so a Complete-stage advance (which reloads the
    // shared workflow instance) also refetches THIS board — otherwise the
    // header/status/tasks kept showing the pre-advance snapshot until the user
    // navigated away and back (#final-review item: board refresh on advance).
    // `refreshTick` does the same after a Run now dispatch (H3) — a dispatched
    // agent run doesn't touch `workflow` at all, so without this the board
    // would keep showing the pre-dispatch state until the user left and came
    // back.
  }, [projectId, nodeId, workflow, refreshTick]);

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

  // Dependency gate (mig 391, M3 PR-J) — local derivation, display-only (the
  // server's DEPS_PENDING predicate is the actual advance gate; this just
  // paints the "Waiting on" row ahead of ever calling advance, same idiom as
  // the overdue tag above).
  const waitingOn = unmetDeps(workflow?.nodes ?? [], node);

  const hasFolder = Boolean(issue && node.folder_id);

  // Start early (M4 Autopilot task O2/O3, spec §3) — the manual, hand-operated
  // twin of the autopilot engine's own auto-start step. Only ever offered for
  // a node OUTSIDE the active group (the active group already has Complete
  // Stage below) whose dependencies are already satisfied (`waitingOn` above
  // — the server re-checks the exact same predicate on the actual call).
  const canStartEarly =
    !isActiveGroup && node.status === 'pending' && !node.skipped && waitingOn.length === 0;

  // brief (mig 395) — editable any time before the node finishes; once
  // done/skipped it's a permanent read-only record of what was asked for.
  const briefReadOnly = node.status === 'done' || node.status === 'skipped' || node.skipped;
  const brief = (node.brief ?? '').trim();

  // Run now (H3): only a solid button once the stage hook actually prepared a
  // run (`metadata.run_prepared_at` set) — before that it's still the plain
  // E3 suggest text chip (unchanged). Both share the same base gate
  // (suggest_agent_run && an agent owner); this only decides the chip's look
  // and what a click does.
  const runPrepared = Boolean(node.metadata?.run_prepared_at);

  /** Open the confirm gate and ask the server what a dispatch would start —
   * mirrors IssueDetailView's openDispatchConfirm exactly, just fed from the
   * board's mirror issue instead of the Todolist's selected issue. Falls back
   * to "Open in Todolist" when there's no mirror issue to dispatch (a node
   * whose hook fired before any mirror issue existed, or the lookup failed). */
  const openRunNow = () => {
    if (!issue) {
      onOpenTodolist();
      return;
    }
    setDispatchPreview(null);
    setDispatchOpen(true);
    getDispatchPreview(Number(issue.id))
      .then(setDispatchPreview)
      .catch((err) => {
        console.error('[WorkspaceStageBoard] dispatch preview failed', err);
        toast?.addToast(err instanceof Error ? err.message : t('common.error'), 'error');
        setDispatchOpen(false);
      });
  };

  /** Deliverable-form field save (task I4, spec §2) — a single-key PATCH
   * (`{ form_data: { [key]: value } }`); the server merges it into the
   * node's existing form_data (whitelisted to this node's own form_schema),
   * so this never clobbers a sibling field. The response carries the FULL
   * updated node — swapped into `board` directly, same idiom as the header's
   * owner/schedule edits elsewhere in this workflow (#1400: nothing here
   * decides the advance gate, it only feeds `form_data` for the server's
   * FORM_INCOMPLETE predicate to read). */
  const handleFormSave = async (patch: Record<string, unknown>) => {
    try {
      const updated = await updateProjectNode(projectId, node.id, { form_data: patch });
      setBoard((prev) => (prev ? { ...prev, node: updated } : prev));
    } catch (err) {
      console.error('[WorkspaceStageBoard] form save failed', err);
      toast?.addToast(err instanceof Error ? err.message : t('common.error'), 'error');
    }
  };

  /** brief blur-save (mig 395, task O1/O2/O3) — full-replace single-key
   * PATCH, same "swap the FULL updated node into `board`" idiom as the
   * form-field save above. */
  const handleBriefSave = async (next: string) => {
    try {
      const updated = await updateProjectNode(projectId, node.id, { brief: next });
      setBoard((prev) => (prev ? { ...prev, node: updated } : prev));
    } catch (err) {
      console.error('[WorkspaceStageBoard] brief save failed', err);
      toast?.addToast(err instanceof Error ? err.message : t('common.error'), 'error');
    }
  };

  /** Start early (M4 Autopilot task O2/O3) — shares the exact error codes the
   * server's tick/start-early endpoint uses: DEPS_PENDING (waiting_on),
   * NODE_CANCELLED, or a generic block. Success refetches the board (via
   * `refreshTick`, same mechanism the Run now dispatch already uses) so the
   * header/status/action-bar reflect the freshly-started node immediately. */
  const handleStartEarly = async () => {
    setStartingEarly(true);
    try {
      await startEarlyNode(projectId, node.id);
      setRefreshTick((v) => v + 1);
    } catch (err) {
      if (err instanceof ApiError && err.status === 422) {
        if (err.code === 'DEPS_PENDING') {
          const waitingOnNames =
            (err.details as { waiting_on?: string[] } | undefined)?.waiting_on ?? [];
          toast?.addToast(
            t('projects.workflow.deps.waitingOn', { names: waitingOnNames.join(', ') }),
            'error',
          );
        } else if (err.code === 'NODE_CANCELLED') {
          toast?.addToast(t('projects.workflow.startEarly.cancelled'), 'error');
        } else {
          toast?.addToast(t('projects.workflow.startEarly.blocked'), 'error');
        }
      } else {
        console.error('[WorkspaceStageBoard] start early failed', err);
        toast?.addToast(t('projects.workflow.startEarly.failed'), 'error');
      }
    } finally {
      setStartingEarly(false);
    }
  };

  const confirmRunNow = async () => {
    if (!issue) return;
    setDispatching(true);
    try {
      await dispatchIssue(Number(issue.id));
      setDispatchOpen(false);
      setRefreshTick((v) => v + 1);
      toast?.addToast('Agent dispatched', 'success');
    } catch (err) {
      console.error('[WorkspaceStageBoard] dispatch failed', err);
      toast?.addToast(err instanceof Error ? err.message : 'Dispatch failed', 'error');
    } finally {
      setDispatching(false);
    }
  };

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
            runPrepared ? (
              <button
                type="button"
                onClick={openRunNow}
                data-testid="stage-board-run-now"
                className="ml-auto inline-flex items-center gap-1.5 rounded-full px-3 py-1 text-[11px] font-medium transition"
                style={{
                  background: 'var(--accent-soft)',
                  color: 'var(--accent-text)',
                }}
              >
                <Bot size={12} /> {t('projects.workflow.runNow')}
              </button>
            ) : (
              <button
                type="button"
                onClick={onOpenTodolist}
                data-testid="stage-board-suggest-chip"
                className="ml-auto inline-flex items-center gap-1 rounded-full bg-amber-500/12 px-2.5 py-1 text-[11px] font-medium text-amber-400 transition hover:bg-amber-500/20"
              >
                <Bot size={12} />
                {t('projects.workflow.suggestAgentRun', {
                  agentName:
                    agents.find((a) => a.id === node.owner_agent_id)?.name ??
                    t('projects.workflow.genericAgent'),
                })}
              </button>
            )
          )}
        </div>

        <div className="mt-3 flex flex-wrap items-center gap-4 text-[12.5px] text-ink-400">
          <span data-testid="stage-board-owner" className="inline-flex items-center gap-1.5">
            <span className="uppercase tracking-wider text-[10px] text-ink-600">
              {t('projects.workflow.owner')}
            </span>
            {node.owner_agent_id ? (
              <span className="inline-flex items-center gap-1 text-ink-200">
                <Bot size={12} /> {agents.find((a) => a.id === node.owner_agent_id)?.name ?? 'Agent'}
              </span>
            ) : node.owner_user_id ? (
              <span className="inline-flex items-center gap-1 text-ink-200">
                <User size={12} /> {people.find((p) => p.id === node.owner_user_id)?.name ?? 'Member'}
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

        {waitingOn.length > 0 && (
          <div data-testid="stage-board-waiting-on" className="mt-2 text-[12px] text-rose-400">
            {t('projects.workflow.deps.waitingOn', {
              names: waitingOn.map((n) => n.name).join(', '),
            })}
          </div>
        )}
      </div>

      {/* ── in_review pin (M4 Autopilot task O2/O3, spec §3: brief 置顶展示
          于 Stage Board 审阅区) — read-only highlight, front and center for
          whoever is reviewing this stage. ──────────────────────────────── */}
      {node.status === 'in_review' && brief && (
        <div
          data-testid="stage-board-brief-pinned"
          className="rounded-xl border border-[var(--accent-border)] bg-[var(--accent-soft)] p-4 text-[13px] text-[var(--accent-text)]"
        >
          <h3 className="mb-1.5 text-[11px] uppercase tracking-wider opacity-80">
            {t('projects.workflow.brief.reviewHeading')}
          </h3>
          <p className="whitespace-pre-wrap">{brief}</p>
        </div>
      )}

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

      {/* ── Deliverable form — schema empty (pre-mig-390 / no Form tab) → absent ── */}
      <StageNodeForm
        schema={node.form_schema ?? []}
        data={node.form_data ?? {}}
        disabled={!canWrite}
        onSave={handleFormSave}
      />

      {/* ── brief (mig 395, M4 Autopilot task O1/O2/O3) — pre-work notes for
          whoever works this stage; injected into an agent's context on
          auto-start/dispatch/start-early alike. Blur-save, read-only once
          the node is done/skipped. ──────────────────────────────────────── */}
      <div data-testid="stage-board-brief" className="rounded-xl border border-line bg-island p-4">
        <h3 className="mb-2 text-[11px] uppercase tracking-wider text-ink-500">
          {t('projects.workflow.brief.label')}
        </h3>
        <BriefField
          value={node.brief ?? ''}
          disabled={!canWrite || briefReadOnly}
          placeholder={t('projects.workflow.brief.placeholder')}
          onSave={(next) => void handleBriefSave(next)}
          testId="stage-board-brief-field"
        />
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

      {/* ── Start early (M4 Autopilot task O2/O3) — a future node OUTSIDE the
          active group whose dependencies are already satisfied. Mutually
          exclusive with the action bar above (canStartEarly requires
          !isActiveGroup). ────────────────────────────────────────────────── */}
      {canStartEarly && canWrite && (
        <div className="flex items-center justify-end">
          <button
            type="button"
            onClick={() => void handleStartEarly()}
            disabled={startingEarly}
            data-testid="stage-board-start-early"
            className="inline-flex items-center gap-1.5 rounded-md border px-3 py-1.5 text-[12.5px] transition disabled:cursor-not-allowed disabled:opacity-50"
            style={{
              background: 'var(--accent-soft)',
              color: 'var(--accent-text)',
              borderColor: 'var(--accent-border)',
            }}
          >
            <Rocket size={13} /> {t('projects.workflow.startEarly.button')}
          </button>
        </div>
      )}

      {dispatchOpen && (
        <DispatchConfirmDialog
          preview={dispatchPreview}
          agentName={
            (dispatchPreview?.agent_id
              ? agents.find((a) => a.id === dispatchPreview.agent_id)?.name
              : undefined) ?? agents.find((a) => a.id === node.owner_agent_id)?.name
          }
          confirming={dispatching}
          onConfirm={() => void confirmRunNow()}
          onClose={() => setDispatchOpen(false)}
        />
      )}
    </div>
  );
};

export default WorkspaceStageBoard;
