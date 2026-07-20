/**
 * WorkflowSection — the workspace Overview's workflow region (spec §5): the
 * node strip on top, then the current node's control card(s). Owns the local
 * candidate pools (project members + agents) for the card's pickers; the
 * workflow data and the advance gate itself live in the shell so a reload
 * refreshes the strip, sidebar and top bar together.
 *
 * Renders nothing for a No-workflow project (`has_workflow=false`).
 */

import React, { useEffect, useRef, useState } from 'react';
import { fetchProjectMembers } from '../../services/projectsService';
import { aiLibraryService } from '../../services/aiLibraryService';
import type { ProjectWorkflow } from '../../types';
import { WorkflowStrip } from './WorkflowStrip';
import { CurrentNodeCard } from './CurrentNodeCard';
import { AgentOption, PersonOption } from './OwnerPicker';

interface WorkflowSectionProps {
  projectId: string;
  workflow: ProjectWorkflow;
  canWrite: boolean;
  onReload: () => void;
  onRequestAdvance: (direction: 'forward' | 'back') => void;
  onOpenTodolist: () => void;
  /** A node the user asked to focus (from the sidebar / strip) — scroll to it. */
  focusNodeId: string | null;
}

export const WorkflowSection: React.FC<WorkflowSectionProps> = ({
  projectId,
  workflow,
  canWrite,
  onReload,
  onRequestAdvance,
  onOpenTodolist,
  focusNodeId,
}) => {
  const [people, setPeople] = useState<PersonOption[]>([]);
  const [agents, setAgents] = useState<AgentOption[]>([]);
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

  // Scroll the requested node (its card if current, else its strip capsule)
  // into view when the sidebar / strip asks to focus it.
  useEffect(() => {
    if (!focusNodeId || !rootRef.current) return;
    const el =
      rootRef.current.querySelector(`[data-testid="workflow-current-node-card"][data-node-id="${focusNodeId}"]`) ||
      rootRef.current.querySelector(`[data-node-id="${focusNodeId}"]`);
    el?.scrollIntoView({ behavior: 'smooth', block: 'center' });
  }, [focusNodeId, workflow]);

  if (!workflow.has_workflow) return null;

  // The active group: the current node plus any siblings in its parallel group.
  const current = workflow.nodes.find((n) => n.id === workflow.current_node_id) ?? null;
  const groupNodes = current
    ? current.parallel_group != null
      ? workflow.nodes.filter((n) => n.parallel_group === current.parallel_group)
      : [current]
    : [];

  return (
    <div ref={rootRef} data-testid="workflow-section" className="flex flex-col gap-3">
      <WorkflowStrip
        nodes={workflow.nodes}
        currentNodeId={workflow.current_node_id}
        onSelectNode={() => undefined}
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
        />
      ))}
    </div>
  );
};
