/**
 * WorkflowStrip — the project workspace's node pipeline (spec §5). A capsule
 * chain reusing the Issues-pipeline visual language: done nodes carry an
 * emerald tick, the current node gets an accent outline with an amber pulse,
 * skipped nodes render dashed + struck through, and a parallel group stacks its
 * members in a dashed vertical box. Overdue nodes (planned_due past, not
 * done/skipped) turn rose with an "Overdue" micro-tag (W3-2). Delivery-only
 * nodes (no creative surface — `surface===null`, B5 T-B5.6) also render dashed,
 * reading as a "deliverable slot" rather than an authorable surface; the
 * resolved surface is stamped on `data-surface` (`'deliverable'` when null).
 *
 * When editing is allowed (W3-1) a trailing "+ Add stage" ghost capsule opens
 * the library picker, and each still-pending node shows a hover remove affordance
 * (the server fences whether it may actually be removed).
 */

import React, { useMemo } from 'react';
import { Check, Lock, Plus, X } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import type { ProjectStageNode } from '../../types';
import { isNodeInActiveGroup, isNodeOverdue, NODE_STATUS_CONFIG, unmetDeps } from './nodeStatus';
import { isDeliverableOnly, resolveSurface } from '../workspace/nodeSurface';

interface WorkflowStripProps {
  nodes: ProjectStageNode[];
  currentNodeId: string | null;
  /** Node-click navigation (B5 T-B5.3) — the strip is now the sole node
   * entry point (the sidebar's Stages list was removed in T-B5.6). Receives
   * the full node so the handler can route by `surface` (see nodeSurface.ts). */
  onSelectNode?: (node: ProjectStageNode) => void;
  /** Editing affordances (W3-1): trailing add capsule + per-node remove. */
  canEdit?: boolean;
  onAddNode?: () => void;
  onRemoveNode?: (node: ProjectStageNode) => void;
}

function runs(nodes: ProjectStageNode[]): { group: number | null; items: ProjectStageNode[] }[] {
  const out: { group: number | null; items: ProjectStageNode[] }[] = [];
  for (const n of nodes) {
    const last = out[out.length - 1];
    if (n.parallel_group != null && last && last.group === n.parallel_group) {
      last.items.push(n);
    } else {
      out.push({ group: n.parallel_group, items: [n] });
    }
  }
  return out;
}

export const WorkflowStrip: React.FC<WorkflowStripProps> = ({
  nodes,
  currentNodeId,
  onSelectNode,
  canEdit = false,
  onAddNode,
  onRemoveNode,
}) => {
  const grouped = useMemo(() => runs(nodes), [nodes]);
  if (nodes.length === 0) return null;

  // Dependency gate (mig 391, M3 PR-J) — local derivation, display-only (see
  // nodeStatus.ts::unmetDeps). Only the CURRENT active group (current node ±
  // its parallel-group siblings) ever needs the lock: every earlier node is
  // already done/skipped (deps trivially satisfied) and every later node
  // isn't reachable yet, so painting a lock there would be noise.
  const currentNode = nodes.find((n) => n.id === currentNodeId) ?? null;

  return (
    <div
      data-testid="workflow-strip"
      className="flex flex-wrap items-center gap-1.5 rounded-xl border border-line bg-island p-3"
    >
      {grouped.map((run, ri) => {
        const capsules = run.items.map((n) => (
          <NodeCapsule
            key={n.id}
            node={n}
            current={n.id === currentNodeId}
            locked={
              isNodeInActiveGroup(n, currentNode) && unmetDeps(nodes, n).length > 0
            }
            onClick={onSelectNode ? () => onSelectNode(n) : undefined}
            canEdit={canEdit}
            onRemove={onRemoveNode ? () => onRemoveNode(n) : undefined}
          />
        ));
        if (run.group != null) {
          return (
            <div
              key={`run${ri}`}
              className="flex flex-col gap-1 rounded-lg border border-dashed border-line-strong p-1"
              title={`Parallel group ${run.group}`}
            >
              {capsules}
            </div>
          );
        }
        return (
          <React.Fragment key={`run${ri}`}>
            {ri > 0 && <span className="h-px w-3 bg-line-strong" aria-hidden />}
            {capsules}
          </React.Fragment>
        );
      })}

      {canEdit && onAddNode && (
        <button
          type="button"
          onClick={onAddNode}
          data-testid="workflow-add-stage"
          className="inline-flex items-center gap-1 rounded-full border border-dashed border-line-strong px-3 py-1.5 text-[12.5px] text-ink-500 transition hover:border-[var(--accent-border)] hover:text-[var(--accent-text)]"
        >
          <Plus size={13} /> Add stage
        </button>
      )}
    </div>
  );
};

const NodeCapsule: React.FC<{
  node: ProjectStageNode;
  current: boolean;
  /** Dependency gate (mig 391, M3 PR-J) — this node is in the active group
   * but has an unmet dependency, so advance can't reach it yet. */
  locked?: boolean;
  onClick?: () => void;
  canEdit?: boolean;
  onRemove?: () => void;
}> = ({ node, current, locked = false, onClick, canEdit, onRemove }) => {
  const { t } = useTranslation();
  const meta = NODE_STATUS_CONFIG[node.status];
  const isDone = node.status === 'done';
  const isSkipped = node.status === 'skipped' || node.skipped;
  const overdue = isNodeOverdue(node);
  // B5 (T-B5.6): nodes with no creative surface are delivery-only slots (upload /
  // version / review, not a place you author). They render a dashed border to
  // read as "a deliverable slot, not a creative surface". `data-surface` (below)
  // stamps the resolved surface — or 'deliverable' for null — for styling/tests.
  const deliverable = isDeliverableOnly(node);
  // A remove affordance only makes sense for a still-pending, non-current node
  // (anything further along is server-fenced anyway — skip ≠ delete).
  const removable = canEdit && !!onRemove && node.status === 'pending' && !current;

  return (
    <span className="group/cap relative inline-flex">
      <button
        type="button"
        onClick={onClick}
        data-testid="workflow-strip-node"
        data-node-id={node.id}
        data-current={current ? 'true' : undefined}
        data-overdue={overdue ? 'true' : undefined}
        data-locked={locked ? 'true' : undefined}
        data-surface={resolveSurface(node) ?? 'deliverable'}
        data-deliverable={deliverable ? 'true' : undefined}
        title={locked ? t('projects.workflow.deps.lockedTitle') : undefined}
        className={`relative inline-flex items-center gap-1.5 rounded-full border px-3 py-1.5 text-[12.5px] transition ${
          current
            ? 'border-[var(--accent-border)] bg-[var(--accent-soft)] text-[var(--accent-text)]'
            : overdue
              ? 'border-rose-500/50 bg-rose-500/10 text-rose-400'
              : 'border-line text-ink-300 hover:border-line-strong'
        } ${
          isSkipped
            ? 'border-dashed line-through opacity-50'
            : deliverable
              ? 'border-dashed'
              : ''
        } ${removable ? 'pr-6' : ''}`}
      >
        {isDone ? (
          <Check size={13} className="text-emerald-500" />
        ) : (
          // Cursor dot lives INSIDE the pill, right before the node name (UI
          // polish fix #1, 2026-08-11): a separate amber dot used to float
          // outside the pill's top-left corner while this inner dot kept its
          // own status color — two dots reading as one indicator was
          // confusing. The current node's dot now just IS the warn/ochre
          // "you are here" signal (still pulsing), same as every other
          // node's dot is its own status signal.
          <span
            data-testid="workflow-node-dot"
            className={`h-2 w-2 rounded-full ${
              current ? 'bg-warn animate-pulse' : overdue ? 'bg-rose-400' : meta.dot
            }`}
            aria-hidden
          />
        )}
        <span className="truncate">{node.name}</span>
        {locked && (
          <Lock
            size={11}
            data-testid="workflow-node-locked"
            className="shrink-0 text-rose-400"
          />
        )}
        {overdue && (
          <span
            data-testid="workflow-overdue-tag"
            className="ml-0.5 rounded-full bg-rose-500/20 px-1.5 py-0.5 text-[9px] font-medium uppercase tracking-wide text-rose-300"
          >
            Overdue
          </span>
        )}
      </button>
      {removable && (
        <button
          type="button"
          onClick={onRemove}
          data-testid="workflow-remove-node"
          data-node-id={node.id}
          title="Remove stage"
          aria-label={`Remove ${node.name}`}
          className="absolute right-1 top-1/2 -translate-y-1/2 rounded-full p-0.5 text-ink-600 opacity-0 transition hover:bg-rose-500/20 hover:text-rose-400 group-hover/cap:opacity-100"
        >
          <X size={12} />
        </button>
      )}
    </span>
  );
};
