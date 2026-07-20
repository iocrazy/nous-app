/**
 * WorkflowStrip — the project workspace's node pipeline (spec §5). A capsule
 * chain reusing the Issues-pipeline visual language: done nodes carry an
 * emerald tick, the current node gets an accent outline with an amber pulse,
 * skipped nodes render dashed + struck through, and a parallel group stacks its
 * members in a dashed vertical box. Pure display + a select callback.
 */

import React, { useMemo } from 'react';
import { Check } from 'lucide-react';
import type { ProjectStageNode } from '../../types';
import { NODE_STATUS_CONFIG } from './nodeStatus';

interface WorkflowStripProps {
  nodes: ProjectStageNode[];
  currentNodeId: string | null;
  onSelectNode?: (nodeId: string) => void;
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
    </div>
  );
};

const NodeCapsule: React.FC<{
  node: ProjectStageNode;
  current: boolean;
  onClick?: () => void;
}> = ({ node, current, onClick }) => {
  const meta = NODE_STATUS_CONFIG[node.status];
  const isDone = node.status === 'done';
  const isSkipped = node.status === 'skipped' || node.skipped;

  return (
    <button
      type="button"
      onClick={onClick}
      data-testid="workflow-strip-node"
      data-node-id={node.id}
      data-current={current ? 'true' : undefined}
      className={`relative inline-flex items-center gap-1.5 rounded-full border px-3 py-1.5 text-[12.5px] transition ${
        current
          ? 'border-[var(--accent-border)] bg-[var(--accent-soft)] text-[var(--accent-text)]'
          : 'border-line text-ink-300 hover:border-line-strong'
      } ${isSkipped ? 'border-dashed line-through opacity-50' : ''}`}
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
        <span className={`h-2 w-2 rounded-full ${meta.dot}`} aria-hidden />
      )}
      <span className="truncate">{node.name}</span>
    </button>
  );
};
