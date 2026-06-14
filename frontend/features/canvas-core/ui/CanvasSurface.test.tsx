/**
 * CanvasSurface `onConnect` tests (review FIX 1).
 *
 * The critical, previously-untested behavior: dragging a wire in the UI must
 * commit a VALIDATED edge to the store. Before this, canvas-core had no
 * `onConnect`, so the typed-port validation + cascade topology were
 * unreachable for UI-built graphs.
 *
 * We mock React Flow down to a prop-capturing stub so we can invoke the real
 * `onConnect` callback the surface wires up, then assert what lands in the
 * store. The validator path (`validateCanvasConnection`) is exercised for
 * real — classic typed-port AND smart node-type rules.
 */

import { render, cleanup } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { Connection } from '@xyflow/react';

// Capture the props React Flow is rendered with so we can drive `onConnect`.
let capturedProps: Record<string, unknown> = {};
vi.mock('@xyflow/react', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@xyflow/react')>();
  return {
    ...actual,
    // Return null so the children (Background/Controls, which need provider
    // context) never mount — we only care about the props.
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

function seedStore(kind: CanvasKind, nodes: CanvasNode[]): void {
  useCanvasCoreStore.setState({ kind, nodes, connections: [] });
}

function onConnect(connection: Connection): void {
  (capturedProps.onConnect as (c: Connection) => void)(connection);
}

describe('CanvasSurface onConnect — classic typed-port commit', () => {
  it('appends a VALID typed-port connection with the right source/target/handles', () => {
    seedStore('classic', [
      { id: 'gen', type: 'image_gen', position: { x: 0, y: 0 }, data: {} },
      { id: 'out', type: 'output', position: { x: 300, y: 0 }, data: {} },
    ]);
    render(<CanvasSurface />);

    onConnect({
      source: 'gen',
      target: 'out',
      sourceHandle: 'image-out',
      targetHandle: 'image-in',
    });

    const conns = useCanvasCoreStore.getState().connections;
    expect(conns).toHaveLength(1);
    const edge = conns[0] as Record<string, unknown>;
    expect(edge.source).toBe('gen');
    expect(edge.target).toBe('out');
    expect(edge.sourceHandle).toBe('image-out');
    expect(edge.targetHandle).toBe('image-in');
    expect(typeof edge.id).toBe('string');
    expect(edge.id as string).toMatch(/^edge-/);
  });

  it('drops an INVALID type-mismatch connection (nothing appended)', () => {
    seedStore('classic', [
      // llm emits `text`; output only accepts `image` → mismatch.
      { id: 'llm', type: 'llm', position: { x: 0, y: 0 }, data: {} },
      { id: 'out', type: 'output', position: { x: 300, y: 0 }, data: {} },
    ]);
    render(<CanvasSurface />);

    onConnect({
      source: 'llm',
      target: 'out',
      sourceHandle: 'text-out',
      targetHandle: 'image-in',
    });

    expect(useCanvasCoreStore.getState().connections).toHaveLength(0);
  });

  it('drops a self-loop (source === target)', () => {
    seedStore('classic', [
      { id: 'gen', type: 'image_gen', position: { x: 0, y: 0 }, data: {} },
    ]);
    render(<CanvasSurface />);

    onConnect({
      source: 'gen',
      target: 'gen',
      sourceHandle: 'image-out',
      targetHandle: 'image-in',
    });

    expect(useCanvasCoreStore.getState().connections).toHaveLength(0);
  });
});

describe('CanvasSurface onConnect — smart-mode commit (gained additively)', () => {
  it('appends a valid smart connection (shot → prompt)', () => {
    seedStore('smart', [
      { id: 's1', type: 'shot', position: { x: 0, y: 0 }, data: {} },
      { id: 'p1', type: 'prompt', position: { x: 300, y: 0 }, data: {} },
    ]);
    render(<CanvasSurface />);

    onConnect({ source: 's1', target: 'p1', sourceHandle: null, targetHandle: null });

    const conns = useCanvasCoreStore.getState().connections;
    expect(conns).toHaveLength(1);
    const edge = conns[0] as Record<string, unknown>;
    expect(edge.source).toBe('s1');
    expect(edge.target).toBe('p1');
  });

  it('drops an invalid smart connection (output is terminal → cannot be a source)', () => {
    seedStore('smart', [
      { id: 'o1', type: 'output', position: { x: 0, y: 0 }, data: {} },
      { id: 'p1', type: 'prompt', position: { x: 300, y: 0 }, data: {} },
    ]);
    render(<CanvasSurface />);

    onConnect({ source: 'o1', target: 'p1', sourceHandle: null, targetHandle: null });

    expect(useCanvasCoreStore.getState().connections).toHaveLength(0);
  });
});
