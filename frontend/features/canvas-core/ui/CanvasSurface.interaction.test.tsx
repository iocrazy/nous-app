/**
 * CanvasSurface — shared canvas-kit interaction adoption (PR-C1 Task 3).
 *
 * Asserts the interaction props canvas-core adopts from canvas-kit — snap grid,
 * Shift box-select + platform additive key, alignment-guide overlay — and the
 * solo-drop alignment snap, which must commit through the store's setNodes
 * action (never bypass it). Group drops are left to the existing
 * onNodesChange → setNodes persist channel, so the snap is skipped there.
 *
 * React Flow is stubbed to a prop-capturing shim (the repo's canvas-core test
 * pattern) so we can drive the real callbacks the surface wires up.
 */

import { render, cleanup } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

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
import { GuideOverlay } from '../../../canvas-kit/GuideOverlay';
import { useCanvasCoreStore } from '../store/canvasCoreStore';
import type { CanvasKind, CanvasNode } from '../types';

afterEach(() => {
  cleanup();
  capturedProps = {};
  useCanvasCoreStore.getState().reset();
});

function seed(kind: CanvasKind, nodes: CanvasNode[], selection: string[] = []): void {
  useCanvasCoreStore.setState({ kind, nodes, connections: [], selection });
}

function findChild(type: unknown): React.ReactElement | undefined {
  const stack: unknown[] = Array.isArray(capturedProps.children)
    ? [...capturedProps.children]
    : [capturedProps.children];
  while (stack.length) {
    const node = stack.pop() as { type?: unknown; props?: { children?: unknown } } | null;
    if (!node || typeof node !== 'object') continue;
    if (node.type === type) return node as React.ReactElement;
    const kids = node.props?.children;
    if (kids != null) stack.push(...(Array.isArray(kids) ? kids : [kids]));
  }
  return undefined;
}

describe('CanvasSurface — adopted interaction props', () => {
  it('wires snap grid, Shift box-select, platform additive key, and a guide overlay', () => {
    seed('smart', [{ id: 'a', type: 'output', position: { x: 0, y: 0 }, data: {} }]);
    render(<CanvasSurface />);
    expect(capturedProps.snapGrid).toEqual([8, 8]);
    // P2-6: the smart canvas opts OUT of the drag lattice (Infinite's free
    // placement); the drop-time alignment snap covers tidiness instead.
    expect(capturedProps.snapToGrid).toBe(false);
    expect(capturedProps.selectionMode).toBe('partial');
    expect(capturedProps.selectionKeyCode).toBe('Shift');
    expect(['Meta', 'Control']).toContain(capturedProps.multiSelectionKeyCode);
    // fit-view / zoom need the live instance + per-drag guide callback.
    expect(typeof capturedProps.onInit).toBe('function');
    expect(typeof capturedProps.onNodeDrag).toBe('function');
    expect(findChild(GuideOverlay)).toBeDefined();
  });
});

describe('CanvasSurface — solo-drop alignment snap', () => {
  it('snaps a solo-dropped node to a neighbour edge via the store setNodes action', () => {
    // Node b sits at x=3; dropping a with its left edge at x=1 is within the 5px
    // tolerance, so a should snap to x=3.
    seed('smart', [
      { id: 'a', type: 'output', position: { x: 0, y: 0 }, data: {} },
      { id: 'b', type: 'output', position: { x: 3, y: 500 }, data: {} },
    ]);
    render(<CanvasSurface />);

    const dragStop = capturedProps.onNodeDragStop as (e: unknown, n: unknown) => void;
    dragStop({}, { id: 'a', position: { x: 1, y: 0 } });

    const nodes = useCanvasCoreStore.getState().nodes as Array<Record<string, unknown>>;
    const a = nodes.find((n) => n.id === 'a')!;
    expect((a.position as { x: number }).x).toBe(3);
    // b is untouched.
    const b = nodes.find((n) => n.id === 'b')!;
    expect((b.position as { x: number }).x).toBe(3);
  });

  it('skips the snap for a node dropped as part of a multi-selection', () => {
    seed(
      'smart',
      [
        { id: 'a', type: 'output', position: { x: 0, y: 0 }, data: {} },
        { id: 'b', type: 'output', position: { x: 3, y: 500 }, data: {} },
      ],
      ['a', 'b'],
    );
    render(<CanvasSurface />);

    const dragStop = capturedProps.onNodeDragStop as (e: unknown, n: unknown) => void;
    dragStop({}, { id: 'a', position: { x: 1, y: 0 } });

    // Group drop → no single-node snap; a keeps its store position (x=0).
    const nodes = useCanvasCoreStore.getState().nodes as Array<Record<string, unknown>>;
    const a = nodes.find((n) => n.id === 'a')!;
    expect((a.position as { x: number }).x).toBe(0);
  });
});

describe('CanvasSurface — onNodesChange persistence hygiene', () => {
  it('routes select/dimensions changes through the transient path (no dirty, no history)', () => {
    seed('smart', [{ id: 'a', type: 'output', position: { x: 0, y: 0 }, data: {} }]);
    render(<CanvasSurface />);
    const onNodesChange = capturedProps.onNodesChange as (c: unknown[]) => void;
    const rev0 = useCanvasCoreStore.getState().revision;

    onNodesChange([{ id: 'a', type: 'select', selected: true }]);
    expect(useCanvasCoreStore.getState().revision).toBe(rev0); // not dirtied
    expect(useCanvasCoreStore.getState().canUndo()).toBe(false); // no history

    onNodesChange([
      { id: 'a', type: 'dimensions', dimensions: { width: 100, height: 50 } },
    ]);
    expect(useCanvasCoreStore.getState().revision).toBe(rev0); // still clean
    expect(useCanvasCoreStore.getState().canUndo()).toBe(false);
  });

  it('routes a committed position change through setNodes (dirty + history base)', () => {
    seed('smart', [{ id: 'a', type: 'output', position: { x: 0, y: 0 }, data: {} }]);
    render(<CanvasSurface />);
    const onNodesChange = capturedProps.onNodesChange as (c: unknown[]) => void;
    const rev0 = useCanvasCoreStore.getState().revision;

    onNodesChange([
      { id: 'a', type: 'position', position: { x: 5, y: 5 }, dragging: false },
    ]);
    expect(useCanvasCoreStore.getState().revision).toBe(rev0 + 1); // dirtied
    expect(useCanvasCoreStore.getState().canUndo()).toBe(true); // history captured
  });
});

describe('CanvasSurface — undo baseline for a MULTI-node drag', () => {
  // `noteDragStart()` captures the pre-drag snapshot the undo stack will
  // rewind to. Solo drags got it from `onNodeDragStart`; a multi-node
  // selection drag is a DIFFERENT React Flow event pair (XYDrag dispatches
  // `onSelectionDrag*` when no single node is behind the gesture), and it
  // was wired to nothing — so the baseline was only captured at drag END,
  // i.e. mid-gesture, and Undo rewound to the positions the drag had
  // already reached rather than to where it started.
  function seedOne(): void {
    seed('smart', [{ id: 'a', type: 'output', position: { x: 0, y: 0 }, data: {} }], ['a']);
  }

  it('a selection drag start captures the pre-drag baseline', () => {
    const noteDragStart = vi.fn();
    seedOne();
    useCanvasCoreStore.setState({ noteDragStart });
    render(<CanvasSurface />);

    expect(typeof capturedProps.onSelectionDragStart).toBe('function');
    (capturedProps.onSelectionDragStart as (e: unknown, n: unknown[]) => void)(
      {},
      [{ id: 'a' }],
    );
    expect(noteDragStart).toHaveBeenCalledTimes(1);
  });

  it('a solo drag start still captures it — the two gestures agree', () => {
    const noteDragStart = vi.fn();
    seedOne();
    useCanvasCoreStore.setState({ noteDragStart });
    render(<CanvasSurface />);

    (capturedProps.onNodeDragStart as (e: unknown, n: unknown) => void)({}, { id: 'a' });
    expect(noteDragStart).toHaveBeenCalledTimes(1);
  });
});
