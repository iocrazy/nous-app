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
  // `nodes` LAST, after the spread: this helper's whole job is to replace the
  // node list, so `extra.nodes` must not win here the way it does above.
  const rerenderWithNodes = (nodes: unknown[]) =>
    act(() => {
      view.rerender(
        <CanvasEngine
          edges={[]}
          onNodesChange={() => {}}
          onEdgesChange={() => {}}
          {...extra}
          nodes={nodes as never}
        />,
      );
    });
  return { view, props: capturedProps, rerenderWithNodes };
}

/** Release the pointer WITHOUT React Flow ever dispatching its drag-stop. */
const releasePointer = (type: 'pointerup' | 'pointercancel' = 'pointerup') =>
  act(() => {
    document.dispatchEvent(new Event(type, { bubbles: true }));
  });

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

  it('carries the class from selection-drag start to selection-drag stop', () => {
    // Dragging a MULTI-node selection is a different React Flow event pair
    // (`onSelectionDrag*`, dispatched by XYDrag when the drag has no single
    // node id). Task 5 only wired the solo-drag pair, so moving a selection
    // — the heaviest gesture on the canvas, every selected card repainting
    // its blur every frame — never downshifted at all.
    const { props } = renderEngine();
    expect(interacting()).toBe(false);
    fire(props, 'onSelectionDragStart', {}, [NODE]);
    expect(interacting()).toBe(true);
    fire(props, 'onSelectionDragStop', {}, [NODE]);
    expect(interacting()).toBe(false);
  });

  it('wires selection drag start/stop even when the caller passed neither', () => {
    // Same reasoning as the move pair above: the downshift is the engine's
    // own business, so it must not depend on a consumer having asked for
    // the callback. `onSelectionDragStop` used to be handed to React Flow
    // only when a consumer supplied one.
    const { props } = renderEngine();
    expect(typeof props.onSelectionDragStart).toBe('function');
    expect(typeof props.onSelectionDragStop).toBe('function');
  });

  it('still forwards the consumer callbacks it composes with', () => {
    const onNodeDragStart = vi.fn();
    const onMoveStart = vi.fn();
    const onMoveEnd = vi.fn();
    const onSelectionDragStop = vi.fn();
    const { props } = renderEngine({
      onNodeDragStart,
      onMoveStart,
      onMoveEnd,
      onSelectionDragStop,
    });
    fire(props, 'onNodeDragStart', {}, NODE);
    fire(props, 'onMoveStart', {}, VIEWPORT);
    fire(props, 'onMoveEnd', {}, VIEWPORT);
    fire(props, 'onSelectionDragStop', {}, [NODE]);
    expect(onNodeDragStart).toHaveBeenCalledTimes(1);
    expect(onMoveStart).toHaveBeenCalledWith(VIEWPORT);
    expect(onMoveEnd).toHaveBeenCalledWith(VIEWPORT);
    expect(onSelectionDragStop).toHaveBeenCalledWith([NODE]);
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

  it('keeps the class while a selection drag outlives the viewport move it began in', () => {
    const { props } = renderEngine();
    fire(props, 'onMoveStart', {}, VIEWPORT);
    fire(props, 'onSelectionDragStart', {}, [NODE]);
    fire(props, 'onMoveEnd', {}, VIEWPORT);
    expect(interacting()).toBe(true);
    fire(props, 'onSelectionDragStop', {}, [NODE]);
    expect(interacting()).toBe(false);
  });

  it('keeps the class while a viewport move outlives the selection drag inside it', () => {
    const { props } = renderEngine();
    fire(props, 'onSelectionDragStart', {}, [NODE]);
    fire(props, 'onMoveStart', {}, VIEWPORT);
    fire(props, 'onSelectionDragStop', {}, [NODE]);
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

describe('CanvasEngine — a drag React Flow ABORTS still ends', () => {
  // React Flow's drag `end` handler returns before dispatching
  // `onNodeDragStop` whenever `abortDrag` is set
  // (@xyflow/system/dist/esm/index.js:2262-2267). Two paths set it: a second
  // touch landing mid-drag (pinch on a touch device), and the dragged node
  // vanishing from `nodeLookup` — which the Delete/Backspace binding at
  // features/canvas-core/ui/useCanvasShortcuts.ts:243 can do from a
  // window-level listener while a drag is held.
  //
  // The start already fired, so nothing would ever clear the flag: the whole
  // app stays flat — every card without blur, shadow OR selection ring — for
  // the rest of the session. An unpaired START is the direction that pins the
  // class ON, and it is the opposite of the unpaired-end case above.

  it('clears the class when the pointer is released with no drag-stop', () => {
    const { props } = renderEngine();
    fire(props, 'onNodeDragStart', {}, NODE);
    expect(interacting()).toBe(true);
    releasePointer('pointerup');
    expect(interacting()).toBe(false);
  });

  it('clears the class when a SELECTION drag is released with no drag-stop', () => {
    // The abort branch is in XYDrag, which is shared by both drag families —
    // so a selection drag must ride the same pointer watchdog, not a flag of
    // its own that nothing would ever take down.
    const { props } = renderEngine();
    fire(props, 'onSelectionDragStart', {}, [NODE]);
    expect(interacting()).toBe(true);
    releasePointer('pointerup');
    expect(interacting()).toBe(false);
  });

  it('clears it on pointercancel too (touch drag interrupted by a second touch)', () => {
    const { props } = renderEngine();
    fire(props, 'onNodeDragStart', {}, NODE);
    releasePointer('pointercancel');
    expect(interacting()).toBe(false);
  });

  it('clears it the moment the dragged node disappears from the node list', () => {
    // Delete pressed mid-drag: do not make the user release the mouse before
    // the canvas looks right again.
    const { props, rerenderWithNodes } = renderEngine({ nodes: [NODE] });
    fire(props, 'onNodeDragStart', {}, NODE);
    expect(interacting()).toBe(true);
    rerenderWithNodes([]);
    expect(interacting()).toBe(false);
  });

  it('leaves other nodes disappearing alone while the dragged one survives', () => {
    const other = { id: 'n2', position: { x: 9, y: 9 }, data: {} };
    const { props, rerenderWithNodes } = renderEngine({ nodes: [NODE, other] });
    fire(props, 'onNodeDragStart', {}, NODE);
    rerenderWithNodes([NODE]);
    expect(interacting()).toBe(true);
  });

  it('a pointer release never cuts short a live viewport move', () => {
    // The watchdog is scoped to the DRAG flag only. With a drag live inside a
    // pan, its `pointerup` fires while the pan is still running (React Flow's
    // own onMoveEnd comes later), so a watchdog that cleared both flags would
    // give every card its blur back mid-pan — the exact stutter this task
    // removes, reintroduced through the abort fix.
    const { props } = renderEngine();
    fire(props, 'onMoveStart', {}, VIEWPORT);
    fire(props, 'onNodeDragStart', {}, NODE);
    releasePointer('pointerup');
    expect(interacting()).toBe(true);
    fire(props, 'onMoveEnd', {}, VIEWPORT);
    expect(interacting()).toBe(false);
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

  it('keeps the selection ring — only the soft shadow goes', () => {
    // `.mh-canvas-interacting .mh-node` (0-2-0) sets `box-shadow: none`,
    // which outranks `.mh-node-selected` (0-1-0) whose ENTIRE ring is a
    // box-shadow. Without an override every selected card loses its ring the
    // instant you grab it or pan — including the card under the cursor.
    expect(css).toMatch(
      /\.mh-canvas-interacting \.mh-node-selected[^}]*box-shadow:\s*0 0 0 1px var\(--canvas-strong\)/s,
    );
    // …and it must NOT drag the 56px soft shadow back in with it.
    expect(css).not.toMatch(
      /\.mh-canvas-interacting \.mh-node-selected[^}]*var\(--mh-node-shadow\)/s,
    );
  });
});
