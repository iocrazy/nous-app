/**
 * CanvasSurface drag-to-create wiring (PR-C2).
 *
 * Drives the onConnectStart/onConnectEnd handlers the surface spreads onto
 * React Flow (captured via the prop-capturing stub). With no measured handles
 * in jsdom, a wire dropped in empty canvas opens the create menu; picking a
 * node type creates the node AND auto-wires it — both through the store actions.
 */

import { render, screen, fireEvent, cleanup, act } from '@testing-library/react';
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

function dragToEmpty(): void {
  act(() => {
    (capturedProps.onConnectStart as (e: unknown, p: unknown) => void)(
      {},
      { nodeId: 'gen', handleId: 'image-out', handleType: 'source' },
    );
    // The release point now comes from the pointer event's client coords (the
    // engine converts them via screenToFlowPosition; with no measured instance
    // in this stub, that conversion is identity). `to` is ignored.
    (capturedProps.onConnectEnd as (e: unknown, s: unknown) => void)(
      { clientX: 240, clientY: 160 },
      { isValid: false, to: { x: -1, y: -1 } },
    );
  });
}

describe('CanvasSurface drag-to-create', () => {
  it('opens the create menu when a wire is dropped in empty canvas', () => {
    seed('classic', [{ id: 'gen', type: 'image', position: { x: 0, y: 0 }, data: {} }]);
    render(<CanvasSurface />);
    dragToEmpty();
    expect(screen.getByRole('menu')).toBeTruthy();
  });

  it('creates the picked node at the drop point and auto-wires it via the store', () => {
    seed('classic', [{ id: 'gen', type: 'image', position: { x: 0, y: 0 }, data: {} }]);
    render(<CanvasSurface />);
    dragToEmpty();

    // Pick "Output" from the menu.
    const output = screen.getByRole('menuitem', { name: 'Output' });
    act(() => fireEvent.click(output));

    const state = useCanvasCoreStore.getState();
    const nodes = state.nodes as Array<Record<string, unknown>>;
    expect(nodes).toHaveLength(2);
    const created = nodes.find((n) => n.type === 'output')!;
    expect(created).toBeTruthy();
    expect((created.position as { x: number; y: number })).toEqual({ x: 240, y: 160 });

    const conns = state.connections as Array<Record<string, unknown>>;
    expect(conns).toHaveLength(1);
    expect(conns[0].source).toBe('gen');
    expect(conns[0].sourceHandle).toBe('image-out');
    expect(conns[0].target).toBe(created.id);
    expect(conns[0].targetHandle).toBe('image-in');

    // The new node is selected and the menu is dismissed.
    expect(state.selection).toEqual([created.id]);
    expect(screen.queryByRole('menu')).toBeNull();
  });

  it('closes the menu on Escape without creating a node', () => {
    seed('classic', [{ id: 'gen', type: 'image', position: { x: 0, y: 0 }, data: {} }]);
    render(<CanvasSurface />);
    dragToEmpty();
    expect(screen.getByRole('menu')).toBeTruthy();

    act(() => {
      fireEvent.keyDown(window, { key: 'Escape' });
    });
    expect(screen.queryByRole('menu')).toBeNull();
    expect(useCanvasCoreStore.getState().nodes).toHaveLength(1);
  });
});
