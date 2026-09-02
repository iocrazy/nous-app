/**
 * `CanvasEngine` — interaction downshift (canvas fluency Wave 2, Task 5).
 *
 * Frosted node cards are the single biggest per-frame compositor cost on
 * this canvas: a `backdrop-filter: blur(18px)` re-reads everything behind
 * the card on every frame, a 56px soft shadow repaints with it, and any
 * `transition` on the card interpolates toward a target the gesture has
 * already moved past. None of that is perceivable DURING a drag or a pan —
 * so the engine turns it off for the duration of the gesture by putting
 * `mh-canvas-interacting` on <body>, and the CSS keys off that.
 *
 * Three properties are pinned here:
 *
 *   1. The class spans the gesture — added at drag/move start, gone at
 *      drag/move stop. Both gesture families, independently.
 *
 *   2. OVERLAPPING gestures compose. A node drag can begin inside a live
 *      viewport move; ending one must not clear the class while the other
 *      is still running, or the cards get their blur back mid-drag (the
 *      exact stutter this task removes).
 *
 *   3. Unmount clears it. A drag interrupted by navigation must not leave
 *      the WHOLE app blur-less — the class is on <body>, so a leak outlives
 *      the canvas.
 *
 * React Flow is stubbed to a prop-capturing shim (the repo's canvas-core
 * test pattern): the assertions are about the handlers the engine hands it.
 */

import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

import { render, cleanup, act } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';
import { vi } from 'vitest';

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

const CLASS = 'mh-canvas-interacting';
const NODE = { id: 'n1', position: { x: 0, y: 0 }, data: {} };
const VIEWPORT = { x: 0, y: 0, zoom: 1 };

type Handler = (...args: unknown[]) => void;

function renderEngine(extra: Record<string, unknown> = {}) {
  const view = render(
    <CanvasEngine
      nodes={[]}
      edges={[]}
      onNodesChange={() => {}}
      onEdgesChange={() => {}}
      {...extra}
    />,
  );
  return { view, props: capturedProps };
}

const fire = (props: Record<string, unknown>, name: string, ...args: unknown[]) =>
  act(() => {
    (props[name] as Handler | undefined)?.(...args);
  });

const interacting = () => document.body.classList.contains(CLASS);

afterEach(() => {
  cleanup();
  capturedProps = {};
  document.body.classList.remove(CLASS);
});

describe('CanvasEngine — mh-canvas-interacting spans the gesture', () => {
  it('carries the class from node-drag start to node-drag stop', () => {
    const { props } = renderEngine();
    expect(interacting()).toBe(false);
    fire(props, 'onNodeDragStart', {}, NODE);
    expect(interacting()).toBe(true);
    fire(props, 'onNodeDragStop', {}, NODE);
    expect(interacting()).toBe(false);
  });

  it('carries the class from move start to move end', () => {
    const { props } = renderEngine();
    fire(props, 'onMoveStart', {}, VIEWPORT);
    expect(interacting()).toBe(true);
    fire(props, 'onMoveEnd', {}, VIEWPORT);
    expect(interacting()).toBe(false);
  });

  it('wires move start/end even when the caller passed neither', () => {
    // Before this task both were left undefined unless a consumer asked for
    // them, so a pan on a surface that does not persist its viewport would
    // never downshift at all.
    const { props } = renderEngine();
    expect(typeof props.onMoveStart).toBe('function');
    expect(typeof props.onMoveEnd).toBe('function');
  });

  it('still forwards the consumer callbacks it composes with', () => {
    const onNodeDragStart = vi.fn();
    const onMoveStart = vi.fn();
    const onMoveEnd = vi.fn();
    const { props } = renderEngine({ onNodeDragStart, onMoveStart, onMoveEnd });
    fire(props, 'onNodeDragStart', {}, NODE);
    fire(props, 'onMoveStart', {}, VIEWPORT);
    fire(props, 'onMoveEnd', {}, VIEWPORT);
    expect(onNodeDragStart).toHaveBeenCalledTimes(1);
    expect(onMoveStart).toHaveBeenCalledWith(VIEWPORT);
    expect(onMoveEnd).toHaveBeenCalledWith(VIEWPORT);
  });
});

describe('CanvasEngine — overlapping gestures compose', () => {
  it('keeps the class while a node drag outlives the viewport move it began in', () => {
    const { props } = renderEngine();
    fire(props, 'onMoveStart', {}, VIEWPORT);
    fire(props, 'onNodeDragStart', {}, NODE);
    // The pan settles first — the drag is still live, so the cards must NOT
    // get their blur back yet.
    fire(props, 'onMoveEnd', {}, VIEWPORT);
    expect(interacting()).toBe(true);
    fire(props, 'onNodeDragStop', {}, NODE);
    expect(interacting()).toBe(false);
  });

  it('keeps the class while a viewport move outlives the node drag inside it', () => {
    const { props } = renderEngine();
    fire(props, 'onNodeDragStart', {}, NODE);
    fire(props, 'onMoveStart', {}, VIEWPORT);
    fire(props, 'onNodeDragStop', {}, NODE);
    expect(interacting()).toBe(true);
    fire(props, 'onMoveEnd', {}, VIEWPORT);
    expect(interacting()).toBe(false);
  });

  it('an unpaired move end (programmatic fitView) never clears a live drag', () => {
    // onMoveEnd also fires for PROGRAMMATIC moves with no matching start.
    const { props } = renderEngine();
    fire(props, 'onNodeDragStart', {}, NODE);
    fire(props, 'onMoveEnd', {}, VIEWPORT);
    expect(interacting()).toBe(true);
  });
});

describe('CanvasEngine — the class never outlives the canvas', () => {
  it('clears it on unmount even mid-drag', () => {
    const { view, props } = renderEngine();
    fire(props, 'onNodeDragStart', {}, NODE);
    expect(interacting()).toBe(true);
    view.unmount();
    expect(interacting()).toBe(false);
  });
});

describe('index.css — what the interacting class actually costs', () => {
  const css = readFileSync(resolve(__dirname, '../index.css'), 'utf8');

  it('drops backdrop blur, shadow and transitions on node cards', () => {
    expect(css).toMatch(/\.mh-canvas-interacting \.mh-node[^}]*backdrop-filter:\s*none/s);
    expect(css).toMatch(/\.mh-canvas-interacting \.mh-node[^}]*box-shadow:\s*none/s);
    expect(css).toMatch(/\.mh-canvas-interacting \.mh-node[^}]*transition:\s*none/s);
  });

  it('covers the group container, which is frosted too', () => {
    expect(css).toMatch(/\.mh-canvas-interacting \.mh-group-node/);
  });

  it('drops the blur on floating canvas chrome as well', () => {
    expect(css).toMatch(/\.mh-canvas-interacting \.canvas-island/);
  });
});
