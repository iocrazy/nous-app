/**
 * Phase 6b — Minimap + viewport virtualization tests.
 *
 * Strategy: mirror the approach in `CanvasSurface.test.tsx` — mock ReactFlow
 * to capture the props it receives, then assert:
 *   6b.1  `onlyRenderVisibleElements` is wired (truthy in captured props)
 *   6b.2  A `<MiniMap>` element appears in the children, pannable + zoomable
 *   6b.2  The `nodeColor` callback returns INK_ACCENT for selected ids and
 *         INK_MUTED for unselected ones
 *
 * jsdom limitation — React Flow's viewport-culling (`onlyRenderVisibleElements`)
 * requires real browser layout; jsdom returns 0 for all element dimensions so
 * no nodes are ever genuinely culled in this environment. The prop-presence
 * assertion in 6b.1 is the practical regression guard; real culling behaviour
 * is verified manually in the browser or via a Playwright test.
 */

import React from 'react';
import { render, cleanup } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { MiniMap } from '@xyflow/react';

// Capture every prop set that ReactFlow is rendered with.  ReactFlow returns
// null so the children (Background, Controls, MiniMap) never mount to the
// DOM — we inspect them as React elements via capturedProps.children instead.
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

// Color literals that CanvasSurface.tsx declares at module level.
// If they drift, the nodeColor end-to-end assertion in the last suite will
// catch it without requiring us to export the constants.
const INK_ACCENT = '#6366f1'; // --color-accent (indigo-500) — selected nodes
const INK_MUTED = '#52525b';  // dark-mode --ink-600           — unselected nodes

afterEach(() => {
  cleanup();
  capturedProps = {};
  useCanvasCoreStore.getState().reset();
});

function setupStore(opts?: { nodes?: object[]; selection?: string[] }): void {
  useCanvasCoreStore.setState({
    canvasId: '4242',
    loadStatus: 'ready',
    kind: 'smart',
    nodes: (opts?.nodes ?? []) as never[],
    connections: [],
    selection: opts?.selection ?? [],
    baseUpdatedAt: '2026-06-10T12:00:00+00:00',
  });
}

/** Walk capturedProps.children and return the first ReactElement whose type
 *  matches the given component. */
function findChild<P>(type: React.ComponentType<P>): React.ReactElement<P> | undefined {
  const children = React.Children.toArray(capturedProps.children as React.ReactNode);
  return children.find(
    (c): c is React.ReactElement<P> => React.isValidElement(c) && c.type === type,
  );
}

// ── 6b.1 — onlyRenderVisibleElements ──────────────────────────────────────

describe('6b.1 — onlyRenderVisibleElements prop', () => {
  it('is truthy in the ReactFlow props (viewport culling is wired)', () => {
    setupStore();
    render(<CanvasSurface />);
    expect(capturedProps.onlyRenderVisibleElements).toBe(true);
  });
});

// ── 6b.2 — MiniMap in children ────────────────────────────────────────────

describe('6b.2 — MiniMap presence and props', () => {
  it('passes a MiniMap element as a child of ReactFlow', () => {
    setupStore();
    render(<CanvasSurface />);
    expect(findChild(MiniMap)).toBeDefined();
  });

  it('MiniMap is positioned bottom-left', () => {
    setupStore();
    render(<CanvasSurface />);
    const el = findChild(MiniMap) as React.ReactElement<{ position?: string }>;
    expect(el.props.position).toBe('bottom-left');
  });

  it('MiniMap is pannable', () => {
    setupStore();
    render(<CanvasSurface />);
    const el = findChild(MiniMap) as React.ReactElement<{ pannable?: boolean }>;
    expect(el.props.pannable).toBe(true);
  });

  it('MiniMap is zoomable', () => {
    setupStore();
    render(<CanvasSurface />);
    const el = findChild(MiniMap) as React.ReactElement<{ zoomable?: boolean }>;
    expect(el.props.zoomable).toBe(true);
  });
});

// ── 6b.2 — nodeColor logic ────────────────────────────────────────────────

describe('6b.2 — nodeColor pure-logic unit tests', () => {
  it('returns INK_ACCENT for a selected node id', () => {
    const sel = new Set(['a', 'b']);
    const nodeColor = (n: { id: string }) => (sel.has(n.id) ? INK_ACCENT : INK_MUTED);
    expect(nodeColor({ id: 'a' })).toBe(INK_ACCENT);
    expect(nodeColor({ id: 'b' })).toBe(INK_ACCENT);
  });

  it('returns INK_MUTED for an unselected node id', () => {
    const sel = new Set(['a']);
    const nodeColor = (n: { id: string }) => (sel.has(n.id) ? INK_ACCENT : INK_MUTED);
    expect(nodeColor({ id: 'z' })).toBe(INK_MUTED);
  });

  it('returns INK_MUTED for every node when selection is empty', () => {
    const sel = new Set<string>();
    const nodeColor = (n: { id: string }) => (sel.has(n.id) ? INK_ACCENT : INK_MUTED);
    expect(nodeColor({ id: 'any' })).toBe(INK_MUTED);
  });
});

// ── 6b.2 — nodeColor end-to-end via wired component ──────────────────────
// Calls the actual `nodeColor` callback that CanvasSurface wires into MiniMap
// (captured from capturedProps.children) to confirm it reads from the real
// selectionSet and maps to the correct ink tokens.

describe('6b.2 — nodeColor wired end-to-end through MiniMap props', () => {
  it('selected node gets INK_ACCENT, unselected node gets INK_MUTED', () => {
    setupStore({ selection: ['node-sel'] });
    render(<CanvasSurface />);

    const miniMap = findChild(MiniMap) as React.ReactElement<{
      nodeColor?: (n: { id: string }) => string;
    }>;
    const nodeColor = miniMap.props.nodeColor;
    expect(nodeColor).toBeDefined();
    expect(nodeColor!({ id: 'node-sel' })).toBe(INK_ACCENT);
    expect(nodeColor!({ id: 'node-other' })).toBe(INK_MUTED);
  });

  it('all nodes get INK_MUTED when nothing is selected', () => {
    setupStore({ selection: [] });
    render(<CanvasSurface />);

    const miniMap = findChild(MiniMap) as React.ReactElement<{
      nodeColor?: (n: { id: string }) => string;
    }>;
    const nodeColor = miniMap.props.nodeColor!;
    expect(nodeColor({ id: 'any-node' })).toBe(INK_MUTED);
  });
});
