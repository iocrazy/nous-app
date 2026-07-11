// features/canvas-core/smart/edges/SmartEdgeView.test.tsx
// Edge midpoint scissors (Infinite-Canvas parity G6 — smart canvas conn-cut):
// a selected edge shows a cut button at its midpoint; clicking removes the
// connection from the store (a user edit — undoable).

import { ReactFlowProvider, type EdgeProps } from '@xyflow/react';
import { fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';

import { useCanvasCoreStore } from '../../store/canvasCoreStore';
import { SmartEdgeView } from './SmartEdgeView';

const EDGE_PROPS = {
  id: 'e1',
  sourceX: 0,
  sourceY: 0,
  targetX: 200,
  targetY: 100,
  sourcePosition: 'right',
  targetPosition: 'left',
  source: 'a',
  target: 'b',
} as const;

function renderEdge(selected: boolean) {
  return render(
    <ReactFlowProvider>
      <svg>
        <SmartEdgeView {...(EDGE_PROPS as unknown as EdgeProps)} selected={selected} />
      </svg>
    </ReactFlowProvider>,
  );
}

afterEach(() => useCanvasCoreStore.getState().reset());

describe('SmartEdgeView scissors', () => {
  it('shows the cut button when the edge is selected', () => {
    renderEdge(true);
    expect(screen.getByLabelText('Cut connection')).toBeTruthy();
  });

  it('keeps the cut button present but dimmed when not selected (P1-8)', () => {
    renderEdge(false);
    const btn = screen.getByLabelText('Cut connection');
    // Always rendered (Infinite's conn-cut); CSS dims it to .55 until
    // hover/selection — the active class only rides on selected edges.
    expect(btn.className).toContain('mh-edge-cut');
    expect(btn.className).not.toContain('mh-edge-cut--active');
  });

  it('marks the selected edge scissors active', () => {
    renderEdge(true);
    expect(screen.getByLabelText('Cut connection').className).toContain('mh-edge-cut--active');
  });

  it('clicking the scissors removes the connection from the store', () => {
    useCanvasCoreStore.setState({
      kind: 'smart',
      nodes: [],
      connections: [
        { id: 'e1', source: 'a', target: 'b', sourceHandle: null, targetHandle: null },
        { id: 'e2', source: 'b', target: 'c', sourceHandle: null, targetHandle: null },
      ],
      selection: [],
    });
    renderEdge(true);
    fireEvent.click(screen.getByLabelText('Cut connection'));

    const conns = useCanvasCoreStore.getState().connections as Array<Record<string, unknown>>;
    expect(conns.map((c) => c.id)).toEqual(['e2']);
  });
});
