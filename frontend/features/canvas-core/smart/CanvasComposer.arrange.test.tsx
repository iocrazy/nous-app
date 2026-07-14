// features/canvas-core/smart/CanvasComposer.arrange.test.tsx
// Composer Arrange button (G6 — Infinite's 拓扑自动整理): one click lays the
// graph out left-to-right by dependency rank through the undoable store path.

import { fireEvent, render, screen, cleanup } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (k: string, d?: string) => d ?? k }),
}));

import { CanvasComposer } from './CanvasComposer';
import { useCanvasCoreStore } from '../store/canvasCoreStore';
import type { CanvasNode } from '../types';

afterEach(() => {
  cleanup();
  useCanvasCoreStore.getState().reset();
});

describe('CanvasComposer — Arrange', () => {
  it('lays nodes out by rank and is undoable', () => {
    const NODES: CanvasNode[] = [
      { id: 'c', type: 'output', position: { x: 0, y: 0 }, data: {} },
      { id: 'a', type: 'shot', position: { x: 500, y: 500 }, data: {} },
      { id: 'b', type: 'prompt', position: { x: 100, y: 900 }, data: {} },
    ];
    useCanvasCoreStore.setState({
      kind: 'smart',
      nodes: NODES,
      connections: [
        { id: 'e1', source: 'a', target: 'b', sourceHandle: null, targetHandle: null },
        { id: 'e2', source: 'b', target: 'c', sourceHandle: null, targetHandle: null },
      ],
      selection: [],
    });
    render(<CanvasComposer />);

    fireEvent.click(screen.getByRole('button', { name: 'Arrange' }));

    const state = useCanvasCoreStore.getState();
    const pos = Object.fromEntries(
      state.nodes.map((n) => [(n as CanvasNode).id, (n as CanvasNode).position as { x: number }]),
    );
    expect(pos.a.x).toBeLessThan(pos.b.x);
    expect(pos.b.x).toBeLessThan(pos.c.x);
    // A layout pass is a user edit — it must be undoable.
    expect(state.canUndo()).toBe(true);
  });
});


describe('CanvasComposer — Arrange selected (IC 整理选中)', () => {
  it('with 2+ selected, only the selected top-level nodes move', () => {
    useCanvasCoreStore.setState({
      nodes: [
        { id: 'a', type: 'prompt', position: { x: 0, y: 0 }, data: {}, measured: { width: 100, height: 50 } },
        { id: 'b', type: 'prompt', position: { x: 0, y: 300 }, data: {}, measured: { width: 100, height: 50 } },
        { id: 'c', type: 'prompt', position: { x: 900, y: 900 }, data: {}, measured: { width: 100, height: 50 } },
      ],
      connections: [
        { id: 'e1', source: 'a', target: 'b', sourceHandle: null, targetHandle: null },
      ],
      selection: ['a', 'b'],
    } as never);
    render(<CanvasComposer />);
    fireEvent.click(screen.getByRole('button', { name: 'Arrange' }));
    const after = useCanvasCoreStore.getState().nodes as Array<{
      id: string;
      position: { x: number; y: number };
    }>;
    const c = after.find((n) => n.id === 'c')!;
    expect(c.position).toEqual({ x: 900, y: 900 }); // untouched
    const a = after.find((n) => n.id === 'a')!;
    const b = after.find((n) => n.id === 'b')!;
    // a→b laid out left-to-right on one rank
    expect(b.position.x).toBeGreaterThan(a.position.x);
  });
});
