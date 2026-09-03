/**
 * CanvasSurface — a read-only session must WITHDRAW its edit gestures,
 * not merely discard their results.
 *
 * PR #1820 latched `readOnly` when an autosave came back 403, which stopped
 * the PUTs but left the surface fully interactive: a viewer could drag,
 * wire, cut and delete, and the store swallowed all of it at `markDirty`.
 * The canvas looked editable and silently threw the work away. These tests
 * pin the two halves of the fix — the React Flow props that stop the
 * gesture at the source, and the handler-level default-deny behind them.
 *
 * What must SURVIVE read-only is asserted just as hard: panning/zooming,
 * selection (view-only state that never dirties the document), and React
 * Flow's measurement pass — suppressing `dimensions` would leave every node
 * `visibility:hidden`, which is the 2026-08-12 blank-canvas symptom.
 *
 * React Flow is stubbed to a prop-capturing shim (the repo's canvas-core
 * test pattern) so the real callbacks the surface wires up can be driven.
 */

import { render, cleanup, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { EdgeChange, NodeChange } from '@xyflow/react';

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
import { useKnifeStore } from '../../../canvas-kit/knifeStore';
import { useCanvasCoreStore } from '../store/canvasCoreStore';
import type { CanvasNode } from '../types';

const NODES: CanvasNode[] = [
  { id: 'a', type: 'output', position: { x: 0, y: 0 }, data: {} },
  { id: 'b', type: 'output', position: { x: 200, y: 0 }, data: {} },
];

function seed(readOnly: boolean): void {
  useCanvasCoreStore.setState({
    kind: 'smart',
    nodes: NODES.map((n) => ({ ...n })),
    connections: [{ id: 'e1', source: 'a', target: 'b' }],
    selection: [],
    readOnly,
    canvasId: '337610660408263',
    baseUpdatedAt: '2026-08-13T09:00:00+00:00',
    loadStatus: 'ready',
  });
}

function snapshot() {
  const s = useCanvasCoreStore.getState();
  return {
    revision: s.revision,
    nodes: s.nodes.map((n) => ({
      id: (n as { id: string }).id,
      position: (n as { position: { x: number; y: number } }).position,
    })),
    connectionIds: s.connections.map((c) => (c as { id: string }).id),
  };
}

afterEach(() => {
  cleanup();
  capturedProps = {};
  useKnifeStore.getState().exit();
  useCanvasCoreStore.getState().reset();
});

describe('CanvasSurface — read-only React Flow props', () => {
  it('drops node dragging and connecting, and detaches every connect handler', () => {
    seed(true);
    render(<CanvasSurface />);

    expect(capturedProps.nodesDraggable).toBe(false);
    expect(capturedProps.nodesConnectable).toBe(false);
    // `allowConnect={false}` in the engine → no commit handler, no live
    // validity hint. `allowDragCreate={false}` → no wire-drag-to-create.
    expect(capturedProps.onConnect).toBeUndefined();
    expect(capturedProps.isValidConnection).toBeUndefined();
    expect(capturedProps.onConnectStart).toBeUndefined();
    expect(capturedProps.onConnectEnd).toBeUndefined();
  });

  it('keeps every one of those live for a writable session', () => {
    seed(false);
    render(<CanvasSurface />);

    expect(capturedProps.nodesDraggable).toBe(true);
    expect(capturedProps.nodesConnectable).toBe(true);
    expect(typeof capturedProps.onConnect).toBe('function');
    expect(typeof capturedProps.isValidConnection).toBe('function');
    expect(typeof capturedProps.onConnectStart).toBe('function');
    expect(typeof capturedProps.onConnectEnd).toBe('function');
  });

  it('leaves reading intact — move, selection change and the minimap stay wired', () => {
    seed(true);
    render(<CanvasSurface />);

    // Since Task 3 the viewport reaches the store only when a gesture
    // settles; there is no per-frame `onMove` channel to keep alive.
    expect(typeof capturedProps.onMoveEnd).toBe('function');
    expect(typeof capturedProps.onSelectionChange).toBe('function');
    expect(typeof capturedProps.onNodesChange).toBe('function');
    // `paneCreateMenu` is off in read-only, which hands double-click back
    // to React Flow's zoom — a read gesture, not a create one.
    expect(capturedProps.zoomOnDoubleClick).toBe(true);
  });
});

describe('CanvasSurface — read-only change handlers are default-deny', () => {
  it('a position change (drag) neither moves the node nor dirties the document', () => {
    seed(true);
    render(<CanvasSurface />);
    const before = snapshot();

    (capturedProps.onNodesChange as (c: NodeChange[]) => void)([
      { id: 'a', type: 'position', position: { x: 999, y: 999 }, dragging: false },
    ]);

    expect(snapshot()).toEqual(before);
  });

  it('a mid-drag tick is dropped too (the drag-tick fast path must not bypass it)', () => {
    seed(true);
    render(<CanvasSurface />);
    const before = snapshot();

    (capturedProps.onNodesChange as (c: NodeChange[]) => void)([
      { id: 'a', type: 'position', position: { x: 50, y: 50 }, dragging: true },
    ]);

    expect(snapshot()).toEqual(before);
  });

  it('a node remove change is dropped', () => {
    seed(true);
    render(<CanvasSurface />);

    (capturedProps.onNodesChange as (c: NodeChange[]) => void)([
      { id: 'a', type: 'remove' },
    ]);

    expect(useCanvasCoreStore.getState().nodes).toHaveLength(2);
    expect(useCanvasCoreStore.getState().revision).toBe(0);
  });

  it('an edge remove change is dropped', () => {
    seed(true);
    render(<CanvasSurface />);

    (capturedProps.onEdgesChange as (c: EdgeChange[]) => void)([
      { id: 'e1', type: 'remove' },
    ]);

    expect(snapshot().connectionIds).toEqual(['e1']);
    expect(useCanvasCoreStore.getState().revision).toBe(0);
  });

  it('measurement STILL lands — otherwise every node stays visibility:hidden', () => {
    seed(true);
    render(<CanvasSurface />);

    (capturedProps.onNodesChange as (c: NodeChange[]) => void)([
      {
        id: 'a',
        type: 'dimensions',
        dimensions: { width: 240, height: 160 },
        setAttributes: true,
      },
    ]);

    const a = useCanvasCoreStore
      .getState()
      .nodes.find((n) => (n as { id: string }).id === 'a') as {
      measured?: { width?: number; height?: number };
    };
    expect(a.measured?.width).toBe(240);
    // Render-only: measurement is not an edit, so nothing is dirtied.
    expect(useCanvasCoreStore.getState().revision).toBe(0);
  });

  it('selection STILL works — it is how a viewer inspects a node', () => {
    seed(true);
    render(<CanvasSurface />);

    (capturedProps.onNodesChange as (c: NodeChange[]) => void)([
      { id: 'a', type: 'select', selected: true },
    ]);

    expect(useCanvasCoreStore.getState().selection).toEqual(['a']);
    expect(useCanvasCoreStore.getState().revision).toBe(0);
  });

  // The engine passes its container element into `renderOverlay`, and a ref
  // is only populated AFTER the first paint — so both cases need a second
  // render pass before the overlay can appear at all. Doing that for the
  // negative case too is what keeps it falsifiable (asserting "absent" on a
  // pass where nothing could ever render would prove nothing).
  it('the knife overlay is never mounted, even if the store says it is armed', () => {
    seed(true);
    useKnifeStore.getState().toggle();
    expect(useKnifeStore.getState().active).toBe(true);

    const { rerender } = render(<CanvasSurface />);
    rerender(<CanvasSurface />);

    expect(screen.queryByTestId('knife-overlay')).toBeNull();
  });

  it('…and it IS mounted for a writable session (the guard is the readOnly flag, not a broken overlay)', () => {
    seed(false);
    useKnifeStore.getState().toggle();

    const { rerender } = render(<CanvasSurface />);
    rerender(<CanvasSurface />);

    expect(screen.queryByTestId('knife-overlay')).not.toBeNull();
  });
});

describe('CanvasSurface — writable session still edits', () => {
  it('a position change moves the node and dirties the document', () => {
    seed(false);
    render(<CanvasSurface />);

    (capturedProps.onNodesChange as (c: NodeChange[]) => void)([
      { id: 'a', type: 'position', position: { x: 999, y: 999 }, dragging: false },
    ]);

    const s = snapshot();
    expect(s.nodes[0].position).toEqual({ x: 999, y: 999 });
    expect(s.revision).toBeGreaterThan(0);
  });
});
