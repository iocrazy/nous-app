/**
 * WorkflowStrip (M2-W3) — the editing affordances and overdue styling.
 * `canEdit` reveals the "+ Add stage" capsule and a per-pending-node remove
 * button (never on the current or started nodes); an overdue node carries the
 * "Overdue" micro-tag. Selection/remove callbacks fire with the right node.
 */
import { render, screen, fireEvent, within } from '@testing-library/react';
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

  // UI polish fix #1 (2026-08-11): the cursor dot used to be a SEPARATE
  // amber dot floating outside the pill's top-left corner, on top of the
  // pill's own inner status dot — two dots reading as one indicator. Now the
  // current node's inner dot itself takes the warn/ochre color; there is no
  // second floating dot.
  it("merges the cursor indicator into the pill's own dot (no floating dot, warn-colored inner dot)", () => {
    render(
      <WorkflowStrip
        nodes={[node({ id: 'cur', name: 'Current', status: 'in_progress' })]}
        currentNodeId="cur"
      />,
    );
    const pill = screen.getByTestId('workflow-strip-node');
    expect(pill.getAttribute('data-current')).toBe('true');
    const dot = screen.getByTestId('workflow-node-dot');
    expect(dot.className).toContain('bg-warn');
    expect(dot.className).toContain('animate-pulse');
    // No separate absolutely-positioned floating dot sibling of the pill.
    const wrapper = pill.parentElement as HTMLElement;
    expect(wrapper.querySelectorAll('.animate-pulse')).toHaveLength(1);
  });

  // M3 PR-J (task J3): a lock icon on a current-active-group capsule blocked
  // by an unmet dependency — local derivation (nodeStatus.ts::unmetDeps),
  // display-only (the server's DEPS_PENDING predicate is the real gate).
  describe('dependency lock (M3 PR-J)', () => {
    it('locks the current node when its dependency is not yet done', () => {
      render(
        <WorkflowStrip
          nodes={[
            node({ id: 'script', name: 'Script', status: 'pending' }),
            node({ id: 'storyboard', name: 'Storyboard', status: 'pending', depends_on: ['script'] }),
          ]}
          currentNodeId="storyboard"
        />,
      );
      const storyboard = screen.getByText('Storyboard').closest('[data-testid="workflow-strip-node"]');
      expect(storyboard?.getAttribute('data-locked')).toBe('true');
      expect(within(storyboard as HTMLElement).getByTestId('workflow-node-locked')).toBeInTheDocument();
    });

    it('does not lock the current node once its dependency is done', () => {
      render(
        <WorkflowStrip
          nodes={[
            node({ id: 'script', name: 'Script', status: 'done' }),
            node({ id: 'storyboard', name: 'Storyboard', status: 'pending', depends_on: ['script'] }),
          ]}
          currentNodeId="storyboard"
        />,
      );
      const storyboard = screen.getByText('Storyboard').closest('[data-testid="workflow-strip-node"]');
      expect(storyboard?.getAttribute('data-locked')).toBeNull();
      expect(screen.queryByTestId('workflow-node-locked')).toBeNull();
    });

    it('never locks a node outside the active group, even with an unmet dependency', () => {
      render(
        <WorkflowStrip
          nodes={[
            node({ id: 'script', name: 'Script', status: 'pending' }),
            node({ id: 'storyboard', name: 'Storyboard', status: 'pending' }),
            node({ id: 'editing', name: 'Editing', status: 'pending', depends_on: ['script'] }),
          ]}
          currentNodeId="storyboard"
        />,
      );
      // "Editing" isn't the current node (nor a parallel-group sibling of
      // it), so its unmet dep on "Script" is never painted.
      expect(screen.queryByTestId('workflow-node-locked')).toBeNull();
    });
  });
});
