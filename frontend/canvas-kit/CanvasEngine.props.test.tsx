/**
 * `CanvasEngine` — the pan/zoom feel contract (canvas fluency Wave 1, Task 3).
 *
 * Three properties are pinned here, all of them things a user feels rather
 * than sees in the DOM, which is exactly why they need a test each:
 *
 *   1. Trackpad two-finger scroll PANS; pinch (and modifier+wheel, which the
 *      browser reports as a pinch) ZOOMS. That is the Figma / Miro / IC
 *      convention. React Flow's default is the opposite — wheel zooms — and
 *      the only way to know which one is wired is to read the props it got.
 *
 *   2. The viewport is UNCONTROLLED. Before this task the surface fed React
 *      Flow a `viewport` prop out of the Zustand store and rewrote it on
 *      every animation frame of every pan, so each frame round-tripped
 *      through React state before the canvas moved. `defaultViewport` seeds
 *      the transform once and React Flow owns it from there; the store hears
 *      about it again only when the gesture settles (`onMoveEnd`).
 *
 *   3. The `.react-flow__viewport` layer carries no CSS transition. A 50ms
 *      `transform` transition means every pan frame is interpolated by the
 *      compositor toward a target that has already moved — the canvas lags
 *      the cursor by a frame and reads as mushy.
 *
 * React Flow itself is stubbed to a prop-capturing shim (the repo's
 * canvas-core test pattern) — the assertions are about what the engine
 * hands it, not about what xyflow then does with it.
 */

import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

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

import { CanvasEngine } from './CanvasEngine';

const SEED_VIEWPORT = { x: 181.47, y: 106.68, zoom: 0.514 };

function renderEngineAndCaptureReactFlowProps(
  extra: Record<string, unknown> = {},
): Record<string, unknown> {
  render(
    <CanvasEngine
      nodes={[]}
      edges={[]}
      onNodesChange={() => {}}
      onEdgesChange={() => {}}
      {...extra}
    />,
  );
  return capturedProps;
}

afterEach(() => {
  cleanup();
  capturedProps = {};
});

describe('CanvasEngine — trackpad pan / pinch zoom', () => {
  it('pans on trackpad scroll and zooms on pinch / modifier+wheel', () => {
    const props = renderEngineAndCaptureReactFlowProps();
    expect(props.panOnScroll).toBe(true);
    expect(props.zoomOnScroll).toBe(false);
    expect(props.zoomOnPinch).toBe(true);
  });

  it('leaves the left/middle button drag-pan semantics alone', () => {
    // Unchanged this wave — pinned so a future props edit has to be
    // deliberate rather than incidental.
    const props = renderEngineAndCaptureReactFlowProps();
    expect(props.panOnDrag).toEqual([0, 1]);
  });
});

describe('CanvasEngine — uncontrolled viewport', () => {
  it('does not control the viewport per frame', () => {
    const props = renderEngineAndCaptureReactFlowProps({
      defaultViewport: SEED_VIEWPORT,
      onMoveEnd: () => {},
    });
    expect(props.viewport).toBeUndefined();
    expect(props.defaultViewport).toEqual(SEED_VIEWPORT);
    expect(typeof props.onMoveEnd).toBe('function');
  });

  it('forwards onMoveStart / onMoveEnd to React Flow', () => {
    const onMoveStart = vi.fn();
    const onMoveEnd = vi.fn();
    const props = renderEngineAndCaptureReactFlowProps({ onMoveStart, onMoveEnd });

    (props.onMoveStart as (e: unknown, v: unknown) => void)(null, SEED_VIEWPORT);
    (props.onMoveEnd as (e: unknown, v: unknown) => void)(null, SEED_VIEWPORT);

    expect(onMoveStart).toHaveBeenCalledWith(SEED_VIEWPORT);
    expect(onMoveEnd).toHaveBeenCalledWith(SEED_VIEWPORT);
  });

  it('omits the PER-FRAME move callback when the caller passes none', () => {
    // A surface that does not care about the viewport (editor NodesView)
    // must not make React Flow call into an empty shim every frame.
    //
    // Task 5 narrowed this to `onMove` alone. `onMoveStart`/`onMoveEnd` used
    // to be omitted too, on the same reasoning — but they fire ONCE per
    // gesture, not once per frame, so there was never a per-frame cost to
    // avoid, and the engine now needs them itself to toggle
    // `mh-canvas-interacting`. See CanvasEngine.interacting.test.tsx.
    const props = renderEngineAndCaptureReactFlowProps();
    expect(props.onMove).toBeUndefined();
    expect(typeof props.onMoveStart).toBe('function');
    expect(typeof props.onMoveEnd).toBe('function');
  });
});

describe('canvas CSS — the viewport layer', () => {
  it('carries no transition', () => {
    // vitest runs with cwd = frontend/. Read a file that isn't there and you
    // get a throw, not a pass — but a file that IS there and simply doesn't
    // mention React Flow would satisfy the negative assertion vacuously, so
    // anchor on a rule that must exist before believing the one that must not.
    const css = readFileSync(resolve(process.cwd(), 'index.css'), 'utf8');
    expect(css).toMatch(/\.react-flow__edge-path/);

    expect(css).not.toMatch(/\.react-flow__viewport\s*\{[^}]*transition/s);
  });
});
