/**
 * A library drag dropped on the EMPTY PANE (Task 8).
 *
 * The engine is REAL here, and that is the point: three separate pieces have
 * to agree before a drop reaches the surface's callback — the container has to
 * be bound at all (it was bound on `onFileDrop` alone), `onContainerDrop` has
 * to recognise a MIME that carries no `File`, and the surface has to withdraw
 * the whole path in a read-only session. Capturing the prop and calling it
 * directly would assert only the third.
 *
 * The React Flow stub is the one from `CanvasSurface.dragCreate.test.tsx` —
 * jsdom has no layout, so the real component measures nothing and the engine's
 * `instanceRef` stays null (the drop point then falls back to client coords,
 * which is what the assertions below read).
 *
 * `fakeDataTransfer` is the REAL `DataTransfer` interface — a `types` list of
 * MIME strings plus `getData` by key — copied from `dropLibraryItems.test.ts`
 * rather than exported out of it. Anything looser would test a shape the
 * browser never produces.
 */

import { render, cleanup, fireEvent } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (k: string, d?: string) => d ?? k }),
}));

const dropLibraryItems = vi.fn();
vi.mock('../library/dropLibraryItems', async (importOriginal) => ({
  ...(await importOriginal<Record<string, unknown>>()),
  dropLibraryItems: (...a: unknown[]) => dropLibraryItems(...a),
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
import { writeLibraryDrag, LIBRARY_DND_MIME } from '../library/dropLibraryItems';
import type { LibraryItem } from '../library/librarySearch';
import { useCanvasCoreStore } from '../store/canvasCoreStore';
import type { CanvasKind } from '../types';

// The wire shape: string ids, as `readLibraryDrag` hands them on.
const ITEMS: LibraryItem[] = [
  { store: 'generated', id: '800000000000000001', title: 'A wide shot', thumbUrl: 'x', kind: 'image' },
];

function fakeDataTransfer(): DataTransfer {
  const store = new Map<string, string>();
  return {
    get types() {
      return [...store.keys()];
    },
    setData: (k: string, v: string) => void store.set(k, v),
    getData: (k: string) => store.get(k) ?? '',
    effectAllowed: 'all',
    dropEffect: 'none',
  } as unknown as DataTransfer;
}

function seed(kind: CanvasKind, readOnly: boolean): void {
  useCanvasCoreStore.setState({
    kind,
    nodes: [],
    connections: [],
    selection: [],
    readOnly,
  } as never);
}

/** The engine's own container — the element the drop bindings live on. */
function pane(container: HTMLElement): Element {
  const el = container.querySelector('.mh-canvas');
  if (!el) throw new Error('engine container not rendered');
  return el;
}

/** jsdom builds a plain `Event` for `drop`/`dragover`, and a plain Event has
 *  no `clientX`/`clientY` — passing them through fireEvent's init silently
 *  yields `undefined`, which is exactly the drop point the engine would then
 *  hand on. Assembling the event here keeps the coordinates real. */
function dispatchDrag(el: Element, type: 'drop' | 'dragover', dt: DataTransfer, at: { x: number; y: number }): Event {
  const evt = new Event(type, { bubbles: true, cancelable: true });
  Object.assign(evt, { dataTransfer: dt, clientX: at.x, clientY: at.y, altKey: false });
  fireEvent(el, evt);
  return evt;
}

beforeEach(() => {
  dropLibraryItems.mockReset().mockResolvedValue({
    handled: true, placed: 1, referenced: 0, mentioned: 0, failed: 0,
  });
});

afterEach(() => {
  cleanup();
  capturedProps = {};
  useCanvasCoreStore.getState().reset();
});

describe('CanvasSurface library drop', () => {
  it('routes a real payload dropped on the pane to a canvas placement at the drop point', () => {
    seed('smart', false);
    const { container } = render(<CanvasSurface />);
    const dt = fakeDataTransfer();
    writeLibraryDrag(dt, ITEMS);

    dispatchDrag(pane(container), 'drop', dt, { x: 240, y: 160 });

    expect(dropLibraryItems).toHaveBeenCalledTimes(1);
    const [items, target] = dropLibraryItems.mock.calls[0];
    expect((items as LibraryItem[]).map((i) => i.id)).toEqual(['800000000000000001']);
    expect(target).toEqual({ kind: 'canvas', position: { x: 240, y: 160 } });
  });

  it('marks the pane a drop target on dragover — without preventDefault the browser refuses the drop', () => {
    seed('smart', false);
    const { container } = render(<CanvasSurface />);
    const dt = fakeDataTransfer();
    writeLibraryDrag(dt, ITEMS);

    // jsdom never ENFORCES the rule (a drop it should refuse still fires), so
    // this asserts the call rather than the consequence.
    expect(dispatchDrag(pane(container), 'dragover', dt, { x: 10, y: 10 }).defaultPrevented).toBe(true);
  });

  it('accepts the library MIME on its OWN, not by way of the text/plain the writer also sets', () => {
    seed('smart', false);
    const { container } = render(<CanvasSurface />);
    // The case above cannot see the library term at all: `writeLibraryDrag`
    // sets `text/plain` too, and the engine's url branch already prevents the
    // default for that — so removing the library term entirely leaves it
    // green. Only a payload carrying the custom MIME ALONE isolates it.
    const dt = fakeDataTransfer();
    dt.setData(LIBRARY_DND_MIME, JSON.stringify({ items: [] }));
    expect(dt.types).toEqual([LIBRARY_DND_MIME]);

    expect(dispatchDrag(pane(container), 'dragover', dt, { x: 10, y: 10 }).defaultPrevented).toBe(true);
  });

  it('withdraws the whole path on a read-only canvas', () => {
    seed('smart', true);
    const { container } = render(<CanvasSurface />);
    const dt = fakeDataTransfer();
    writeLibraryDrag(dt, ITEMS);

    dispatchDrag(pane(container), 'drop', dt, { x: 240, y: 160 });
    expect(dropLibraryItems).not.toHaveBeenCalled();
    // The engine binds its container handlers off the same three props, so a
    // viewer's pane is not a drop target at all rather than a silent one.
    expect(dispatchDrag(pane(container), 'dragover', dt, { x: 10, y: 10 }).defaultPrevented).toBe(false);
  });

  it('leaves a foreign drag alone, so a file drag still reaches the file path', () => {
    seed('smart', false);
    const { container } = render(<CanvasSurface />);
    const dt = fakeDataTransfer();
    dt.setData('text/plain', 'hello');
    expect(dt.types).not.toContain(LIBRARY_DND_MIME);

    dispatchDrag(pane(container), 'drop', dt, { x: 5, y: 5 });
    expect(dropLibraryItems).not.toHaveBeenCalled();
  });

  it('rendered the surface at all — a stub that never mounted would pass every case above', () => {
    seed('smart', false);
    render(<CanvasSurface />);
    expect(typeof capturedProps.onNodesChange).toBe('function');
  });
});
