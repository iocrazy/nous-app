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

export interface TrajectoryRendererProps {
  events: AgentRunEvent[];
  isRunning?: boolean;
  className?: string;
  /** Render nothing (not an empty frame) when there are no nodes. */
  hideWhenEmpty?: boolean;
  /** Phase 2b-1 §2: node.key → ids of runs forked at that boundary. */
  forkMarks?: Record<string, string[]>;
}

export const TrajectoryRenderer: React.FC<TrajectoryRendererProps> = ({
  events,
  isRunning = false,
  className = '',
  hideWhenEmpty = true,
  forkMarks,
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
    <div className={`flex flex-col gap-0.5 ${className}`} data-testid="trajectory">
      {nodes.map((node) => {
        const View = trajectoryNodeFor(node.kind);
        if (!View) return null;
        return (
          <View key={node.key} node={node} expanded={expanded.has(node.key)} onToggle={toggle} marks={forkMarks?.[node.key]} />
        );
      })}
    </div>
  );
};

export default TrajectoryRenderer;
