/**
 * The shared trajectory view (harness P4 seam C): every surface that shows
 * "what the agent did, step by step" — Issue detail timeline, chat's
 * Trajectory tab, the Task Center detail — renders this one component over
 * the same folded nodes. Steps do not stack: the live step is the only
 * expanded block, finished steps are one-line summaries until clicked.
 */

import React, { useCallback, useMemo, useState } from 'react';

import type { AgentRunEvent } from '../../../types';
import { foldEvents, type TrajectoryNode } from './foldEvents';
import './nodes/builtins';
import { trajectoryNodeFor } from './nodes/registry';
import { TrajectoryRunContext } from './trajectoryRunContext';

export interface TrajectoryRendererProps {
  events: AgentRunEvent[];
  isRunning?: boolean;
  className?: string;
  /** Render nothing (not an empty frame) when there are no nodes. */
  hideWhenEmpty?: boolean;
  /** Phase 2b-1 §2: node.key → ids of runs forked at that boundary. */
  forkMarks?: Record<string, string[]>;
  /** The run these events belong to. A sub-agent card names it when it opens
   *  the child's panel — «from run #…» in that header (Task 7b defect H).
   *  Surfaces that do not track a run id leave it unset and the header falls
   *  back to a dash, as it always did. */
  runId?: string | null;
}

export const TrajectoryRenderer: React.FC<TrajectoryRendererProps> = ({
  events,
  isRunning = false,
  className = '',
  hideWhenEmpty = true,
  forkMarks,
  runId = null,
}) => {
  const nodes = useMemo(() => foldEvents(events, { isRunning }), [events, isRunning]);
  const [expanded, setExpanded] = useState<Set<string>>(() => new Set());
  const toggle = useCallback((node: TrajectoryNode) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(node.key)) next.delete(node.key);
      else next.add(node.key);
      return next;
    });
  }, []);

  if (nodes.length === 0 && hideWhenEmpty) return null;
  return (
    <TrajectoryRunContext.Provider value={runId}>
      <div className={`flex flex-col gap-0.5 ${className}`} data-testid="trajectory">
        {nodes.map((node) => {
          const View = trajectoryNodeFor(node.kind);
          if (!View) return null;
          return (
            <View key={node.key} node={node} expanded={expanded.has(node.key)} onToggle={toggle} marks={forkMarks?.[node.key]} />
          );
        })}
      </div>
    </TrajectoryRunContext.Provider>
  );
};

export default TrajectoryRenderer;
