/**
 * Run-state edge colouring wiring (Infinite-Canvas parity Phase 1 G2).
 *
 * In smart mode the edges CanvasSurface hands to the engine carry a
 * run-state className derived from the adjacent prompt's run_status, so a
 * cascade run visibly flows along the wires.
 */

import { render, cleanup, act } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (k: string, d?: string) => d ?? k }),
}));

let capturedProps: Record<string, unknown> = {};
vi.mock('@xyflow/react', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@xyflow/react')>();
  return {
    ...actual,
    ReactFlow: (props: Record<string, unknown>) => {
      capturedProps = props;
      return null;
    },
  };
});

import { CanvasSurface } from './CanvasSurface';
import { useCanvasCoreStore } from '../store/canvasCoreStore';
import type { CanvasNode } from '../types';

afterEach(() => {
  cleanup();
  capturedProps = {};
  useCanvasCoreStore.getState().reset();
});

const NODES: CanvasNode[] = [
  { id: 'shot1', type: 'shot', position: { x: 0, y: 0 }, data: {} },
  { id: 'p1', type: 'prompt', position: { x: 300, y: 0 }, data: { run_status: 'running' } },
  { id: 'out1', type: 'output', position: { x: 600, y: 0 }, data: {} },
];
const CONNS = [
  { id: 'e1', source: 'shot1', target: 'p1', sourceHandle: null, targetHandle: null },
  { id: 'e2', source: 'p1', target: 'out1', sourceHandle: null, targetHandle: null },
];

describe('CanvasSurface run-state edge colouring', () => {
  it('smart edges carry the run-state class of the adjacent prompt', () => {
    useCanvasCoreStore.setState({
      kind: 'smart',
      nodes: NODES,
      connections: CONNS,
      selection: [],
    });
    render(<CanvasSurface />);

    const edges = capturedProps.edges as Array<{ id: string; className?: string }>;
    expect(edges.find((e) => e.id === 'e1')?.className).toContain('mh-edge-active');
    expect(edges.find((e) => e.id === 'e2')?.className).toContain('mh-edge-active');
  });

  it('keeps edge identity stable across drag ticks when statuses are unchanged', () => {
    useCanvasCoreStore.setState({
      kind: 'smart',
      nodes: NODES,
      connections: CONNS,
      selection: [],
    });
    render(<CanvasSurface />);
    const before = capturedProps.edges;

    // A mid-drag position tick swaps the nodes array identity but not any
    // run_status — the edges array must NOT be rebuilt (RF re-renders every
    // EdgeWrapper when edge identities churn, O(E) per drag tick).
    const moved = NODES.map((n) =>
      n.id === 'shot1' ? { ...n, position: { x: 10, y: 10 } } : n,
    );
    act(() => {
      useCanvasCoreStore.getState().setNodesDragTick(moved);
    });

    expect(capturedProps.edges).toBe(before);
  });

  it('never persists the decoration className into the store', () => {
    useCanvasCoreStore.setState({
      kind: 'smart',
      nodes: NODES,
      connections: CONNS,
      selection: [],
    });
    render(<CanvasSurface />);

    // An edge-level change (select) routes decorated rfEdges back through
    // applyEdgeChanges → setConnections; the view-only className/selected
    // must be stripped or they end up in connections_json (stale run state
    // in the DB row + byte-level divergence between collaborators).
    act(() => {
      (capturedProps.onEdgesChange as (c: unknown[]) => void)?.([
        { id: 'e1', type: 'select', selected: true },
      ]);
    });

    const conns = useCanvasCoreStore.getState().connections as Array<Record<string, unknown>>;
    for (const c of conns) {
      expect(c.className).toBeUndefined();
      expect(c.selected).toBeUndefined();
    }
  });

  it('idle prompts leave edges undecorated', () => {
    useCanvasCoreStore.setState({
      kind: 'smart',
      nodes: [
        { id: 'shot1', type: 'shot', position: { x: 0, y: 0 }, data: {} },
        { id: 'p1', type: 'prompt', position: { x: 300, y: 0 }, data: { run_status: 'idle' } },
      ],
      connections: [
        { id: 'e1', source: 'shot1', target: 'p1', sourceHandle: null, targetHandle: null },
      ],
      selection: [],
    });
    render(<CanvasSurface />);

    const edges = capturedProps.edges as Array<{ id: string; className?: string }>;
    expect(edges[0].className ?? '').not.toContain('mh-edge');
  });
});
