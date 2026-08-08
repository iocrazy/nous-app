/**
 * WorkflowSection — the workspace Overview's workflow region (spec §5): the
 * node strip on top, then the current node's control card(s). Owns the local
 * candidate pools (project members + agents) for the card's pickers; the
 * workflow data and the advance gate itself live in the shell so a reload
 * refreshes the strip, sidebar and top bar together.
 *
 * W3-1: when the user may write, the strip grows a "+ Add stage" affordance
 * (opens the shared library picker; add from the node bank or a blank name) and
 * a per-node remove affordance (server-fenced — a 409 surfaces its reason as a
 * toast). For a No-workflow project (`has_workflow=false`) a writer gets a
 * "Set up workflow" empty-state CTA (M1.x opt-in migration path — see
 * AttachWorkflowModal); a non-writer sees nothing, same as before.
 */

import React, { useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { GitBranch } from 'lucide-react';
import { fetchProjectMembers } from '../../services/projectsService';
import { aiLibraryService } from '../../services/aiLibraryService';
import {
  addProjectNode,
  deleteProjectNode,
  fetchStageLibrary,
  startEarlyNode,
} from '../../services/workflowService';
import { ApiError } from '../../services/apiClient';
import type { ProjectStageNode, ProjectWorkflow, StageLibraryItem } from '../../types';
import { useToast } from '../Toast';
import { WorkflowStrip } from './WorkflowStrip';
import { CurrentNodeCard } from './CurrentNodeCard';
import { LibraryPickerModal } from './LibraryPickerModal';
import { AttachWorkflowModal } from './AttachWorkflowModal';
import { AgentOption, PersonOption } from './OwnerPicker';
import { isNodeInActiveGroup, unmetDeps } from './nodeStatus';

interface WorkflowSectionProps {
  projectId: string;
  /** The project's team (personal project → '' — AttachWorkflowModal's
   * fetchTemplates resolves that server-side to the owner's personal team). */
  teamId: string;
  /** Current episode id (B2 #1712) — passed to startEarlyNode so a start-early
   * scopes to this episode's frozen node; null/undefined → project-level. Note
   * addProjectNode/deleteProjectNode stay project-level (episode ownership of
   * new nodes is B3's scope, deliberately not threaded here). */
  episodeId?: string | null;
  workflow: ProjectWorkflow;
  canWrite: boolean;
  onReload: () => void;
  onRequestAdvance: (direction: 'forward' | 'back') => void;
  onOpenTodolist: () => void;
  /** Navigate to a node's dedicated Stage Board (H3 Run now chip) — threaded
   * straight through to CurrentNodeCard, which falls back to onOpenTodolist
   * when this isn't provided. */
  onOpenStage?: (nodeId: string) => void;
  /** Strip node click (B5 T-B5.3) — routes by the node's `surface` to the
   * matching creative surface (script / storyboard / renders) or its Stage
   * Board (deliverable-only). Threaded through to WorkflowStrip. */
  onSelectNode?: (node: ProjectStageNode) => void;
  /** A node the user asked to focus (from the sidebar / strip) — scroll to it. */
  focusNodeId: string | null;
}

/** Map a delete 409 reason code to user copy (server sends the code only). */
const REMOVE_BLOCK_KEY: Record<string, string> = {
  NODE_NOT_PENDING: 'projects.workflow.removeBlocked.notPending',
  NODE_HAS_ISSUE: 'projects.workflow.removeBlocked.hasIssue',
  NODE_IN_ACTIVE_GROUP: 'projects.workflow.removeBlocked.active',
};

export const WorkflowSection: React.FC<WorkflowSectionProps> = ({
  projectId,
  teamId,
  episodeId,
  workflow,
  canWrite,
  onReload,
  onRequestAdvance,
  onOpenTodolist,
  onOpenStage,
  onSelectNode,
  focusNodeId,
}) => {
  const { t } = useTranslation();
  const { addToast } = useToast();
  const [people, setPeople] = useState<PersonOption[]>([]);
  const [agents, setAgents] = useState<AgentOption[]>([]);
  const [library, setLibrary] = useState<StageLibraryItem[]>([]);
  const [pickerOpen, setPickerOpen] = useState(false);
  const [removing, setRemoving] = useState<ProjectStageNode | null>(null);
  const [busy, setBusy] = useState(false);
  // Attach-workflow (M1.x opt-in migration path) — the empty-state CTA below.
  const [attachOpen, setAttachOpen] = useState(false);
  // Start early (M4 Autopilot task O2/O3) — which future node's request is
  // currently in flight, so only THAT card's button disables (not every
  // eligible one at once).
  const [startingEarlyId, setStartingEarlyId] = useState<string | null>(null);
  const rootRef = useRef<HTMLDivElement>(null);

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

  // Load the node bank lazily the first time the picker is opened.
  useEffect(() => {
    if (!pickerOpen || library.length > 0) return;
    let alive = true;
    fetchStageLibrary()
      .then((items) => {
        if (alive) setLibrary(items);
      })
      .catch((err) => console.error('[WorkflowSection] stage library failed', err));
    return () => {
      alive = false;
    };
  }, [pickerOpen, library.length]);

  // Scroll the requested node (its card if current, else its strip capsule)
  // into view when the sidebar / strip asks to focus it.
  useEffect(() => {
    if (!focusNodeId || !rootRef.current) return;
    const el =
      rootRef.current.querySelector(`[data-testid="workflow-current-node-card"][data-node-id="${focusNodeId}"]`) ||
      rootRef.current.querySelector(`[data-node-id="${focusNodeId}"]`);
    el?.scrollIntoView({ behavior: 'smooth', block: 'center' });
  }, [focusNodeId, workflow]);

  // New nodes append after the last existing node.
  const nextSortOrder = () =>
    workflow.nodes.reduce((max, n) => Math.max(max, n.sort_order), -1) + 1;

  const doAdd = async (body: { source_stage_id?: string; name?: string }) => {
    setBusy(true);
    try {
      await addProjectNode(projectId, { ...body, sort_order: nextSortOrder() });
      setPickerOpen(false);
      onReload();
    } catch (err) {
      console.error('[WorkflowSection] add node failed', err);
      addToast(t('projects.workflow.addFailed'), 'error');
    } finally {
      setBusy(false);
    }
  };

  const confirmRemove = async () => {
    if (!removing) return;
    setBusy(true);
    try {
      await deleteProjectNode(projectId, removing.id);
      setRemoving(null);
      onReload();
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) {
        const key = REMOVE_BLOCK_KEY[err.message] ?? 'projects.workflow.removeBlocked.generic';
        addToast(t(key), 'error');
      } else {
        console.error('[WorkflowSection] remove node failed', err);
        addToast(t('projects.workflow.removeFailed'), 'error');
      }
      setRemoving(null);
    } finally {
      setBusy(false);
    }
  };

  // Start early (M4 Autopilot task O2/O3) — the manual, hand-operated twin of
  // the autopilot engine's own auto-start step (spec §3). Shares the exact
  // same error codes the server's tick uses: DEPS_PENDING (waiting_on),
  // NODE_CANCELLED, or a generic NODE_NOT_PENDING/other block.
  const handleStartEarly = async (nodeId: string) => {
    // Gate on episodeId (B6 PR-2 task 10): startEarlyNode now REQUIRES
    // episode_id (server 422s otherwise). This card only renders once
    // `workflow` is loaded (itself gated on a resolved episodeId), so this is
    // a defensive no-op rather than a reachable path in practice.
    if (!episodeId) return;
    setStartingEarlyId(nodeId);
    try {
      await startEarlyNode(projectId, nodeId, episodeId);
      onReload();
    } catch (err) {
      if (err instanceof ApiError && err.status === 422) {
        if (err.code === 'DEPS_PENDING') {
          const waitingOn =
            (err.details as { waiting_on?: string[] } | undefined)?.waiting_on ?? [];
          addToast(
            t('projects.workflow.deps.waitingOn', { names: waitingOn.join(', ') }),
            'error',
          );
        } else if (err.code === 'NODE_CANCELLED') {
          addToast(t('projects.workflow.startEarly.cancelled'), 'error');
        } else if (err.code === 'EPISODE_MISMATCH') {
          addToast(t('projects.workflow.startEarly.episodeMismatch'), 'error');
        } else {
          addToast(t('projects.workflow.startEarly.blocked'), 'error');
        }
      } else {
        console.error('[WorkflowSection] start early failed', err);
        addToast(t('projects.workflow.startEarly.failed'), 'error');
      }
    } finally {
      setStartingEarlyId(null);
    }
  };

  if (!workflow.has_workflow) {
    // A non-writer has no action to take here — same "render nothing" as
    // before this CTA existed.
    if (!canWrite) return null;
    return (
      <div
        data-testid="workflow-empty-state"
        className="flex flex-col items-start gap-2 rounded-xl border border-dashed border-line p-4"
      >
        <div className="flex items-center gap-1.5 text-[13px] font-medium text-ink-200">
          <GitBranch size={14} className="text-ink-500" />
          {t('projects.workflow.attach.emptyStateTitle')}
        </div>
        <p className="text-[12.5px] text-ink-500">
          {t('projects.workflow.attach.emptyStateBody')}
        </p>
        <button
          type="button"
          onClick={() => setAttachOpen(true)}
          data-testid="workflow-empty-state-attach"
          className="mt-1 inline-flex items-center gap-1.5 rounded-md border px-3 py-1.5 text-[12.5px] transition"
          style={{
            background: 'var(--accent-soft)',
            color: 'var(--accent-text)',
            borderColor: 'var(--accent-border)',
          }}
        >
          {t('projects.workflow.attach.emptyStateButton')}
        </button>
        {attachOpen && (
          <AttachWorkflowModal
            projectId={projectId}
            teamId={teamId}
            onClose={() => setAttachOpen(false)}
            onAttached={() => onReload()}
          />
        )}
      </div>
    );
  }

  // The active group: the current node plus any siblings in its parallel group
  // (shared predicate — see nodeStatus.ts::isNodeInActiveGroup — so this and
  // WorkspaceStageBoard's "Complete stage" gate can't silently diverge).
  const current = workflow.nodes.find((n) => n.id === workflow.current_node_id) ?? null;
  const groupNodes = workflow.nodes.filter((n) => isNodeInActiveGroup(n, current));
  // Future nodes eligible for Start early: not yet reached by the cursor,
  // still pending (never started), not skipped, and every dependency already
  // satisfied (nodeStatus.ts::unmetDeps — display-only, the server re-checks).
  const futureEligible = workflow.nodes.filter(
    (n) =>
      !isNodeInActiveGroup(n, current) &&
      n.status === 'pending' &&
      !n.skipped &&
      unmetDeps(workflow.nodes, n).length === 0,
  );

  return (
    <div ref={rootRef} data-testid="workflow-section" className="flex flex-col gap-3">
      <WorkflowStrip
        nodes={workflow.nodes}
        currentNodeId={workflow.current_node_id}
        onSelectNode={onSelectNode}
        canEdit={canWrite}
        onAddNode={() => setPickerOpen(true)}
        onRemoveNode={(node) => setRemoving(node)}
      />
      {groupNodes.map((node) => (
        <CurrentNodeCard
          key={node.id}
          projectId={projectId}
          node={node}
          canWrite={canWrite}
          people={people}
          agents={agents}
          onPatched={onReload}
          onRequestAdvance={onRequestAdvance}
          onOpenTodolist={onOpenTodolist}
          onOpenStage={onOpenStage}
        />
      ))}
      {canWrite &&
        futureEligible.map((node) => (
          <CurrentNodeCard
            key={node.id}
            projectId={projectId}
            node={node}
            canWrite={canWrite}
            people={people}
            agents={agents}
            onPatched={onReload}
            onRequestAdvance={onRequestAdvance}
            onOpenTodolist={onOpenTodolist}
            onOpenStage={onOpenStage}
            isActive={false}
            allNodes={workflow.nodes}
            onStartEarly={(id) => void handleStartEarly(id)}
            startEarlyBusy={startingEarlyId === node.id}
          />
        ))}

      {pickerOpen && (
        <LibraryPickerModal
          items={library}
          onPick={(item) => void doAdd({ source_stage_id: item.id })}
          onAddBlank={(name) => void doAdd({ name })}
          onClose={() => setPickerOpen(false)}
        />
      )}

      {removing && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm"
          onClick={() => setRemoving(null)}
        >
          <div
            role="dialog"
            aria-label="Remove stage"
            data-testid="workflow-remove-confirm"
            className="w-full max-w-sm rounded-xl border border-line-strong bg-island p-4 shadow-2xl"
            onClick={(e) => e.stopPropagation()}
          >
            <h3 className="text-sm font-semibold text-ink-100">
              {t('projects.workflow.removeTitle')}
            </h3>
            <p className="mt-1.5 text-[13px] text-ink-400">
              {t('projects.workflow.removeBody', { name: removing.name })}
            </p>
            <div className="mt-4 flex items-center justify-end gap-2">
              <button
                onClick={() => setRemoving(null)}
                className="rounded-md px-3 py-1.5 text-[12.5px] text-ink-400 hover:bg-ink-800 hover:text-ink-200"
              >
                {t('common.cancel')}
              </button>
              <button
                onClick={() => void confirmRemove()}
                disabled={busy}
                data-testid="workflow-remove-confirm-btn"
                className="rounded-md border border-rose-500/50 bg-rose-500/10 px-3 py-1.5 text-[12.5px] text-rose-300 transition hover:bg-rose-500/20 disabled:opacity-50"
              >
                {t('projects.workflow.removeConfirm')}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};
