/**
 * CanvasSurface drag-snap-connect wiring (Infinite-Canvas parity Phase 1 G1).
 *
 * Alt-dragging a single smart node so its probe point (node center; pointer
 * for prompt/loop sources) lands inside a valid target auto-creates the edge
 * dragged→target through the same validated store path as manual connect,
 * then snaps the dragged node back to its drag-start position.
 *
 * Driven through the prop-capturing React Flow stub: the real engine handlers
 * (onNodeDragStart/onNodeDrag/onNodeDragStop) run against the real store.
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
import type { CanvasKind, CanvasNode } from '../types';

afterEach(() => {
  cleanup();
  capturedProps = {};
  useCanvasCoreStore.getState().reset();
});

function seed(kind: CanvasKind, nodes: CanvasNode[]): void {
  useCanvasCoreStore.setState({ kind, nodes, connections: [], selection: [] });
}

type DragHandler = (evt: unknown, node: unknown) => void;

/** Simulate a full node drag through the captured engine handlers. */
function dragNode(args: {
  id: string;
  type: string;
  from: { x: number; y: number };
  to: { x: number; y: number };
  altKey: boolean;
  /** Pointer client coords at drop (identity → flow coords in the stub). */
  pointer?: { x: number; y: number };
}): void {
  const { id, type, from, to, altKey } = args;
  const pointer = args.pointer ?? { x: to.x, y: to.y };
  const evt = { altKey, clientX: pointer.x, clientY: pointer.y };
  act(() => {
    (capturedProps.onNodeDragStart as DragHandler)?.(evt, { id, type, position: from });
    // Mid-drag position tick so the store actually holds the moved position
    // (proves the snap-back on connect is real, not a no-op).
    (capturedProps.onNodesChange as (c: unknown[]) => void)?.([
      { id, type: 'position', position: to, dragging: true },
    ]);
    (capturedProps.onNodeDrag as DragHandler)?.(evt, { id, type, position: to });
    (capturedProps.onNodeDragStop as DragHandler)?.(evt, { id, type, position: to });
  });
}

const SHOT: CanvasNode = { id: 'shot1', type: 'shot', position: { x: 0, y: 0 }, data: {} };
// Fallback measure in jsdom is 200×120 — prompt1 occupies (600,0)–(800,120).
const PROMPT: CanvasNode = { id: 'prompt1', type: 'prompt', position: { x: 600, y: 0 }, data: {} };
const OUTPUT: CanvasNode = { id: 'out1', type: 'output', position: { x: 1200, y: 0 }, data: {} };

describe('CanvasSurface drag-snap-connect (smart)', () => {
  it('alt-dropping a shot onto a prompt creates the edge and snaps the shot back', () => {
    seed('smart', [SHOT, PROMPT]);
    render(<CanvasSurface />);

    // Dragged shot at (650,10): center (750,70) inside prompt1's rect.
    dragNode({ id: 'shot1', type: 'shot', from: { x: 0, y: 0 }, to: { x: 650, y: 10 }, altKey: true });

    const state = useCanvasCoreStore.getState();
    const conns = state.connections as Array<Record<string, unknown>>;
    expect(conns).toHaveLength(1);
    expect(conns[0].source).toBe('shot1');
    expect(conns[0].target).toBe('prompt1');
    expect(conns[0].sourceHandle).toBeNull();
    expect(conns[0].targetHandle).toBeNull();

    // Snap-back: the connect gesture must not move the node.
    const shot = (state.nodes as CanvasNode[]).find((n) => n.id === 'shot1')!;
    expect(shot.position).toEqual({ x: 0, y: 0 });
  });

  it('highlights the hovered target during a alt-drag', () => {
    seed('smart', [SHOT, PROMPT]);
    render(<CanvasSurface />);

    const evt = { altKey: true, clientX: 650, clientY: 10 };
    act(() => {
      (capturedProps.onNodeDragStart as DragHandler)?.(evt, { id: 'shot1', type: 'shot', position: { x: 0, y: 0 } });
      (capturedProps.onNodeDrag as DragHandler)?.(evt, { id: 'shot1', type: 'shot', position: { x: 650, y: 10 } });
    });

    const rfNodes = capturedProps.nodes as Array<{ id: string; className?: string }>;
    const prompt = rfNodes.find((n) => n.id === 'prompt1')!;
    expect(prompt.className ?? '').toContain('mh-snap-target');
  });

  it('does nothing without the Alt modifier', () => {
    seed('smart', [SHOT, PROMPT]);
    render(<CanvasSurface />);

    dragNode({ id: 'shot1', type: 'shot', from: { x: 0, y: 0 }, to: { x: 650, y: 10 }, altKey: false });

    expect(useCanvasCoreStore.getState().connections).toHaveLength(0);
  });

  it('refuses pairs the smart connection rules forbid (prompt onto shot)', () => {
    seed('smart', [SHOT, PROMPT]);
    render(<CanvasSurface />);

    // Pointer probe for a dragged prompt: pointer over the shot node (0,0)–(200,120).
    dragNode({
      id: 'prompt1',
      type: 'prompt',
      from: { x: 600, y: 0 },
      to: { x: 40, y: 20 },
      altKey: true,
      pointer: { x: 100, y: 60 },
    });

    expect(useCanvasCoreStore.getState().connections).toHaveLength(0);
  });

  it('uses the pointer (not the node center) as the probe for prompt sources', () => {
    seed('smart', [PROMPT, OUTPUT]);
    render(<CanvasSurface />);

    // Dragged prompt body far from out1, but the pointer is inside out1's
    // rect (1200,0)–(1400,120) — pointer probe must still connect.
    dragNode({
      id: 'prompt1',
      type: 'prompt',
      from: { x: 600, y: 0 },
      to: { x: 700, y: 400 },
      altKey: true,
      pointer: { x: 1300, y: 60 },
    });

    const conns = useCanvasCoreStore.getState().connections as Array<Record<string, unknown>>;
    expect(conns).toHaveLength(1);
    expect(conns[0].source).toBe('prompt1');
    expect(conns[0].target).toBe('out1');
  });

  it('does not duplicate an existing edge on a second snap-connect', () => {
    seed('smart', [SHOT, PROMPT]);
    render(<CanvasSurface />);

    dragNode({ id: 'shot1', type: 'shot', from: { x: 0, y: 0 }, to: { x: 650, y: 10 }, altKey: true });
    dragNode({ id: 'shot1', type: 'shot', from: { x: 0, y: 0 }, to: { x: 650, y: 10 }, altKey: true });

    expect(useCanvasCoreStore.getState().connections).toHaveLength(1);
  });

  it('ignores multi-selection drags', () => {
    seed('smart', [SHOT, PROMPT, OUTPUT]);
    useCanvasCoreStore.setState({ selection: ['shot1', 'out1'] });
    render(<CanvasSurface />);

    dragNode({ id: 'shot1', type: 'shot', from: { x: 0, y: 0 }, to: { x: 650, y: 10 }, altKey: true });

    expect(useCanvasCoreStore.getState().connections).toHaveLength(0);
  });
});

describe('CanvasSurface drag-snap-connect (classic keeps old behavior)', () => {
  it('classic mode never snap-connects', () => {
    seed('classic', [
      { id: 'img', type: 'image', position: { x: 0, y: 0 }, data: {} },
      { id: 'out', type: 'output', position: { x: 600, y: 0 }, data: {} },
    ]);
    render(<CanvasSurface />);

    dragNode({ id: 'img', type: 'image', from: { x: 0, y: 0 }, to: { x: 650, y: 10 }, altKey: true });

    expect(useCanvasCoreStore.getState().connections).toHaveLength(0);
  });
});
