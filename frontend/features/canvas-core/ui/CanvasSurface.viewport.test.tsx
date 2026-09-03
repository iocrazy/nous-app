/**
 * `CanvasSurface` — the viewport is React Flow's, not the store's
 * (canvas fluency Wave 1, Task 3).
 *
 * The property that matters is negative and therefore easy to lose: a pan
 * frame must reach NOTHING. Before this task every frame of every pan ran
 * `onMove` → `setViewportOnMove` (a React state write, which React Flow was
 * rendering from, so the transform waited on a re-render) plus a RAF that
 * bumped `revision` and reset the 500ms save debounce. The store learned the
 * viewport 60 times a second to persist it once.
 *
 * Now the surface hands React Flow a seed (`defaultViewport`) and one settle
 * handler. The frames in between are xyflow's business.
 *
 * React Flow is stubbed to a prop-capturing shim (the repo's canvas-core test
 * pattern) so the handlers the surface actually wires up can be driven.
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
import { useCanvasCoreStore } from '../store/canvasCoreStore';
import type { CanvasNode } from '../types';

/** The 2026-08-12 production row's viewport, verbatim — a value nothing in
 *  this file could have produced by accident. */
const SAVED_VIEWPORT = { x: 181.47, y: 106.68, zoom: 0.514 };

const NODES: CanvasNode[] = [
  { id: 'a', type: 'output', position: { x: 0, y: 0 }, data: {} },
];

function seed(): void {
  useCanvasCoreStore.setState({
    kind: 'smart',
    nodes: NODES.map((n) => ({ ...n })),
    connections: [],
    selection: [],
    readOnly: false,
    canvasId: '337610660408263',
    baseUpdatedAt: '2026-08-13T09:00:00+00:00',
    loadStatus: 'ready',
    viewport: { ...SAVED_VIEWPORT },
  });
}

afterEach(() => {
  cleanup();
  capturedProps = {};
  useCanvasCoreStore.getState().reset();
});

describe('CanvasSurface — uncontrolled viewport', () => {
  it('seeds React Flow with the persisted viewport instead of controlling it', () => {
    seed();
    render(<CanvasSurface />);

    expect(capturedProps.defaultViewport).toEqual(SAVED_VIEWPORT);
    expect(capturedProps.viewport).toBeUndefined();
  });

  it('gives React Flow no per-frame move channel, so a pan frame reaches nothing', () => {
    // React Flow emits `onMove` on every frame of a pan. The surface not
    // listening is the whole optimisation, and it is a structural property —
    // there is no handler to drive, so driving one would be theatre. What
    // makes this falsifiable is mutation M4: rewire a dirtying `onMove` and
    // this assertion is the one that fails.
    seed();
    render(<CanvasSurface />);

    expect(capturedProps.onMove).toBeUndefined();
    // `onMoveStart` used to be absent here too. Task 5 made the ENGINE wire
    // it unconditionally so it can toggle `mh-canvas-interacting`; it fires
    // once per gesture, not once per frame, so the per-frame property this
    // test guards is unaffected. The surface still passes no start handler
    // of its own — what it must not have is the per-frame channel above.
    expect(typeof capturedProps.onMoveStart).toBe('function');
  });

  it('persists once per settle, no matter how long the gesture was', () => {
    seed();
    render(<CanvasSurface />);
    const before = useCanvasCoreStore.getState().revision;

    (capturedProps.onMoveEnd as (e: unknown, v: unknown) => void)(null, {
      x: 40,
      y: 60,
      zoom: 1.25,
    });

    const s = useCanvasCoreStore.getState();
    // Exactly one — the old RAF path bumped once per animation frame, so a
    // two-second pan cost ~120 revisions and as many save-debounce resets.
    expect(s.revision).toBe(before + 1);
    expect(s.viewport).toEqual({ x: 40, y: 60, zoom: 1.25 });
  });

  it('a settle that did not move the viewport is not an edit', () => {
    // React Flow fires move-end for a programmatic `setViewport` too — which
    // is how the persisted viewport is restored on open and on canvas switch.
    // Treating that echo as a user edit would dirty every canvas the moment
    // it is opened and schedule a save of the value just loaded.
    seed();
    render(<CanvasSurface />);
    const before = useCanvasCoreStore.getState().revision;

    (capturedProps.onMoveEnd as (e: unknown, v: unknown) => void)(
      null,
      { ...SAVED_VIEWPORT },
    );

    const s = useCanvasCoreStore.getState();
    expect(s.revision).toBe(before);
    expect(s.saveStatus).toBe('idle');
  });
});
