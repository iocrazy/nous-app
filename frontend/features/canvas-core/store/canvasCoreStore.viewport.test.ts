/**
 * The store's viewport contract after canvas fluency Wave 1, Task 3.
 *
 * The old design had TWO viewport writers for one gesture: `setViewportOnMove`
 * wrote the transform on every animation frame (so React Flow, which was
 * running CONTROLLED, could render it), and a RAF-scheduled
 * `flushViewportDirty` bumped `revision` at most once per frame. Both existed
 * only to make a per-frame React state write survivable.
 *
 * React Flow now owns the transform (uncontrolled, seeded once via
 * `defaultViewport`), so no pan frame reaches the store at all. What is left
 * is one function for one event: the gesture settled, here is where it landed,
 * persist it. That is `setViewportSettled`.
 *
 * `scheduleSave` is observed through its only externally visible effect — a
 * call to the injected `saveImpl` after the debounce elapses. Spying on the
 * private timer would pass even if the timer never reached the network.
 *
 * Fix round 1 adds `viewportEpoch`: the store cannot reach React Flow's
 * instance, so a viewport it writes ITSELF (load, switch, realtime rebase,
 * conflict resolve) has to announce that the canvas needs moving. A viewport
 * that arrived FROM the canvas (`setViewportSettled`) must not — it is already
 * on screen, and re-applying it would fight the user mid-pan.
 */

import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { Canvas, CanvasSaveResult, CanvasUpdatePayload } from '../types';
import { createCanvasCoreStore } from './canvasCoreStore';

const baseCanvas: Canvas = {
  id: '4242',
  project_id: '111',
  name: 'Untitled',
  kind: 'smart',
  viewport_json: { x: 0, y: 0, zoom: 1 },
  nodes_json: [],
  connections_json: [],
  node_ops_json: [],
  connection_ops_json: [],
  base_updated_at: '2026-06-10T12:00:00+00:00',
  created_at: '2026-06-10T12:00:00+00:00',
  updated_at: '2026-06-10T12:00:00+00:00',
  created_by: null,
};

function makeStubs() {
  const loadImpl = vi.fn(async (): Promise<Canvas> => ({ ...baseCanvas }));
  const calls: CanvasUpdatePayload[] = [];
  const saveImpl = vi.fn(
    async (_id: string, payload: CanvasUpdatePayload): Promise<CanvasSaveResult> => {
      calls.push(payload);
      return {
        ok: true,
        canvas: { ...baseCanvas, base_updated_at: new Date().toISOString() },
      };
    },
  );
  return { loadImpl, saveImpl, calls };
}

beforeEach(() => {
  vi.useFakeTimers();
});

afterEach(() => {
  vi.useRealTimers();
});

describe('canvasCoreStore — settled viewport', () => {
  it('a settled viewport bumps revision exactly once and schedules exactly one save', async () => {
    const stubs = makeStubs();
    const useStore = createCanvasCoreStore({ ...stubs, debounceMs: 100 });
    await useStore.getState().loadCanvas('4242');

    const before = useStore.getState().revision;
    useStore.getState().setViewportSettled({ x: 1, y: 2, zoom: 1.5 });

    expect(useStore.getState().revision).toBe(before + 1);
    expect(useStore.getState().viewport).toEqual({ x: 1, y: 2, zoom: 1.5 });

    await vi.advanceTimersByTimeAsync(100);
    expect(stubs.saveImpl).toHaveBeenCalledTimes(1);
    expect(stubs.calls[0].viewport_json).toMatchObject({ x: 1, y: 2, zoom: 1.5 });
  });

  it('has no per-frame viewport writer any more', () => {
    const stubs = makeStubs();
    const useStore = createCanvasCoreStore({ ...stubs, debounceMs: 100 });
    const state = useStore.getState() as unknown as Record<string, unknown>;

    expect(state.setViewportOnMove).toBeUndefined();
    expect(state.flushViewportDirty).toBeUndefined();
  });

  it('clamps an out-of-range zoom on the way in', async () => {
    const stubs = makeStubs();
    const useStore = createCanvasCoreStore({ ...stubs, debounceMs: 9999 });
    await useStore.getState().loadCanvas('4242');

    useStore.getState().setViewportSettled({ x: 0, y: 0, zoom: 9000 });
    expect(useStore.getState().viewport.zoom).toBe(8);
  });

  it('does not create an undo entry — panning is not a document edit', async () => {
    const stubs = makeStubs();
    const useStore = createCanvasCoreStore({
      ...stubs,
      debounceMs: 9999,
      historyDebounceMs: 100,
    });
    await useStore.getState().loadCanvas('4242');

    useStore.getState().setViewportSettled({ x: 111, y: 222, zoom: 1.5 });
    await vi.advanceTimersByTimeAsync(200);

    expect(useStore.getState().historyPast).toHaveLength(0);
    expect(useStore.getState().canUndo()).toBe(false);
  });
});

describe('canvasCoreStore — viewportEpoch (store-side writes announce themselves)', () => {
  it('a load bumps the epoch', async () => {
    const stubs = makeStubs();
    const useStore = createCanvasCoreStore({ ...stubs, debounceMs: 9999 });
    const before = useStore.getState().viewportEpoch;

    await useStore.getState().loadCanvas('4242');

    expect(useStore.getState().viewportEpoch).toBe(before + 1);
  });

  it('a realtime rebase bumps the epoch', async () => {
    const stubs = makeStubs();
    const useStore = createCanvasCoreStore({ ...stubs, debounceMs: 9999 });
    await useStore.getState().loadCanvas('4242');
    const before = useStore.getState().viewportEpoch;

    useStore.getState().applyRemoteUpdate({
      ...baseCanvas,
      viewport_json: { x: 7, y: 8, zoom: 2 },
      base_updated_at: '2026-06-10T13:00:00+00:00',
    });

    expect(useStore.getState().viewport).toEqual({ x: 7, y: 8, zoom: 2 });
    expect(useStore.getState().viewportEpoch).toBe(before + 1);
  });

  it('a conflict resolve bumps the epoch', async () => {
    const stubs = makeStubs();
    const useStore = createCanvasCoreStore({ ...stubs, debounceMs: 9999 });
    await useStore.getState().loadCanvas('4242');
    useStore.setState({
      conflict: {
        ...baseCanvas,
        viewport_json: { x: -1, y: -2, zoom: 3 },
        base_updated_at: '2026-06-10T14:00:00+00:00',
      },
    });
    const before = useStore.getState().viewportEpoch;

    useStore.getState().resolveConflictWithServer();

    expect(useStore.getState().viewport).toEqual({ x: -1, y: -2, zoom: 3 });
    expect(useStore.getState().viewportEpoch).toBe(before + 1);
  });

  it('a settled viewport does NOT bump the epoch', async () => {
    const stubs = makeStubs();
    const useStore = createCanvasCoreStore({ ...stubs, debounceMs: 9999 });
    await useStore.getState().loadCanvas('4242');
    const before = useStore.getState().viewportEpoch;

    useStore.getState().setViewportSettled({ x: 1, y: 2, zoom: 1.5 });
    useStore.getState().setViewportSettled({ x: 3, y: 4, zoom: 1.6 });

    expect(useStore.getState().viewportEpoch).toBe(before);
  });

  it('the three programmatic writers are gone, not kept "just in case"', () => {
    // `setViewport` / `panViewportBy` / `zoomViewportAround` never had a
    // production caller. Under an uncontrolled surface they were three more
    // sites obliged to remember the epoch, with end-to-end behaviour nothing
    // had ever exercised. A caller that wants to MOVE the canvas drives React
    // Flow's instance directly (`zoomPreview`, `CanvasPage`'s overview
    // fly-in), which is what the deleted names only pretended to do.
    const stubs = makeStubs();
    const useStore = createCanvasCoreStore({ ...stubs, debounceMs: 100 });
    const state = useStore.getState() as unknown as Record<string, unknown>;

    expect(state.setViewport).toBeUndefined();
    expect(state.panViewportBy).toBeUndefined();
    expect(state.zoomViewportAround).toBeUndefined();
  });

  it('reset() bumps the epoch with the viewport it writes', async () => {
    // Harmless in practice — `reset()` also nulls `canvasId`, so the reader
    // effect is skipped. Kept conforming anyway: one invariant with no
    // exceptions is cheaper to hold than one with a footnote, and this was
    // the live counter-example the source scan below would otherwise have to
    // allow-list.
    const stubs = makeStubs();
    const useStore = createCanvasCoreStore({ ...stubs, debounceMs: 9999 });
    await useStore.getState().loadCanvas('4242');
    const before = useStore.getState().viewportEpoch;

    useStore.getState().reset();

    expect(useStore.getState().viewportEpoch).toBe(before + 1);
  });
});

/**
 * `viewportEpoch` is a CONVENTION: nothing in the type system links a
 * `viewport:` write to the bump that makes it visible, and an unbumped write
 * fails silently — the store and the transform on screen simply diverge, and
 * the two placement readers map screen coordinates through a viewport nobody
 * is looking at. A convention with no enforcement erodes, so this scans the
 * source instead of any one behaviour.
 *
 * The rule: every `set({...})` in the store that assigns `viewport:` assigns
 * `viewportEpoch:` in the SAME object literal. One documented exception —
 * `setViewportSettled`, whose value came FROM React Flow and is therefore
 * already on screen; pushing it back would fight the user mid-gesture.
 */
describe('viewportEpoch — the invariant is pinned in the source, not just in behaviour', () => {
  const SOURCE = readFileSync(resolve(__dirname, './canvasCoreStore.ts'), 'utf8');

  /** Every `set({ … })` call in the store, as its literal body text. */
  function setLiterals(source: string): string[] {
    const out: string[] = [];
    const marker = 'set({';
    let at = source.indexOf(marker);
    while (at !== -1) {
      let depth = 0;
      let i = at + marker.length - 1;
      for (; i < source.length; i += 1) {
        if (source[i] === '{') depth += 1;
        else if (source[i] === '}') {
          depth -= 1;
          if (depth === 0) break;
        }
      }
      out.push(source.slice(at, i + 1));
      at = source.indexOf(marker, i + 1);
    }
    return out;
  }

  it('finds the set() calls at all — a scan that matches nothing reports clean', () => {
    // The scan's own failure mode is silence, so pin that it sees the file.
    const literals = setLiterals(SOURCE);
    expect(literals.length).toBeGreaterThan(5);
    expect(literals.some((l) => l.includes('viewport:'))).toBe(true);
  });

  /** The only way to write `viewport:` without bumping: say so in the
   *  literal, in these words. Nobody types this by accident. */
  const OPT_OUT = 'viewportEpoch: intentionally NOT bumped';

  it('every set() that writes viewport also names viewportEpoch in the same literal', () => {
    const offenders = setLiterals(SOURCE).filter(
      (literal) => /\bviewport:/.test(literal) && !literal.includes('viewportEpoch'),
    );
    expect(offenders).toEqual([]);
  });

  it('exactly one write opts out, and it is the one that came from React Flow', () => {
    // An escape hatch nobody counts is an escape hatch everybody uses.
    const optedOut = setLiterals(SOURCE).filter((literal) => literal.includes(OPT_OUT));
    expect(optedOut).toHaveLength(1);
    expect(optedOut[0]).toContain('clampZoom(viewport.zoom)');
  });
});
