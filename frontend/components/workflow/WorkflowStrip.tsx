/**
 * WorkflowStrip — the project workspace's node pipeline (spec §5). A capsule
 * chain reusing the Issues-pipeline visual language: done nodes carry an
 * emerald tick, the current node gets an accent outline with an amber pulse,
 * skipped nodes render dashed + struck through, and a parallel group stacks its
 * members in a dashed vertical box. Overdue nodes (planned_due past, not
 * done/skipped) turn rose with an "Overdue" micro-tag (W3-2).
 *
 * When editing is allowed (W3-1) a trailing "+ Add stage" ghost capsule opens
 * the library picker, and each still-pending node shows a hover remove affordance
 * (the server fences whether it may actually be removed).
 */

import React, { useMemo } from 'react';
import { Check, Plus, X } from 'lucide-react';
import type { ProjectStageNode } from '../../types';
import { isNodeOverdue, NODE_STATUS_CONFIG } from './nodeStatus';

interface WorkflowStripProps {
  nodes: ProjectStageNode[];
  currentNodeId: string | null;
  onSelectNode?: (nodeId: string) => void;
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
            onClick={onSelectNode ? () => onSelectNode(n.id) : undefined}
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
  onClick?: () => void;
  canEdit?: boolean;
  onRemove?: () => void;
}> = ({ node, current, onClick, canEdit, onRemove }) => {
  const meta = NODE_STATUS_CONFIG[node.status];
  const isDone = node.status === 'done';
  const isSkipped = node.status === 'skipped' || node.skipped;
  const overdue = isNodeOverdue(node);
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
        className={`relative inline-flex items-center gap-1.5 rounded-full border px-3 py-1.5 text-[12.5px] transition ${
          current
            ? 'border-[var(--accent-border)] bg-[var(--accent-soft)] text-[var(--accent-text)]'
            : overdue
              ? 'border-rose-500/50 bg-rose-500/10 text-rose-400'
              : 'border-line text-ink-300 hover:border-line-strong'
        } ${isSkipped ? 'border-dashed line-through opacity-50' : ''} ${removable ? 'pr-6' : ''}`}
      >
        {current && (
          <span
            aria-hidden
            className="absolute -left-1 -top-1 h-2 w-2 animate-pulse rounded-full bg-amber-400"
          />
        )}
        {isDone ? (
          <Check size={13} className="text-emerald-500" />
        ) : (
          <span
            className={`h-2 w-2 rounded-full ${overdue ? 'bg-rose-400' : meta.dot}`}
            aria-hidden
          />
        )}
        <span className="truncate">{node.name}</span>
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
