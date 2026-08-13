/**
 * CurrentNodeCard — the active workflow node's control panel on the workspace
 * Overview (spec §5). Owner / members through the shared OwnerPicker, a
 * planned_start/due range through the shared DateTimePopover, the deliverable line, and
 * the two flow actions: Open in Todolist (jump to the mirror issue's module)
 * and Complete stage (opens the server-computed advance gate). Every in-place
 * edit writes the instance node only (PATCH), then reloads the shared workflow.
 */

import React, { useState } from 'react';
import { ArrowLeft, ArrowRight, Bot, CalendarDays, ChevronDown, ClipboardList, ExternalLink, FileCheck2, Rocket } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { updateProjectNode } from '../../services/workflowService';
import type { ProjectNodePatch, ProjectStageNode } from '../../types';
import { AgentOption, OwnerPicker, PersonOption } from './OwnerPicker';
import { DateTimePopover } from '../common/DateTimePopover';
import { isNodeOverdue, NODE_STATUS_CONFIG, NODE_STATUS_LABEL, unmetDeps } from './nodeStatus';
import { countFilledFields } from './formFieldFill';
import { BriefField } from './BriefField';

interface CurrentNodeCardProps {
  projectId: string;
  node: ProjectStageNode;
  canWrite: boolean;
  people: PersonOption[];
  agents: AgentOption[];
  onPatched: () => void;
  onRequestAdvance: (direction: 'forward' | 'back') => void;
  onOpenTodolist: () => void;
  /** Navigate to this node's dedicated Stage Board (sidebar's Stages block —
   * see ProjectWorkspace.tsx's `handleOpenStage`), where Run now can actually
   * open DispatchConfirmDialog inline (WorkspaceStageBoard has the mirror
   * issue this card doesn't). Optional so the card stays safe standalone
   * (e.g. in a host that hasn't wired the Stage Board route yet) — falls back
   * to `onOpenTodolist` when absent. */
  onOpenStage?: (nodeId: string) => void;
  /** False when WorkflowSection is rendering this card for a FUTURE node — a
   * peek-ahead "Start early" surface (M4 Autopilot task O3), not the active
   * cursor group. Hides the Back/Complete-stage row (those act on the shared
   * workflow cursor, not this specific node) and instead may show a "Start
   * early" button. Defaults `true` — every existing caller (the active
   * group) keeps its current behavior unchanged. */
  isActive?: boolean;
  /** The full project node list, needed to compute this node's own dependency
   * gate locally (`nodeStatus.ts::unmetDeps`, display-only — the server
   * re-checks on the actual `startEarlyNode` call). Optional: omitting it
   * (or `isActive` staying `true`) simply never shows the Start-early button. */
  allNodes?: ProjectStageNode[];
  /** Fires the actual `startEarlyNode` call — the caller (WorkflowSection)
   * owns the busy state / toast / error-code mapping, mirroring how
   * `onRequestAdvance` and `onPatched` are also thin fire callbacks here. */
  onStartEarly?: (nodeId: string) => void;
  /** Disables the Start-early button while the caller's request is in flight. */
  startEarlyBusy?: boolean;
}

const Row: React.FC<{ label: string; children: React.ReactNode }> = ({ label, children }) => (
  <div className="grid grid-cols-[5.5rem_1fr] items-start gap-3 py-1.5">
    <span className="pt-1.5 text-[11px] uppercase tracking-wider text-ink-600">{label}</span>
    <div className="min-w-0">{children}</div>
  </div>
);

export const CurrentNodeCard: React.FC<CurrentNodeCardProps> = ({
  projectId,
  node,
  canWrite,
  people,
  agents,
  onPatched,
  onRequestAdvance,
  onOpenTodolist,
  onOpenStage,
  isActive = true,
  allNodes,
  onStartEarly,
  startEarlyBusy,
}) => {
  const { t } = useTranslation();
  const [saving, setSaving] = useState(false);
  // B5 T-B5.8: management fields (owner / members / schedule) are collapsed by
  // default so the card leads with "what's happening now + what to deliver".
  // Auto-open when the node is overdue so the schedule stays discoverable
  // without a click when it actually needs attention.
  const [detailsOpen, setDetailsOpen] = useState(() => isNodeOverdue(node));
  const [scheduleAnchor, setScheduleAnchor] = useState<HTMLElement | null>(null);
  const meta = NODE_STATUS_CONFIG[node.status];
  const overdue = isNodeOverdue(node);

  // Dependency gate (mig 391, M3 PR-J), reused for the Start-early gate (M4
  // Autopilot task O3, spec §3: "deps-satisfied … 时显示按钮") — local
  // derivation, display-only; the server re-rules on the actual call.
  const waitingOn = allNodes ? unmetDeps(allNodes, node) : [];
  const canStartEarly =
    !isActive && node.status === 'pending' && !node.skipped && waitingOn.length === 0;

  // brief (mig 395): editable any time before the node finishes; once
  // done/skipped it's a permanent read-only record of what was asked for.
  const briefReadOnly = node.status === 'done' || node.status === 'skipped' || node.skipped;
  const brief = (node.brief ?? '').trim();

  const patch = async (body: ProjectNodePatch) => {
    setSaving(true);
    try {
      await updateProjectNode(projectId, node.id, body);
      onPatched();
    } catch (err) {
      console.error('[CurrentNodeCard] patch failed', err);
    } finally {
      setSaving(false);
    }
  };

  /** Navigate to this node's dedicated Stage Board, falling back to Open in
   * Todolist when the host hasn't wired `onOpenStage` through. Shared by Run
   * now (it needs the mirror issue this card doesn't have) and the Form
   * completion row below (task I4) — both just want "go look at this node's
   * full surface". */
  const goToStageBoard = () => {
    if (onOpenStage) {
      onOpenStage(node.id);
    } else {
      onOpenTodolist();
    }
  };

  const formSchema = node.form_schema ?? [];
  const { filled: formFilled, total: formTotal } = countFilledFields(formSchema, node.form_data ?? {});

  return (
    <div
      data-testid="workflow-current-node-card"
      data-node-id={node.id}
      className="rounded-xl border border-[var(--accent-border)] bg-island p-4"
    >
      <div className="mb-2 flex items-center gap-2">
        <span className={`h-2.5 w-2.5 rounded-full ${meta.dot}`} aria-hidden />
        <h3 className="text-sm font-semibold text-ink-100">{node.name}</h3>
        <span className={`text-[11px] font-medium ${meta.text}`}>
          {NODE_STATUS_LABEL[node.status]}
        </span>
        {node.events.suggest_agent_run && node.owner_agent_id && (
          node.metadata?.run_prepared_at ? (
            // H3: the stage hook already prepared this run (metadata.run_prepared_at
            // is set) — solid "Run now" button instead of the plain suggest text.
            // This card has no mirror-issue id to prefill DispatchConfirmDialog with
            // directly, so a click navigates to the node's Stage Board (which does
            // have the issue and opens the dialog inline — see WorkspaceStageBoard)
            // via the optional onOpenStage prop; falls back to Open in Todolist when
            // the host hasn't wired that route through.
            <button
              type="button"
              onClick={goToStageBoard}
              data-testid="workflow-run-now-chip"
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
              data-testid="workflow-suggest-agent-chip"
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

      {/* in_review pin (M4 Autopilot task O3, spec §3: "brief 置顶展示" —
          primarily the Stage Board's review context, but a cheap same-signal
          highlight here too so the card doesn't disagree with it). */}
      {node.status === 'in_review' && brief && (
        <div
          data-testid="workflow-node-brief-pinned"
          className="mb-3 rounded-md border border-[var(--accent-border)] bg-[var(--accent-soft)] px-3 py-2 text-[12.5px] text-[var(--accent-text)]"
        >
          <div className="mb-1 text-[10px] uppercase tracking-wider opacity-80">
            {t('projects.workflow.brief.reviewHeading')}
          </div>
          <p className="whitespace-pre-wrap">{brief}</p>
        </div>
      )}

      {(node.deliverable_label || node.deliverable_required) && (
        <Row label={t('projects.workflow.deliverable')}>
          <div className="flex items-center gap-2 pt-1 text-[13px] text-ink-300">
            <FileCheck2 size={14} className="shrink-0 text-ink-500" />
            <span className="truncate">{node.deliverable_label || '—'}</span>
            {node.deliverable_required && (
              <span className="rounded-full bg-amber-500/10 px-1.5 py-0.5 text-[10px] text-amber-400">
                Required
              </span>
            )}
            <span
              data-testid="workflow-node-filed-count"
              className={`ml-auto shrink-0 rounded-full px-1.5 py-0.5 text-[10px] ${
                (node.deliverable_file_count ?? 0) > 0
                  ? 'bg-emerald-500/10 text-emerald-300'
                  : 'bg-ink-800 text-ink-500'
              }`}
            >
              {t('projects.workflow.deliverables.filesFiled', {
                count: node.deliverable_file_count ?? 0,
              })}
            </span>
          </div>
        </Row>
      )}

      {formTotal > 0 && (
        <Row label={t('projects.workflow.form')}>
          <button
            type="button"
            onClick={goToStageBoard}
            data-testid="workflow-node-form-progress"
            className="inline-flex items-center gap-1.5 rounded-md border border-line px-2 py-1 text-[12.5px] text-ink-300 transition hover:border-line-strong hover:text-ink-100"
          >
            <ClipboardList size={14} className="text-ink-500" />
            {t('projects.workflow.formFilled', { filled: formFilled, total: formTotal })}
          </button>
        </Row>
      )}

      {/* brief (mig 395, M4 Autopilot task O1/O2/O3) — the "compact entry"
          twin of WorkspaceStageBoard's fuller box; same blur-save contract
          (BriefField). Read-only once the node is done/skipped — a
          permanent record of what was asked for, not editable history. */}
      <Row label={t('projects.workflow.brief.label')}>
        <BriefField
          value={node.brief ?? ''}
          disabled={!canWrite || saving || briefReadOnly}
          compact
          placeholder={t('projects.workflow.brief.placeholder')}
          onSave={(next) => void patch({ brief: next })}
          testId="workflow-node-brief"
        />
      </Row>

      {/* B5 T-B5.8: management fields (owner / members / schedule) folded into a
          collapsible "Details" block, collapsed by default so the card leads
          with the current action + completion condition and keeps the
          management surface one click away at the bottom. Every editor
          (OwnerPicker, the schedule popover) is untouched — just relocated. The
          overdue chip peeks on the toggle even while collapsed so an at-risk
          schedule stays visible without opening (and the node auto-expands when
          overdue on mount). */}
      <div className="mt-1 border-t border-line pt-1">
        <button
          type="button"
          onClick={() => setDetailsOpen((open) => !open)}
          data-testid="workflow-card-details-toggle"
          aria-expanded={detailsOpen}
          className="flex w-full items-center gap-2 py-1 text-[11px] uppercase tracking-wider text-ink-600 transition hover:text-ink-300"
        >
          <ChevronDown
            size={13}
            className={`shrink-0 transition-transform ${detailsOpen ? '' : '-rotate-90'}`}
            aria-hidden
          />
          {t('projects.workflow.details')}
          {overdue && !detailsOpen && (
            <span
              data-testid="workflow-card-overdue"
              className="ml-1 inline-flex items-center gap-1 rounded-full bg-danger-soft px-2 py-0.5 text-[10px] font-medium normal-case tracking-normal text-danger"
            >
              {t('projects.workflow.overdue')}
            </span>
          )}
        </button>

        {detailsOpen && (
          <div data-testid="workflow-card-details" className="pt-1">
            <Row label={t('projects.workflow.owner')}>
              <OwnerPicker
                mode="owner"
                people={people}
                agents={agents}
                ownerUserId={node.owner_user_id}
                ownerAgentId={node.owner_agent_id}
                disabled={!canWrite || saving}
                placeholder={t('projects.workflow.unassigned')}
                onOwnerChange={(ref) =>
                  void patch({
                    owner_user_id: ref?.user_id ?? null,
                    owner_agent_id: ref?.agent_id ?? null,
                  })
                }
              />
            </Row>

            <Row label={t('projects.workflow.members')}>
              <OwnerPicker
                mode="members"
                people={people}
                agents={agents}
                members={node.members}
                disabled={!canWrite || saving}
                onMembersChange={(members) => void patch({ members })}
              />
            </Row>

            <Row label={t('projects.workflow.schedule')}>
              <div className={overdue ? 'text-danger' : undefined} data-testid="workflow-card-schedule">
                <button
                  type="button"
                  data-testid="workflow-schedule-trigger"
                  disabled={!canWrite || saving}
                  onClick={(e) => setScheduleAnchor(e.currentTarget)}
                  className="flex h-8 w-full items-center gap-2 rounded-md border border-line px-2 text-[13px] text-ink-200 transition hover:border-line-strong disabled:opacity-50"
                >
                  <CalendarDays size={14} className="shrink-0 text-ink-500" aria-hidden />
                  <span className={`truncate ${node.planned_start ? '' : 'text-ink-500'}`}>
                    {node.planned_start
                      ? `${node.planned_start} → ${node.planned_due ?? '…'}`
                      : t('projects.workflow.setSchedule')}
                  </span>
                </button>
                <DateTimePopover
                  anchorEl={scheduleAnchor}
                  start={node.planned_start}
                  end={node.planned_due}
                  onChange={(start, dueDate) => {
                    setScheduleAnchor(null);
                    void patch({ planned_start: start, planned_due: dueDate });
                  }}
                  onClose={() => setScheduleAnchor(null)}
                />
                {overdue && (
                  <span
                    data-testid="workflow-card-overdue-expanded"
                    className="mt-1 inline-flex items-center gap-1 rounded-full bg-danger-soft px-2 py-0.5 text-[10px] font-medium uppercase tracking-wide text-danger"
                  >
                    {t('projects.workflow.overdue')}
                  </span>
                )}
              </div>
            </Row>
          </div>
        )}
      </div>

      <div className="mt-3 flex items-center gap-2 border-t border-line pt-3">
        <button
          onClick={onOpenTodolist}
          data-testid="workflow-open-todolist"
          className="inline-flex items-center gap-1.5 rounded-md border border-line px-2.5 py-1.5 text-[12.5px] text-ink-300 transition hover:border-line-strong hover:text-ink-100"
        >
          <ExternalLink size={13} /> {t('projects.workflow.openInTodolist')}
        </button>
        {/* Back/Complete-stage act on the SHARED workflow cursor — only
            meaningful for the active group. A future-node peek card
            (isActive=false) never renders these; it may show Start-early
            instead. */}
        {isActive && canWrite && (
          <>
            <button
              onClick={() => onRequestAdvance('back')}
              data-testid="workflow-back-btn"
              className="ml-auto inline-flex items-center gap-1.5 rounded-md px-2.5 py-1.5 text-[12.5px] text-ink-500 transition hover:bg-ink-800 hover:text-ink-200"
            >
              <ArrowLeft size={13} /> {t('projects.workflow.back')}
            </button>
            <button
              onClick={() => onRequestAdvance('forward')}
              data-testid="workflow-complete-stage"
              className="inline-flex items-center gap-1.5 rounded-md border px-3 py-1.5 text-[12.5px] transition"
              style={{
                background: 'var(--accent-soft)',
                color: 'var(--accent-text)',
                borderColor: 'var(--accent-border)',
              }}
            >
              {t('projects.workflow.completeStage')} <ArrowRight size={13} />
            </button>
          </>
        )}
        {/* Start early (M4 Autopilot task O2/O3) — the manual, hand-operated
            twin of the autopilot engine's own auto-start step. Only ever
            shown on a future-node peek card whose dependencies are already
            satisfied (see `canStartEarly` above — local unmetDeps, the
            server re-checks on the actual call). */}
        {canStartEarly && onStartEarly && canWrite && (
          <button
            onClick={() => onStartEarly(node.id)}
            disabled={startEarlyBusy}
            data-testid="workflow-start-early"
            className="ml-auto inline-flex items-center gap-1.5 rounded-md border px-3 py-1.5 text-[12.5px] transition disabled:cursor-not-allowed disabled:opacity-50"
            style={{
              background: 'var(--accent-soft)',
              color: 'var(--accent-text)',
              borderColor: 'var(--accent-border)',
            }}
          >
            <Rocket size={13} /> {t('projects.workflow.startEarly.button')}
          </button>
        )}
      </div>
    </div>
  );
};
