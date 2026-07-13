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
import { DragCreateMenu } from './DragCreateMenu';
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
      { nodeId: 'gen', handleId: null, handleType: 'source' },
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
    seed('smart', [{ id: 'gen', type: 'prompt', position: { x: 0, y: 0 }, data: {} }]);
    render(<CanvasSurface />);
    dragToEmpty();
    expect(screen.getByRole('menu')).toBeTruthy();
  });

  it('creates the picked node at the drop point and auto-wires it via the store', () => {
    seed('smart', [{ id: 'gen', type: 'prompt', position: { x: 0, y: 0 }, data: {} }]);
    render(<CanvasSurface />);
    dragToEmpty();

    // Pick "Output" from the menu.
    const output = screen.getByRole('menuitem', { name: /Output/ });
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
    expect(conns[0].sourceHandle).toBeNull();
    expect(conns[0].target).toBe(created.id);
    expect(conns[0].targetHandle).toBeNull();

    // The new node is selected and the menu is dismissed.
    expect(state.selection).toEqual([created.id]);
    expect(screen.queryByRole('menu')).toBeNull();
  });

  it('closes the menu on Escape without creating a node', () => {
    seed('smart', [{ id: 'gen', type: 'prompt', position: { x: 0, y: 0 }, data: {} }]);
    render(<CanvasSurface />);
    dragToEmpty();
    expect(screen.getByRole('menu')).toBeTruthy();

    act(() => {
      fireEvent.keyDown(window, { key: 'Escape' });
    });
    expect(screen.queryByRole('menu')).toBeNull();
    expect(useCanvasCoreStore.getState().nodes).toHaveLength(1);
  });

  it('offers only node types that can legally receive the source wire', () => {
    // Dragging from a prompt → only nodes the prompt may legally feed are
    // offered. Source-only cards (Shot, Upload/media) and wireless containers
    // (Group) must not be offered (they would produce an edge the validator
    // rejects).
    seed('smart', [{ id: 'gen', type: 'prompt', position: { x: 0, y: 0 }, data: {} }]);
    render(<CanvasSurface />);
    dragToEmpty();
    expect(screen.getByRole('menuitem', { name: /Output/ })).toBeTruthy();
    expect(screen.queryByRole('menuitem', { name: /Shot/ })).toBeNull();
    expect(screen.queryByRole('menuitem', { name: /Upload/ })).toBeNull();
    expect(screen.queryByRole('menuitem', { name: /Group/ })).toBeNull();
  });
});

describe('CanvasSurface onConnect — duplicate guard', () => {
  it('does not add a second identical edge', () => {
    seed('smart', [
      { id: 'gen', type: 'prompt', position: { x: 0, y: 0 }, data: {} },
      { id: 'out', type: 'output', position: { x: 200, y: 0 }, data: {} },
    ]);
    render(<CanvasSurface />);
    const onConnect = capturedProps.onConnect as (c: unknown) => void;
    const conn = {
      source: 'gen',
      target: 'out',
      sourceHandle: null,
      targetHandle: null,
    };
    act(() => onConnect(conn));
    act(() => onConnect(conn)); // identical second attempt
    expect(useCanvasCoreStore.getState().connections).toHaveLength(1);
  });
});


describe('DragCreateMenu — lite kind (IC four cards)', () => {
  it('pane menu on a lite canvas offers only Upload/Group/Prompt/Loop', () => {
    seed('lite', []);
    render(
      <DragCreateMenu
        screenPosition={{ x: 0, y: 0 }}
        flowPosition={{ x: 0, y: 0 }}
        fromNodeId={null}
        fromHandle={null}
        onClose={() => {}}
      />,
    );
    const names = screen
      .getAllByRole('menuitem')
      .map((el) => el.textContent ?? '');
    expect(names.some((n) => n.includes('Upload'))).toBe(true);
    expect(names.some((n) => n.includes('Group'))).toBe(true);
    expect(names.some((n) => n.includes('Prompt'))).toBe(true);
    expect(names.some((n) => n.includes('Loop'))).toBe(true);
    expect(names.some((n) => n.includes('Shot'))).toBe(false);
    expect(names.some((n) => n.includes('Timeline'))).toBe(false);
    expect(names.some((n) => n.includes('Output'))).toBe(false);
  });
});
