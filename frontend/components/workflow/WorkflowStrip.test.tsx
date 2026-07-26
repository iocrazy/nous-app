/**
 * WorkflowStrip (M2-W3) — the editing affordances and overdue styling.
 * `canEdit` reveals the "+ Add stage" capsule and a per-pending-node remove
 * button (never on the current or started nodes); an overdue node carries the
 * "Overdue" micro-tag. Selection/remove callbacks fire with the right node.
 */
import { render, screen, fireEvent } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { WorkflowStrip } from './WorkflowStrip';
import type { ProjectStageNode } from '../../types';

const FUTURE = '2999-01-01';
const PAST = '2000-01-01';

function node(over: Partial<ProjectStageNode>): ProjectStageNode {
  return {
    id: '1',
    project_id: '10',
    source_template_node_id: null,
    legacy_stage_id: null,
    name: 'Script',
    sort_order: 0,
    parallel_group: null,
    status: 'pending',
    owner_user_id: null,
    owner_agent_id: null,
    planned_start: null,
    planned_due: FUTURE,
    review_required: false,
    deliverable_required: false,
    deliverable_label: null,
    skipped: false,
    members: [],
    completion_policy: 'owner',
    events: { notify_on_arrival: true, notify_on_complete: false, suggest_agent_run: false },
    ...over,
  };
}

describe('WorkflowStrip', () => {
  it('hides the add capsule and remove buttons when not editable', () => {
    render(
      <WorkflowStrip
        nodes={[node({ id: 'a', name: 'Script' })]}
        currentNodeId={null}
      />,
    );
    expect(screen.queryByTestId('workflow-add-stage')).toBeNull();
    expect(screen.queryByTestId('workflow-remove-node')).toBeNull();
  });

  it('shows the add capsule and fires onAddNode when editable', () => {
    const onAddNode = vi.fn();
    render(
      <WorkflowStrip
        nodes={[node({ id: 'a' })]}
        currentNodeId={null}
        canEdit
        onAddNode={onAddNode}
      />,
    );
    fireEvent.click(screen.getByTestId('workflow-add-stage'));
    expect(onAddNode).toHaveBeenCalled();
  });

  it('offers remove only on pending non-current nodes', () => {
    const onRemoveNode = vi.fn();
    render(
      <WorkflowStrip
        nodes={[
          node({ id: 'cur', name: 'Current', status: 'pending' }),
          node({ id: 'pend', name: 'Later', status: 'pending' }),
          node({ id: 'started', name: 'Started', status: 'in_progress' }),
        ]}
        currentNodeId="cur"
        canEdit
        onRemoveNode={onRemoveNode}
      />,
    );
    const removeButtons = screen.getAllByTestId('workflow-remove-node');
    // Only the pending, non-current node ("Later") is removable.
    expect(removeButtons).toHaveLength(1);
    expect(removeButtons[0].getAttribute('data-node-id')).toBe('pend');
    fireEvent.click(removeButtons[0]);
    expect(onRemoveNode).toHaveBeenCalledWith(expect.objectContaining({ id: 'pend' }));
  });

  it('tags an overdue node and leaves an on-time one untagged', () => {
    render(
      <WorkflowStrip
        nodes={[
          node({ id: 'late', name: 'Late', planned_due: PAST, status: 'in_progress' }),
          node({ id: 'ok', name: 'OnTime', planned_due: FUTURE, status: 'in_progress' }),
        ]}
        currentNodeId={null}
      />,
    );
    const tags = screen.getAllByTestId('workflow-overdue-tag');
    expect(tags).toHaveLength(1);
    const late = screen.getByText('Late').closest('[data-testid="workflow-strip-node"]');
    expect(late?.getAttribute('data-overdue')).toBe('true');
  });
});
