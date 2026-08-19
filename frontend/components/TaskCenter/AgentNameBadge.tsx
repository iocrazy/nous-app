import React from 'react';
import type { UnifiedTask } from '../../contexts/TaskManagerContext';
import { taskAgentName } from './taskRowPresentation';

/**
 * "Which agent ran this" — the who half of a Task Center row's attribution
 * (the title carries the user's request, the subtitle the result). Renders
 * nothing when the run's agent has no name we can show, rather than a
 * placeholder.
 *
 * Shared deliberately: a run is drawn by ActiveTaskCard while it executes and
 * by TaskCenterRow once it is terminal, so a badge added to only one of them
 * disappears the moment the run starts or finishes.
 */
export const AgentNameBadge: React.FC<{
  task: Pick<UnifiedTask, 'task_type' | 'metadata'>;
}> = ({ task }) => {
  const name = taskAgentName(task);
  if (!name) return null;
  return (
    <span
      data-testid="agent-name-badge"
      className="shrink-0 max-w-[96px] truncate rounded-full border border-agent-line bg-agent-soft px-1.5 text-[10px] leading-4 text-agent"
      title={name}
    >
      {name}
    </span>
  );
};
